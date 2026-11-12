# Test double: a tiny Comware-like Telnet CLI with port and VLAN state. Used only to exercise painel_local.py.
import re
import socket
import sys
import threading
import time

ERR = "      ^\n % Unrecognized command found at '^' position."


def port_no(name):
    m = re.search(r'(\d+)/(\d+)/(\d+)$', name)
    return int(m.group(3)) if m else None


class MockSwitch(threading.Thread):
    def __init__(self, port, user='admin', pw='Mock#2026', echo=True, name='S2300G'):
        super().__init__(daemon=True)
        self.port, self.user, self.pw, self.echo, self.name = port, user, pw, echo, name
        self.vlans = {1: 'VLAN 0001'}
        self.ports = {n: {'adm': False, 'up': n in (1, 2, 5, 24), 'type': 'A', 'pvid': 1, 'desc': '', 'permit': {1}} for n in range(1, 29)}
        self.ports[1]['desc'] = 'Meu PC'
        self.ports[9]['desc'] = 'link failed once, 0 input errors'
        self.seen = []
        self.secrets = []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('127.0.0.1', port))
        self.srv.listen(5)

    def run(self):
        while True:
            try:
                c, a = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.client, args=(c,), daemon=True).start()

    def client(self, c):
        view = []

        def prompt():
            if not view:
                return '<%s>' % self.name
            if len(view) == 1:
                return '[%s]' % self.name
            return '[%s-%s]' % (self.name, view[1])

        def out(s):
            c.sendall(s.replace('\n', '\r\n').encode())

        try:
            c.sendall(bytes([255, 251, 1, 255, 251, 3, 255, 253, 31, 255, 253, 24]))
            out('\n' + '*' * 60 + '\n* Copyright (c) Intelbras. All rights reserved.\n' + '*' * 60 + '\n\nLogin: ')
            stage, user, buf, pending = 'login', '', b'', None
            while True:
                d = c.recv(1024)
                if not d:
                    return
                buf += d
                buf = re.sub(rb'\xff[\xfb-\xfe].', b'', buf, flags=re.S)
                while buf:
                    if buf[:1] == b'\x1a':
                        buf = buf[1:]
                        if stage == 'cli':
                            view.clear()
                            out('\n' + prompt())
                        continue
                    m = re.search(rb'\r\n|\r\x00|\n|\r', buf)
                    if not m:
                        break
                    line = buf[:m.start()].decode('utf-8', 'replace')
                    buf = buf[m.end():]
                    if stage == 'login':
                        user = line.strip()
                        out((line + '\n' if self.echo else '\n') + 'Password: ')
                        stage = 'password'
                    elif stage == 'password':
                        if user == self.user and line == self.pw:
                            stage = 'cli'
                            out('\n' + prompt())
                        else:
                            out('\nLogin failed.\n\nLogin: ')
                            stage = 'login'
                    elif pending == 'yn':
                        pending = None
                        self.seen.append('answer:' + line.strip())
                        out('\n' + prompt())
                    elif pending == 'pw1':
                        pending = 'pw2'
                        self.secrets.append(line)
                        out('\nConfirm :')
                    elif pending == 'pw2':
                        pending = None
                        out('\n' + prompt())
                    else:
                        out(line + '\n' if self.echo else '\n')
                        resp = self.execute(line.strip(), view)
                        if resp == '@yn':
                            pending = 'yn'
                            out('This command will reboot the device. Continue? [Y/N]:')
                            continue
                        if resp == '@pw':
                            pending = 'pw1'
                            out('Password:')
                            continue
                        if resp:
                            out(resp + '\n')
                        out(prompt())
        except OSError:
            return

    # ---- listings, in the shape Comware prints them
    def show_interfaces(self):
        rows = ['Brief information on interfaces in route mode:', 'Link: ADM - administratively down; Stby - standby', 'Protocol: (s) - spoofing',
                'Interface            Link Protocol Primary IP      Description', 'InLoop0              UP   UP(s)    --', 'NULL0                UP   UP(s)    --',
                'Vlan1                UP   UP       192.168.0.1', '', 'Brief information on interfaces in bridge mode:', 'Link: ADM - administratively down; Stby - standby',
                'Speed: (a) - auto', 'Duplex: (a)/A - auto; H - half; F - full', 'Type: A - access; T - trunk; H - hybrid',
                'Interface            Link Speed   Duplex Type PVID Description']
        for n, p in sorted(self.ports.items()):
            link = 'ADM' if p['adm'] else ('UP' if p['up'] else 'DOWN')
            rows.append('%-20s %-4s %-7s %-6s %-4s %-4s %s' % ('GE1/0/%d' % n, link, '1G(a)' if link == 'UP' else 'auto', 'F(a)' if link == 'UP' else 'A', p['type'], p['pvid'], p['desc']))
        return '\n'.join(r.rstrip() for r in rows)

    def show_vlans(self):
        rows = ['Brief information about all VLANs:', 'Supported Minimum VLAN ID: 1', 'Supported Maximum VLAN ID: 4094', 'Default VLAN ID: 1',
                'VLAN ID   Name                             Port']
        for vid in sorted(self.vlans):
            members = ['GE1/0/%d' % n for n, p in sorted(self.ports.items()) if (p['type'] == 'A' and p['pvid'] == vid) or (p['type'] == 'T' and vid in p['permit'])]
            chunks = [members[i:i + 4] for i in range(0, len(members), 4)] or [[]]
            rows.append('%-9d %-32s %s' % (vid, self.vlans[vid], '  '.join(chunks[0])))
            for ch in chunks[1:]:
                rows.append(' ' * 43 + '  '.join(ch))
        return '\n'.join(r.rstrip() for r in rows)

    def execute(self, cmd, view):
        self.seen.append(cmd)
        if cmd == '':
            return ''
        if cmd.startswith('display arp'):
            ip = cmd.split()[-1] if len(cmd.split()) > 2 else '192.168.0.2'
            return ('  Type: S-Static   D-Dynamic   O-Openflow   R-Rule   M-Multiport  I-Invalid\n'
                    'IP address      MAC address    VLAN/VSI name Interface                Aging Type\n'
                    '%-15s 00e0-4c68-0a01 1             GE1/0/1                  1183  D' % ip)
        if cmd == 'display vlan brief':
            return self.show_vlans()
        if cmd == 'display interface brief':
            return self.show_interfaces()
        if cmd == 'display port trunk':
            rows = ['Interface            PVID  VLAN Passing']
            rows += ['%-20s %-5d %s' % ('GE1/0/%d' % n, p['pvid'], ', '.join(str(v) for v in sorted(p['permit']))) for n, p in sorted(self.ports.items()) if p['type'] == 'T']
            return '\n'.join(rows)
        if cmd == 'display mac-address':
            return ('MAC Address      VLAN ID    State            Port/Nickname            Aging\n'
                    '00e0-4c68-0a01   1          Learned          GE1/0/1                  Y\n'
                    'ac1e-a939-0001   %-10d Learned          GE1/0/2                  Y\n'
                    '3c52-8261-77aa   1          Learned          GE1/0/24                 Y' % self.ports[2]['pvid'])
        if cmd == 'display ip interface brief':
            return ('*down: administratively down\n(s): spoofing  (l): loopback\n'
                    'Interface           Physical Protocol IP address/Mask    VPN instance Description\n'
                    'Vlan1               up       up       192.168.0.1/24     --           --')
        if cmd == 'display ip routing-table':
            return ('Destinations : 3        Routes : 3\n\n'
                    'Destination/Mask   Proto   Pre Cost        NextHop         Interface\n'
                    '0.0.0.0/0          Static  60  0           192.168.0.254   Vlan1\n'
                    '127.0.0.0/8        Direct  0   0           127.0.0.1       InLoop0\n'
                    '192.168.0.0/24     Direct  0   0           192.168.0.1     Vlan1')
        if cmd == 'display version':
            return ('Intelbras Comware Software, Version 7.1.070, Release 6348P01\n'
                    'Copyright (c) 2004-2024 Intelbras. All rights reserved.\n'
                    'Intelbras S2328G-PA uptime is 0 weeks, 0 days, 3 hours, 12 minutes\n'
                    'Last reboot reason : Cold reboot')
        if cmd == 'save force':
            time.sleep(1.2)
            return 'Validating file. Please wait...\nSaved the current configuration to mainboard device successfully.'
        if not view:
            if cmd == 'reboot':
                return '@yn'
            if cmd == 'screen-length disable':
                return ''
            if cmd == 'system-view':
                view.append('sys')
                return 'System View: return to User View with Ctrl+Z.'
            return ERR
        if cmd == 'return':
            view.clear()
            return ''
        if cmd == 'quit':
            view.pop()
            return ''
        if len(view) == 1:
            m = re.fullmatch(r'sysname (\S+)', cmd)
            if m:
                self.name = m.group(1)
                return ''
            m = re.fullmatch(r'local-user (\S+) class manage', cmd)
            if m:
                view.append('luser-manage-' + m.group(1))
                return ''
            m = re.fullmatch(r'vlan (\d+)', cmd)
            if m:
                self.vlans.setdefault(int(m.group(1)), 'VLAN %04d' % int(m.group(1)))
                view.append('vlan' + m.group(1))
                return ''
            m = re.fullmatch(r'interface (\S+)', cmd)
            if m:
                view.append(m.group(1))
                return ''
            return ERR
        if view[1].startswith('luser'):
            if cmd == 'password':
                return '@pw'
            return '' if cmd.startswith('service-type ') else ERR
        if view[1].startswith('vlan'):
            vid = int(view[1][4:])
            if cmd.startswith('name '):
                self.vlans[vid] = cmd[5:].strip()
                return ''
            if cmd.startswith('description '):
                return ''
            if cmd.startswith('port '):
                names = cmd.split()[1:]
                i = 0
                while i < len(names):
                    a = port_no(names[i])
                    b = port_no(names[i + 2]) if i + 2 < len(names) and names[i + 1] == 'to' else a
                    for n in range(a, b + 1):
                        if n in self.ports:
                            self.ports[n].update(type='A', pvid=vid)
                    i += 3 if b != a or (i + 1 < len(names) and names[i + 1] == 'to') else 1
                return ''
            return ERR
        p = self.ports.get(port_no(view[1]) or 0)
        if p is None:
            return ERR
        if cmd.startswith('description '):
            p['desc'] = cmd[12:].strip()
            return ''
        if cmd == 'undo description':
            p['desc'] = ''
            return ''
        if cmd == 'port link-type access':
            p.update(type='A', pvid=1, permit={1})
            return ''
        if cmd == 'port link-type trunk':
            p.update(type='T', permit={1})
            return ''
        if cmd == 'shutdown':
            p['adm'] = True
            return ''
        if cmd == 'undo shutdown':
            p['adm'] = False
            return ''
        if cmd == 'undo port trunk permit vlan 1':
            p['permit'].discard(1)
            return ''
        m = re.fullmatch(r'port access vlan (\d+)', cmd)
        if m:
            if int(m.group(1)) not in self.vlans:
                return ' Error: The VLAN does not exist.'
            p['pvid'] = int(m.group(1))
            return ''
        if cmd.startswith('port trunk permit vlan '):
            p['permit'].update(int(v) for v in cmd.split()[4:])
            return ' Please wait... Done.'
        m = re.fullmatch(r'port trunk pvid vlan (\d+)', cmd)
        if m:
            p['pvid'] = int(m.group(1))
            return ''
        return ERR


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2323
    MockSwitch(port).start()
    print('mock switch em 127.0.0.1:%d (admin / Mock#2026)' % port)
    sys.stdout.flush()
    while True:
        time.sleep(3600)
