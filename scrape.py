from bpmicro.util import str2hex

import json
import binascii
import subprocess
import os
import sys

from bpmicro.cmd import led_i2s
from bpmicro.util import hexdump
from bpmicro.util import add_bool_arg
from bpmicro import fw

fout = sys.stdout
args = None

prefix = ' ' * 8
indent = ''
line_buff = []


def lines_clear():
    del line_buff[:]


def lines_commit():
    for line in line_buff:
        fout.write(line + '\n')
    del line_buff[:]


def line(s):
    line_buff.append('%s%s' % (indent, s))


def inc_indent():
    global indent
    indent += '    '


def dec_indent():
    global indent
    indent = indent[4:]


dumb = False
omit_ro = True


def emit_ro():
    '''Return true if keeping ro. Otherwise clear line buffer and return false'''
    if omit_ro:
        lines_clear()
        return False
    else:
        return True


hash_orig = set(fw.hash2bin.keys())
hash_used = set()


def fmt_terse(data, pktn=None):
    data = str(data)
    h = fw.fwhash(data)

    if h in fw.hash2bin:
        assert data == fw.hash2bin[h]
        hash_used.add(h)
        return 'fw.hash2bin["%s"]' % h

    if args and args.big_thresh and pktn and len(data) >= args.big_thresh:
        fw.hash2bin[h] = data
        hash_used.add(h)
        return 'fw.hash2bin["%s"]' % h

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
        print((fmt_terse(p)))
        print(size)
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


class OutOfPackets(Exception):
    pass


