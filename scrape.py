from uvscada.util import str2hex

import json
import binascii
import subprocess

from bpmicro.cmd import led_i2s
from uvscada.util import hexdump

fw_mods = {}
if 0:
    import bpmicro.i87c51.read_fw
    fw_mods['bpmicro.i87c51.read_fw'] = bpmicro.i87c51.read_fw.p_p2n
    import bpmicro.i87c51.write_fw
    fw_mods['bpmicro.i87c51.write_fw'] = bpmicro.i87c51.write_fw.p_p2n
if 1:
    import bpmicro.pic16f84.read_fw
    fw_mods['bpmicro.pic16f84.read_fw'] = bpmicro.pic16f84.read_fw.p_p2n
    import bpmicro.pic16f84.write_fw
    fw_mods['bpmicro.pic16f84.write_fw'] = bpmicro.pic16f84.write_fw.p_p2n

pi = None
ps = None

prefix = ' ' * 8
indent = ''
def line(s):
    print '%s%s' % (indent, s)
def indentP():
    global indent
    indent += '    '
def indentN():
    global indent
    indent = indent[4:]

dumb = False

# args.big_thresh
big_pkt = {}
def fmt_terse(data, pktn=None):
    for modname, packets in fw_mods.iteritems():
        if data in packets:
            return '%s.%s' % (modname, packets[data])
    '''
    if pktn and data in big_pkt:
        return 'my_fw.%s' % big_pkt[data]
    '''

    if args.big_thresh and pktn and len(data) > args.big_thresh:
        big_pkt[data] = 'p%d' % pktn
        return 'my_fw.%s' % big_pkt[data]

    return dump_packet(data)

def dump_packet(data):
    ret = str2hex(data, prefix=prefix)
    if len(data) > 16:
        ret += '\n%s' % prefix
    return ret

def pkt_strip(p):
    pprefix = ord(p[0])
    '''
    if pprefix != 0x08:
        #raise Exception("Bad prefix")
        line('# WARNING: unexpected prefix')
    '''
    size = (ord(p[-1]) << 8) | ord(p[-2])
    # Exact match
    if size == len(p) - 3:
        return (p[1:-2], False, pprefix)
    # Extra data
    # So far this is always 0 (should verify?)
    elif size < len(p) - 3:
        # TODO: verify 0 padding
        return (p[1:1 + size], True, pprefix)
    # Not supposed to happen
    else:
        print fmt_terse(p)
        print size
        raise Exception("Bad size")

class CmpFail(Exception):
    pass

def cmp_buff(exp, act):
    if len(exp) != len(act):
        raise CmpFail("Exp: %d, act: %d" % (len(exp), len(act)))

def cmp_mask(exp, mask, act):
    if len(exp) != len(act):
        hexdump(exp, indent='  ', label='expected')
        hexdump(act, indent='  ', label='actual')
        raise CmpFail("Exp: %d, act: %d" % (len(exp), len(act)))
    if len(exp) != len(mask):
        hexdump(exp, indent='  ', label='expected')
        hexdump(act, indent='  ', label='mask')
        raise CmpFail("Exp: %d, mask: %d" % (len(exp), len(mask)))
    for expc, actc in zip(exp, act):
        if mask == '\xFF' and expc != actc:
            hexdump(exp, indent='  ', label='expected')
            hexdump(act, indent='  ', label='actual')
            raise CmpFail("Exp: 0x%02X, act: 0x%02X" % (ord(exp), ord(actc)))

def peekp():
    return nextp()[1]

def nextp():
    ppi = pi + 1
    while True:
        if ppi >= len(ps):
            raise Exception("Out of packets")
        p = ps[ppi]
        if p['type'] != 'comment':
            return ppi, p
        ppi = ppi + 1

def bulk2(p_w, p_rs):
    cmd = binascii.unhexlify(p_w['data'])

    reply_all = ''
    for p_r in p_rs:
        reply_full = binascii.unhexlify(p_r['data'])
        reply, _truncate, pprefix = pkt_strip(reply_full)
        reply_all += reply
        if pprefix != 0x08:
            pprefix_str = ', prefix=0x%02X' % pprefix
            raise Exception(pprefix_str)

    pack_str = 'packet W: %s/%s, R %d to %s/%s' % (
            p_w['packn'][0], p_w['packn'][1],
            len(p_rs),
            p_r['packn'][0], p_r['packn'][1])
    line('buff = cmd.bulk2b(dev, %s)' % (fmt_terse(cmd, p_w['packn'][0]),))
    #line('# Discarded %d / %d bytes => %d bytes' % (len(reply_full) - len(reply), len(reply_full), len(reply)))
    line('validate_read(%s, buff, "%s")' % (fmt_terse(reply_all, p_r['packn'][0]), pack_str))

