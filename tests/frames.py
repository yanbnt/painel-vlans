# Hand-made network frames and capture files, used only to test painel_local.py without a real capture.
import struct

BC = 'FF-FF-FF-FF-FF-FF'
ZERO = '00-00-00-00-00-00'
ME = 'AC-AA-AA-AA-AA-01'
GW = '80-85-44-00-00-51'
SW1 = '00-1A-3F-00-00-16'
SW2 = 'AC-1E-A9-00-00-E7'
SW2_PORT = 'AC-1E-A9-00-00-EC'
CAM = '3C-EF-8C-11-22-33'
MT = '48-A9-8A-00-00-01'
CISCO = '00-1B-54-00-00-09'
PRN = '00-80-77-AA-BB-CC'
AP = '30-E1-F1-00-00-79'
RT2 = 'C0-25-E9-00-00-02'
V20 = '00-11-22-33-44-55'
APIPA = '00-50-56-00-00-07'
PUB = '00-50-56-00-00-08'
PROXY = '02-00-00-00-00-99'


def mac(s):
    return bytes(int(x, 16) for x in s.split('-'))


def ip(s):
    return bytes(int(x) for x in s.split('.'))


def eth(dst, src, etype, payload, vlan=None):
    tag = struct.pack('>HH', 0x8100, vlan) if vlan is not None else b''
    return mac(dst) + mac(src) + tag + struct.pack('>H', etype) + payload


def arp(op, sha, spa, tha, tpa):
    return struct.pack('>HHBBH', 1, 0x0800, 6, 4, op) + mac(sha) + ip(spa) + mac(tha) + ip(tpa)


def arp_request(sha, spa, tpa, vlan=None):
    return eth(BC, sha, 0x0806, arp(1, sha, spa, ZERO, tpa), vlan)


def arp_reply(sha, spa, tha, tpa):
    return eth(tha, sha, 0x0806, arp(2, sha, spa, tha, tpa))


def udp(dst_mac, src_mac, sip, dip, sport, dport, payload):
    u = struct.pack('>HHHH', sport, dport, 8 + len(payload), 0) + payload
    h = struct.pack('>BBHHHBBH', 0x45, 0, 20 + len(u), 0, 0, 64, 17, 0) + ip(sip) + ip(dip)
    return eth(dst_mac, src_mac, 0x0800, h + u)


def lldp(src, chassis, port, name, desc, mgmt=None, caps=0x0004, pvid=None, portdesc=None):
    def t(kind, v):
        return struct.pack('>H', (kind << 9) | len(v)) + v
    p = t(1, b'\x04' + mac(chassis)) + t(2, b'\x05' + port.encode()) + t(3, b'\x00\x78')
    if portdesc:
        p += t(4, portdesc.encode())
    p += t(5, name.encode()) + t(6, desc.encode()) + t(7, struct.pack('>HH', caps, caps))
    if mgmt:
        p += t(8, b'\x05\x01' + ip(mgmt) + b'\x02' + struct.pack('>I', 1) + b'\x00')
    if pvid is not None:
        p += t(127, b'\x00\x80\xc2\x01' + struct.pack('>H', pvid))
    return eth('01-80-C2-00-00-0E', src, 0x88CC, p + t(0, b''))


def stp(src, root, bridge, port=0x8005, version=2):
    bpdu = (struct.pack('>HBBB', 0, version, 2 if version else 0, 0x3c) + struct.pack('>H', 32768) + mac(root) +
            struct.pack('>I', 0 if root == bridge else 20000) + struct.pack('>H', 32768) + mac(bridge) +
            struct.pack('>H', port) + struct.pack('>HHHH', 0, 20 * 256, 2 * 256, 15 * 256) + (b'\x00' if version else b''))
    llc = b'\x42\x42\x03' + bpdu
    return mac('01-80-C2-00-00-00') + mac(src) + struct.pack('>H', len(llc)) + llc


def cdp(src, device, port, platform, addr=None, native=None):
    def t(kind, v):
        return struct.pack('>HH', kind, 4 + len(v)) + v
    p = b'\x02\xb4\x00\x00' + t(1, device.encode())
    if addr:
        p += t(2, struct.pack('>I', 1) + b'\x01\x01\xcc' + struct.pack('>H', 4) + ip(addr))
    p += t(3, port.encode()) + t(4, struct.pack('>I', 0x28)) + t(6, platform.encode())
    if native is not None:
        p += t(10, struct.pack('>H', native))
    llc = b'\xaa\xaa\x03\x00\x00\x0c\x20\x00' + p
    return mac('01-00-0C-CC-CC-CC') + mac(src) + struct.pack('>H', len(llc)) + llc


def mndp(src, sip, identity, version, platform, board, ifname, ipv4=None):
    def t(kind, v):
        return struct.pack('>HH', kind, len(v)) + v
    p = (b'\x00\x00\x00\x01' + t(1, mac(src)) + t(5, identity.encode()) + t(7, version.encode()) + t(8, platform.encode()) +
         t(10, struct.pack('<I', 3600)) + t(12, board.encode()) + t(16, ifname.encode()))
    if ipv4:
        p += t(17, ip(ipv4))
    return udp(BC, src, sip, '255.255.255.255', 5678, 5678, p)