class Scraper(object):
    def __init__(self):
        # Packets
        self.ps = None
        # Packets index
        self.pi = None

    def nextp(self):
        ppi = self.pi + 1
        while True:
            if ppi >= len(self.ps):
                raise OutOfPackets("Out of packets, started packet %d, at %d" %
                                   (self.pi, ppi))
            p = self.ps[ppi]
            if p['type'] != 'comment':
                return ppi, p
            ppi = ppi + 1

    def peekp(self):
        return self.nextp()[1]

    def eat_packet(self, type=None, req=None, val=None, ind=None, len=None):
        p = self.ps[self.pi + 1]

        if type and type != p['type']:
            raise Exception()
        if req and type != p['bRequest']:
            raise Exception()
        if val and type != p['wValue']:
            raise Exception()
        if ind and type != p['wIndex']:
            raise Exception()
        if len and len != p['wLength']:
            raise Exception()

        return self.pi + 1

    def check_bulk2(self, cmd):
        # Sample
        # fw_in = bulk2(dev, "\x08\x00\x57\x8F\x00", 4096)
        return True

    def bulk2(self, p_w, p_rs):
        cmd = binascii.unhexlify(p_w['data'])
        reply_all = self.bulk2_combine_packets(p_rs)

        pack_str = 'packet W: %s/%s, R %d to %s/%s' % (
            p_w['packn'][0], p_w['packn'][1], len(p_rs), p_rs[-1]['packn'][0],
            p_rs[-1]['packn'][1])
        line('buff = cmd.bulk2b(dev, %s)' %
             (fmt_terse(cmd, p_w['packn'][0]), ))

        if self.check_bulk2(cmd):
            #line('# Discarded %d / %d bytes => %d bytes' % (len(reply_full) - len(reply), len(reply_full), len(reply)))
            line('validate_read(%s, buff, "%s")' %
                 (fmt_terse(reply_all, p_rs[-1]['packn'][0]), pack_str))

        startup_end_cmd = \
            "\x1D\x10\x01\x09\x00\x00\x00\x15\x60\x00\x00\x00\x00\x00\x00\x00" \
            "\x00\x00\x00\x00\x00\x00\x1C\x30\x00\x00\x00\x00\x00\x00\x00\x48" \
            "\x00\x12\xAA"
        if cmd == startup_end_cmd:
            line('')
            line('')
            line('')
            line('# END OF STARTUP')
            line('')
            line('')
            line('')

    def bulk2_next_prs(self, p_r=None):
        p_rs = []
        if p_r:
            p_rs.append(p_r)
        while True:
            try:
                if self.peekp()['type'] != 'bulkRead':
                    break
            except OutOfPackets:
                break
            self.pi, p_r = self.nextp()
            p_rs.append(p_r)
        return p_rs

    def bulk2_combine_packets(self, p_rs):
        reply_all = ''
        for p_r in p_rs:
            reply_full = binascii.unhexlify(p_r['data'])
            reply, _truncate, pprefix = pkt_strip(reply_full)
            reply_all += reply
            if pprefix != 0x08:
                pprefix_str = ', prefix=0x%02X' % pprefix
                raise Exception(pprefix_str)
        return reply_all

    def bulk2_get_reply(self, p_r=None):
        '''
        Read all following bulk2 packets and aggregate response
        Optionally pass in an already fetched packet (p_r)
        '''
        p_rs = self.bulk2_next_prs(p_r)
        return p_rs, self.bulk2_combine_packets(p_rs)

    def peek_bulk2(self, p):
        '''bulk2 command resulting in read(s)'''

        p_w = p
        #pi, p_r = nextp()
        p_rs, reply = self.bulk2_get_reply()
        # Should have at least one reply
        prl = p_rs[-1]

        cmd = binascii.unhexlify(p_w['data'])
        '''
        reply_full = binascii.unhexlify(p_r['data'])
        reply, _truncate, pprefix = pkt_strip(reply_full)
        if pprefix != 0x08:
            pprefix_str = ', prefix=0x%02X' % pprefix
            raise Exception(pprefix_str)
        '''

        line('# bulk2 aggregate: packet W: %s/%s, %d to R %s/%s' %
             (p_w['packn'][0], p_w['packn'][1], len(p_rs), prl['packn'][0],
              prl['packn'][1]))

        if cmd == "\x01":
            if emit_ro():
                line('cmd.cmd_01(dev)')
                line('buff = %s' % fmt_terse(reply))
        elif cmd == "\x02":
            line('cmd.cmd_02(dev, %s)' % fmt_terse(reply))
        elif cmd == "\x03":
            if emit_ro():
                line('cmd.gpio_readi(dev)')
        elif 0 and cmd[0] == "\x08":
            '''
            cmp_mask(
                    "\x08\x01\x57\x00\x00",
                    "\xFF\xFF\xFF\x00\xFF",
                    cmd)
            try:
                cmp_buff("\x00\x00", reply)
            except CmpFail:
                line('# Unexpected reply for cmd_08(), falling back to low level command')
                bulk2(p_w, p_rs)
            else:
                line('cmd.cmd_08(dev, %s)' % (fmt_terse(cmd[3])))
            '''
        elif cmd[0] == "\x0C":
            if len(cmd) != 3 or cmd[2] != "\x30":
                raise Exception("Unexpected")
            #line('led_mask(dev, 0x%02X)' % ord(cmd[1]))
            line('cmd.led_mask(dev, "%s")' % led_i2s[ord(cmd[1])])
        elif cmd == "\x0E\x00":
            if emit_ro():
                line('cmd.sn_read(dev)')
        elif cmd == "\x0E\x02":
            if emit_ro():
                line('cmd.sm_info3(dev)')
        elif cmd == "\x10\x80\x02":
            cmp_buff("\x80\x00\x00\x00\x09\x00", reply)
            line('cmd.cmd_10(dev)')
        # XXX: investigate
        # is likely offset + number to read
        elif cmd[0] == "\x22":
            if emit_ro():
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
                    line(
                        '# Unexpected (SM?) read, falling back to low level command'
                    )
                    self.bulk2(p_w, p_rs)
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
            # XXX: I think this is a RO command
            line('cmd.cmd_49(dev)')
        elif cmd == "\x4A\x03\x00\x00\x00":
            cmp_buff("\x03\x00", reply)
            line('cmd.cmd_4A(dev)')
        # Prefix and postfix seem fixed
        # Length 3 and 6 are both common
        #elif len(cmd) % 3 == 0 and cmd[0] == "\x57" and cmd[-1] == "\x00":
        elif len(cmd) in (3, 6) and cmd[0] == "\x57" and cmd[-1] == "\x00":
            cmds = ''
            for i in range(0, len(cmd), 3):
                if cmd[i] != "\x57":
                    raise Exception()
                if cmd[i + 2] != "\x00":
                    raise Exception()
                cmds += cmd[i + 1]
            if cmds == '\x85':
                line('cmd.check_cont(dev)')
            else:
                line('cmd.cmd_57s(dev, %s, %s)' %
                     (fmt_terse(cmds), fmt_terse(reply)))
        # Unknown response
        # Do generic bulk read
        else:
            self.bulk2(p_w, p_rs)

    def bulk86_next_read(self, p):
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
        pack_str = 'packet %s/%s' % (p['packn'][0], p['packn'][1])
        line('_prefix, buff, _size = cmd.bulk86_next_read(dev)')
        line('validate_read(%s, buff, "%s")' %
             (fmt_terse(reply, p['packn'][0]), pack_str))

    def bulk_write(self, p):
        '''
        bulkWrite(0x02, "\x01")
        '''
        # Not all 0x02 have readback
        # bulkWrite(0x%02X
        if p['endp'] != 0x02:
            cmd = binascii.unhexlify(p['data'])
            line('bulkWrite(0x%02X, %s)' %
                 (p['endp'], fmt_terse(cmd, p['packn'][0])))
        # Write followed by response read?
        # bulk2(
        elif not dumb and self.peekp()['type'] == 'bulkRead':
            self.peek_bulk2(p)
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
                line('cmd.cmd_43(dev, "\\x10")')
            elif cmd == "\x4C\x00\x02":
                line('cmd.cmd_4C(dev)')
            elif cmd[0] == "\x57" and len(cmd) == 7:
                c57a = cmd[0:3]
                cmp_mask("\x57\x00\x00", "\xFF\x00\xFF", c57a)

                c50a = cmd[3:]
                cmp_mask("\x50\x00\x00\x00", "\xFF\x00\xFF\xFF", c50a)

                line('cmd.cmd_57_50(dev, %s, %s)' %
                     (fmt_terse(c57a[1]), fmt_terse(c50a[1])))
            elif cmd[0] == "\x50":
                # ex: "\x50\x9F\x09\x00\x00"
                cmp_mask("\x50\x00\x00\x00\x00", "\xFF\x00\x00\xFF\xFF", cmd)
                line('cmd.cmd_50(dev, %s)' % (fmt_terse(cmd[1:3])))
            else:
                line('bulkWrite(0x02, %s)' % (fmt_terse(cmd, p['packn'][0])))

    def file_postfix(self):

        line('''
def open_dev(usbcontext=None):
    if usbcontext is None:
        usbcontext = usb1.USBContext()
    
    print('Scanning for devices...')
    for udev in usbcontext.getDeviceList(skip_on_error=True):
        vid = udev.getVendorID()
        pid = udev.getProductID()
        if (vid, pid) == (0x14b9, 0x0001):
            print
            print
            print('Found device')
            print('Bus %03i Device %03i: ID %04x:%04x' % (
                udev.getBusNumber(),
                udev.getDeviceAddress(),
                vid,
                pid))
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
''')

    def dump_fw(self, save):
        line('# my_fw.py')
        new_fw = set(fw.hash2bin.keys()) - hash_orig
        line('# %u new firmwares' % len(new_fw))
        # save firmwares
        fw_dir = os.path.join(fw.FW_DIR, 'tmp')
        if save:
            if not os.path.exists(fw_dir):
                os.mkdir(fw_dir)
        for h in new_fw:
            d = fw.hash2bin[h]
            line('#   %s: %u bytes' % (h, len(d)))
            if save:
                fn = os.path.join(fw_dir, '%s.bin' % h)
                assert not os.path.exists(fn)
                open(fn, 'w').write(d)
        line('# %u existing firmwares' % (len(hash_used) - len(new_fw)))
        used = [(fw.hash2fns_get_rel(h, None), h) for h in hash_used]
        for fns, h in sorted(used):
            if fns:
                if len(fns) == 1:
                    fn = list(fns)[0]
                    line('#   %s: %s' % (h, fn))
                else:
                    line('#   %s' % (h, ))
                    for fn in fns:
                        line('#     %s' % (fn, ))

        lines_commit()

    def file_prefix(self):
        line('# Generated from scrape.py')
        line('from bpmicro.cmd import bulk2, bulk86')
        line('from bpmicro import cmd')
        line('from bpmicro.usb import usb_wraps')
        line('from bpmicro.usb import validate_read')
        line('from bpmicro import fw')
        line('import usb1')
        line('')

        # remove all comments to make processing easier
        # we'll add our own anyway
        # ps = filter(lambda p: p['type'] != 'comment', ps)

        line('def replay(dev):')
        inc_indent()
        line("bulkRead, bulkWrite, controlRead, controlWrite = usb_wraps(dev)")
        line('')

    def parse_next(self, p):
        comment = False
        if p['type'] == 'comment':
            line('# %s' % p['v'])
            comment = True
        elif p['type'] == 'controlRead':
            if not dumb and (p['bRequest'], p['wValue'], p['wIndex'],
                             p['wLength']) == (0xC0, 0xB0, 0x0000, 0x0000):
                pi = self.eat_packet('bulkRead')
                line('cmd.readB0(dev)')
            else:
                '''
                # Generated from packet 6/7
                # None (0xB0)
                buff = controlRead(0xC0, 0xB0, 0x0000, 0x0000, 4096)
                # NOTE:: req max 4096 but got 3
                validate_read("\x00\x00\x00", buff, "packet 6/7")
                '''
                line('buff = controlRead(0x%02X, 0x%02X, 0x%04X, 0x%04X, %d)' %
                     (p['bRequestType'], p['bRequest'], p['wValue'],
                      p['wIndex'], p['wLength']))
                data = binascii.unhexlify(p['data'])
                #line('# Req: %d, got: %d' % (p['wLength'], len(data)))
                line('validate_read(%s, buff, "packet %s/%s")' % (fmt_terse(
                    data, p['packn'][0]), p['packn'][0], p['packn'][1]))
        elif p['type'] == 'controlWrite':
            '''
            controlWrite(0x40, 0xB2, 0x0000, 0x0000, "")
            '''
            data = binascii.unhexlify(p['data'])
            line('buff = controlWrite(0x%02X, 0x%02X, 0x%04X, 0x%04X, %s)' %
                 (p['bRequestType'], p['bRequest'], p['wValue'], p['wIndex'],
                  fmt_terse(data, pktn=p['packn'][0])))
        elif p['type'] == 'bulkRead':
            self.bulk86_next_read(p)
        elif p['type'] == 'bulkWrite':
            self.bulk_write(p)
        else:
            raise Exception("Unknown type: %s" % p['type'])
        if not comment:
            lines_commit()

    def loop_postfix(self):
        pass

    def dump(self, j, save=False):
        self.pi = 0
        self.ps = j['data']

        self.file_prefix()

        while self.pi < len(self.ps):
            p = self.ps[self.pi]
            self.parse_next(p)
            self.pi += 1

        self.loop_postfix()
        lines_commit()
        dec_indent()

        self.file_postfix()

        line('')
        line('')
        line('')

        self.dump_fw(save)