def peek_bulk2(p):
    global pi

    p_w = p
    pi, p_r = nextp()

    cmd = binascii.unhexlify(p_w['data'])
    reply_full = binascii.unhexlify(p_r['data'])
    reply, _truncate, pprefix = pkt_strip(reply_full)
    if pprefix != 0x08:
        pprefix_str = ', prefix=0x%02X' % pprefix
        raise Exception(pprefix_str)

    if cmd == "\x01":
        line('cmd.cmd_01(dev)')
    elif cmd == "\x02":
        line('cmd.cmd_02(dev, %s)' % fmt_terse(reply))
    elif cmd == "\x03":
        line('cmd.gpio_readi(dev)')
    elif 0 and cmd[0] == "\x08":
        cmp_mask(
                "\x08\x01\x57\x00\x00",
                "\xFF\xFF\xFF\x00\xFF",
                cmd)
        try:
            cmp_buff("\x00\x00", reply)
        except CmpFail:
            line('# Bad reply for cmd_08()')
            bulk2(p_w, [p_r])
        else:
            line('cmd.cmd_08(dev, %s)' % (fmt_terse(cmd[3])))
    elif cmd[0] == "\x0C":
        if len(cmd) != 3 or cmd[2] != "\x30":
            raise Exception("Unexpected")
        #line('led_mask(dev, 0x%02X)' % ord(cmd[1]))
        line('cmd.led_mask(dev, "%s")' % led_i2s[ord(cmd[1])])
    elif cmd == "\x0E\x00":
        line('cmd.sn_read(dev)')
    elif cmd == "\x0E\x02":
        line('cmd.sm_read(dev)')
    elif cmd == "\x10\x80\x02":
        cmp_buff("\x80\x00\x00\x00\x09\x00", reply)
        line('cmd.cmd_10(dev)')
    elif cmd[0] == "\x22":
        if cmd == "\x22\x02\x10\x00\x13\x00\x06":
            line('cmd.sm_info10(dev)')
        elif cmd == "\x22\x02\x10\x00\x1F\x00\x06":
            line('cmd.sm_insert(dev)')
        elif cmd == "\x22\x02\x22\x00\x23\x00\x06":
            line('cmd.sm_info22(dev)')
        elif cmd == "\x22\x02\x24\x00\x25\x00\x06":
            line('cmd.sm_info24(dev)')
        else:
            #raise Exception("Unexpected read")
            line('# Unexpected SM read')
            bulk2(p_w, [p_r])
    elif cmd == "\x45\x01\x00\x00\x31\x00\x06":
        cmp_buff( \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF" \
                "\xFF\xFF\xFF\xFF",
                reply)
        line('cmd.cmd_45(dev)')
    elif cmd == "\x49":
        cmp_buff("\x0F\x00", reply)
        line('cmd.cmd_49(dev)')
    elif cmd == "\x4A\x03\x00\x00\x00":
        cmp_buff("\x03\x00", reply)
        line('cmd.cmd_4A(dev)')
    # Observed several times as compound command
    # Only handle simple case
    elif cmd[0] == "\x57" and len(cmd) == 3:
        cmp_mask(
                "\x57\x00\x00",
                "\xFF\x00\xFF",
                cmd)
        line('cmd.cmd_57s(dev, %s, %s)' % (fmt_terse(cmd[1]), fmt_terse(reply)))
    # Unknown response
    # Do generic bulk read
    else:
        p_rs = [p_r]
        while peekp()['type'] == 'bulkRead':
            pi, p_r = nextp()
            p_rs.append(p_r)
        bulk2(p_w, p_rs)