def dhcp(src, sip, op, chaddr, opts, yiaddr='0.0.0.0'):
    body = (struct.pack('>BBBBIHH', op, 1, 6, 0, 0x12345678, 0, 0x8000) + ip('0.0.0.0') + ip(yiaddr) + ip('0.0.0.0') + ip('0.0.0.0') +
            mac(chaddr) + b'\x00' * 10 + b'\x00' * 192 + b'\x63\x82\x53\x63')
    for c, v in opts:
        body += bytes([c, len(v)]) + v
    return udp(BC, src, sip, '255.255.255.255', 68 if op == 1 else 67, 67 if op == 1 else 68, body + b'\xff')


def ssdp(src, sip, server):
    body = ('NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nCACHE-CONTROL: max-age=1800\r\n'
            'LOCATION: http://%s:1900/rootDesc.xml\r\nSERVER: %s\r\nNT: upnp:rootdevice\r\n\r\n' % (sip, server)).encode()
    return udp('01-00-5E-7F-FF-FA', src, sip, '239.255.255.250', 1900, 1900, body)


def sample(me=ME, my_ip='192.168.16.145', my_ip2='192.168.0.250'):
    """A capture as a small office network would produce it, with one of each thing the panel must recognise."""
    fr = [
        arp_request(me, my_ip, '192.168.16.1'),
        arp_reply(GW, '192.168.16.1', me, my_ip),
        arp_reply(SW1, '192.168.0.1', me, my_ip2),
        arp_reply(SW2, '192.168.0.1', me, my_ip2),                      # the same address, a second device
        arp_request(CAM, '192.168.1.108', '192.168.1.1'),
        arp_request(CAM, '192.168.1.108', '192.168.1.1'),
        lldp(SW2_PORT, SW2, 'GigabitEthernet1/0/5', 'SW-TESTE', 'Intelbras S2328G-PA Software Version 7.1.070', mgmt='192.168.16.83', pvid=1),
        stp('00-1A-3F-00-00-1B', SW1, SW1),
        cdp(CISCO, 'SW-CISCO.exemplo.local', 'FastEthernet0/1', 'cisco WS-C2960-24TT-L', addr='10.1.1.2', native=10),
        mndp(MT, '192.168.88.1', 'MikroTik', '7.15.3 (stable)', 'MikroTik', 'RB750Gr3', 'bridge/ether2', ipv4='192.168.88.1'),
        dhcp(PRN, '0.0.0.0', 1, PRN, [(53, b'\x01'), (12, b'IMPRESSORA-RH'), (60, b'udhcp 1.30.1')]),
        dhcp(GW, '192.168.16.1', 2, PRN, [(53, b'\x02'), (54, ip('192.168.16.1')), (1, ip('255.255.255.0')), (3, ip('192.168.16.1'))], yiaddr='192.168.16.77'),
        dhcp(AP, '192.168.16.51', 2, PRN, [(53, b'\x02'), (54, ip('192.168.16.51')), (1, ip('255.255.255.0')), (3, ip('192.168.16.51'))], yiaddr='192.168.16.200'),
        ssdp(RT2, '192.168.2.1', 'Linux/3.10 UPnP/1.0 Intelbras/1.0'),
        arp_request(V20, '10.20.0.5', '10.20.0.1', vlan=20),
        udp(BC, APIPA, '169.254.10.20', '169.254.255.255', 137, 137, b'\x00' * 50),
        udp(BC, PUB, '8.8.8.8', '255.255.255.255', 9999, 9999, b'x'),      # not an internal address: ignored
        eth(BC, CAM, 0x0806, b'\x00\x01'),                                  # cut short
        eth('01-80-C2-00-00-0E', SW2_PORT, 0x88CC, b'\x02'),                # cut short
        eth(BC, CAM, 0x0800, b'\x45\x00'),                                  # cut short
    ]
    for i in range(1, 5):                                                   # a router answering for four other devices
        fr.append(arp_reply('00-E0-4C-00-00-%02X' % i, '192.168.16.%d' % (200 + i), me, my_ip))
        fr.append(arp_reply(PROXY, '192.168.16.%d' % (200 + i), me, my_ip))
    return fr


def pcapng(frames, order='<', extra_iface=False):
    def block(kind, body):
        body += b'\x00' * (-len(body) % 4)
        n = 12 + len(body)
        return struct.pack(order + 'II', kind, n) + body + struct.pack(order + 'I', n)
    out = block(0x0A0D0D0A, struct.pack(order + 'IHHq', 0x1A2B3C4D, 1, 0, -1))
    out += block(1, struct.pack(order + 'HHI', 1, 0, 0))                    # interface 0: Ethernet
    if extra_iface:
        out += block(1, struct.pack(order + 'HHI', 101, 0, 0))              # interface 1: not Ethernet, must be ignored
    out += block(5, struct.pack(order + 'I', 0) + b'\x00' * 8)              # a block kind the reader does not need
    for i, fr in enumerate(frames):
        out += block(6, struct.pack(order + 'IIIII', 0, 0, i, len(fr), len(fr)) + fr)
    if extra_iface:
        out += block(6, struct.pack(order + 'IIIII', 1, 0, 0, 40, 40) + b'\x45' + b'\x00' * 39)
    return out