def load_json(fin, usbrply="", dumb=False):
    if fin.find('.cap') >= 0 or fin.find('.pcapng') >= 0:
        json_fn = '/tmp/scrape.json'
        if dumb:
            cmd = 'usbrply --no-packet-numbers --no-setup --no-comment --fx2 --device-hi %s -j %s >%s' % (
                usbrply, fin, json_fn)
        else:
            cmd = 'usbrply --no-setup --comment --fx2 --device-hi %s -j %s >%s' % (
                usbrply, fin, json_fn)
        subprocess.check_call(cmd, shell=True)
    else:
        json_fn = fin

    j = json.load(open(json_fn))
    return j, json_fn


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--dumb', action='store_true')
    add_bool_arg(parser,
                 '--omit-ro',
                 default=True,
                 help='Omit read only requests (ex: get SM info)')
    parser.add_argument('--big-thresh', type=int, default=256)
    parser.add_argument('--usbrply', default='')
    parser.add_argument('--save', action='store_true', help='Save firmware')
    parser.add_argument('-w', action='store_true', help='Write python file')
    parser.add_argument('fin')
    args = parser.parse_args()

    j, json_fn = load_json(args.fin, args.usbrply, args.dumb)

    if args.w:
        filename, file_extension = os.path.splitext(args.fin)
        fnout = filename + '.py'
        print(('Selected output file %s' % fnout))
        assert fnout != args.fin and fnout != json_fn
        fout = open(fnout, 'w')

    dumb = args.dumb
    omit_ro = args.omit_ro
    scraper = Scraper()
    scraper.dump(j, save=args.save)