def bulk86_next_read(p):
    if p['type'] != 'bulkRead':
        raise Exception("Unexpected type")
    if p['endp'] != 0x86:
        raise Exception("Unexpected endpoint")
    reply_full = binascii.unhexlify(p['data'])
    reply, _truncate, pprefix = pkt_strip(reply_full)
    if pprefix != 0x08:
        pprefix_str = ', prefix=0x%02X' % pprefix
        raise Exception(pprefix_str)
    #line('# Discarded %d / %d bytes => %d bytes' % (len(reply_full) - len(reply), len(reply_full), len(reply)))
    pack_str = 'packet %s/%s' % (
             p['packn'][0], p['packn'][1])
    line('_prefix, buff, _size = cmd.bulk86_next_read(dev)')
    line('validate_read(%s, buff, "%s")' % (fmt_terse(reply, p['packn'][0]), pack_str))

def bulk_write(p):
    global pi

    '''
    bulkWrite(0x02, "\x01")
    '''
    # Not all 0x02 have readback
    # bulkWrite(0x%02X
    if p['endp'] != 0x02:
        cmd = binascii.unhexlify(p['data'])
        line('bulkWrite(0x%02X, %s)' % (p['endp'], fmt_terse(cmd, p['packn'][0])))
    # Write followed by response read?
    # bulk2(
    elif not dumb and peekp()['type'] == 'bulkRead':
        peek_bulk2(p)
    # Write without following readback
    else:
        cmd = binascii.unhexlify(p['data'])
        if dumb:
            line('bulkWrite(0x02, %s)' % (fmt_terse(cmd, p['packn'][0])))
            # peked not actually fetched
            #bulk86_next_read(p)
        elif cmd == "\x09\x10\x57\x81\x00":
            line("cmd.cmd_09(dev)")
        elif cmd[0] == '\x0C' and len(cmd) == 2:
            line('cmd.led_mask(dev, 0x%02X)' % ord(cmd[1]))
        elif cmd == "\x20\x01\x00":
            line('cmd.cmd_20(dev)')
        elif cmd == \
                "\x3B\x0C\x22\x00\xC0\x40\x00\x3B\x0E\x22\x00\xC0\x00\x00\x3B\x1A" \
                "\x22\x00\xC0\x18\x00":
            line('cmd.cmd_3B(dev)')
        elif cmd == "\x41\x00\x00":
            line('cmd.cmd_41(dev)')
        elif cmd == "\x43\x19\x10\x00\x00":
            line('cmd.cmd_43(dev)')
        elif cmd == "\x4C\x00\x02":
            line('cmd.cmd_4C(dev)')
        elif cmd[0] == "\x57" and len(cmd) == 7:
            c57a = cmd[0:3]
            cmp_mask(
                    "\x57\x00\x00" ,
                    "\xFF\x00\xFF" ,
                    c57a)

            c50a = cmd[3:]
            cmp_mask(
                    "\x50\x00\x00\x00" ,
                    "\xFF\x00\xFF\xFF" ,
                    c50a)
            
            line('cmd.cmd_57_50(dev, %s, %s)' % (fmt_terse(c57a[1]), fmt_terse(c50a[1])))
        elif cmd[0] == "\x50":
            # ex: "\x50\x9F\x09\x00\x00"
            cmp_mask(
                    "\x50\x00\x00\x00\x00",
                    "\xFF\x00\x00\xFF\xFF",
                    cmd)
            line('cmd.cmd_50(dev, %s)' % (fmt_terse(cmd[1:3])))
        else:
            line('bulkWrite(0x02, %s)' % (fmt_terse(cmd, p['packn'][0])))

def dump(fin):
    #global j
    global pi
    global ps

    j = json.load(open(fin))
    pi = 0
    ps = j['data']

    def eat_packet(type=None, req=None, val=None, ind=None, len=None):
        p = ps[pi + 1]

        if type and type != p['type']:
            raise Exception()
        if req and type != p['req']:
            raise Exception()
        if val and type != p['val']:
            raise Exception()
        if ind and type != p['ind']:
            raise Exception()
        if len and len != p['len']:
            raise Exception()
            
        return pi + 1
    
    line('# Generated from scrape.py')
    line('from bpmicro.cmd import bulk2, bulk86')
    line('from bpmicro import cmd')
    line('import bpmicro.pic16f84.write_fw')
    line('import bpmicro.pic16f84.read_fw')
    line('import usb1')
    line('from bpmicro.usb import usb_wraps')
    line('from bpmicro.usb import validate_read')
    for module in fw_mods:
        line('import %s' % module)
    line('')

    # remove all comments to make processing easier
    # we'll add our own anyway
    # ps = filter(lambda p: p['type'] != 'comment', ps)
    
    line('def replay(dev):')
    indentP()
    line("bulkRead, bulkWrite, controlRead, controlWrite = usb_wraps(dev)")
    line('')
    
    if 0:
        line("# Generated from packet 61/62")
        line("# ...")
        line("# Generated from packet 71/72")
        line("load_fx2(dev)")
        line()

    
    while pi < len(ps):
        p = ps[pi]
        if p['type'] == 'comment':
            line('# %s' % p['v'])
            pass
        elif p['type'] == 'controlRead':
            if not dumb and (p['req'], p['val'], p['ind'], p['len']) == (0xC0, 0xB0, 0x0000, 0x0000):
                pi = eat_packet('bulkRead')
                line('cmd.readB0(dev)')
            else:
                '''
                # Generated from packet 6/7
                # None (0xB0)
                buff = controlRead(0xC0, 0xB0, 0x0000, 0x0000, 4096)
                # NOTE:: req max 4096 but got 3
                validate_read("\x00\x00\x00", buff, "packet 6/7")
                '''
                line('buff = controlRead(0x%02X, 0x%02X, 0x%04X, 0x%04X, %d)' % (
                        p['reqt'], p['req'], p['val'], p['ind'], p['len']))
                data = binascii.unhexlify(p['data'])
                line('# Req: %d, got: %d' % (p['len'], len(data)))
                line('validate_read(%s, buff, "packet %s/%s")' % (
                        fmt_terse(data, p['packn'][0]), p['packn'][0], p['packn'][1]))
        elif p['type'] == 'controlWrite':
            '''
            controlWrite(0x40, 0xB2, 0x0000, 0x0000, "")
            '''
            data = binascii.unhexlify(p['data'])
            line('buff = controlWrite(0x%02X, 0x%02X, 0x%04X, 0x%04X, %s)' % (
                    p['reqt'], p['req'], p['val'], p['ind'], str2hex(data, prefix=prefix)))
        elif p['type'] == 'bulkRead':
            bulk86_next_read(p)
        elif p['type'] == 'bulkWrite':
            bulk_write(p)
        else:
            raise Exception("Unknown type: %s" % p['type'])
        pi += 1

    indentN()

    print '''
def open_dev(usbcontext=None):
    if usbcontext is None:
        usbcontext = usb1.USBContext()
    
    print 'Scanning for devices...'
    for udev in usbcontext.getDeviceList(skip_on_error=True):
        vid = udev.getVendorID()
        pid = udev.getProductID()
        if (vid, pid) == (0x14b9, 0x0001):
            print
            print
            print 'Found device'
            print 'Bus %03i Device %03i: ID %04x:%04x' % (
                udev.getBusNumber(),
                udev.getDeviceAddress(),
                vid,
                pid)
            return udev.open()
    raise Exception("Failed to find a device")

if __name__ == "__main__":
    import argparse 
    
    parser = argparse.ArgumentParser(description='Replay captured USB packets')
    args = parser.parse_args()

    usbcontext = usb1.USBContext()
    dev = open_dev(usbcontext)
    dev.claimInterface(0)
    dev.resetDevice()
    replay(dev)
'''

    print
    print
    print
    print '# my_fw.py'
    for pkt, name in big_pkt.iteritems():
        print '%s = \\%s' % (name, dump_packet(pkt))


if __name__ == "__main__":
    import argparse 
    
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--dumb', action='store_true')
    parser.add_argument('--big-thresh', type=int, default=255)
    parser.add_argument('--usbrply', default='')
    parser.add_argument('fin')
    args = parser.parse_args()

    if args.fin.find('.cap') >= 0:
        fin = '/tmp/scrape.json'
        #print 'Generating json'
        cmd = 'usbrply --packet-numbers --no-setup --comment --fx2 %s -j %s >%s' % (args.usbrply, args.fin, fin)
        #print cmd
        subprocess.check_call(cmd, shell=True)
    else:
        fin = args.fin

    dumb=args.dumb
    dump(fin)

