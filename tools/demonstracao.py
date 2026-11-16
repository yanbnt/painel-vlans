# Modo demonstração: abre o painel com uma rede fictícia, sem tocar na rede de verdade.
#
# Tudo o que aparece é inventado: um escritório na faixa 192.168.10.x com switches, câmeras, impressoras e
# computadores, um switch simulado para o terminal e as listas ao vivo, e uma escuta do cabo pronta.
# A placa de rede deste computador não é lida nem alterada, e o Windows não pede autorização nenhuma.
# Serve para conhecer o painel e para gerar as fotos do README (tools/capturar_telas.py).
#
# Uso:  python tools/demonstracao.py [--porta 8780] [--sem-navegador]
import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import frames as F                    # noqa: E402  hand-made network frames, shared with the tests
from mock_switch import MockSwitch    # noqa: E402  a small Comware-style switch, shared with the tests

DATA = tempfile.mkdtemp(prefix='painel-demonstracao-')
os.environ['PAINEL_DADOS'] = DATA     # read by painel_local when it loads, so it must be set first
_spec = importlib.util.spec_from_file_location('painel_local', os.path.join(ROOT, 'painel_local.py'))
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)

MY_MAC, MY_IP, GW = 'D8-BB-C1-10-00-25', '192.168.10.25', '192.168.10.1'
NETS = [('192.168.10.0/24', False), ('192.168.0.0/24', True), ('192.168.1.0/24', True)]
ADAPTER = {'index': 7, 'name': 'Ethernet', 'desc': 'Placa de rede Gigabit', 'status': 'Up', 'mac': MY_MAC, 'dhcp': False, 'gateways': [GW],
           'ips': [{'ip': MY_IP, 'prefix': 24, 'state': 'Preferred', 'mine': False},
                   {'ip': '192.168.0.249', 'prefix': 24, 'state': 'Preferred', 'mine': True},
                   {'ip': '192.168.1.249', 'prefix': 24, 'state': 'Preferred', 'mine': True}]}

PLAN = {
    'vlans': [
        {'vid': 1, 'name': 'Rede atual', 'sector': 'Todos os setores', 'subnet': '192.168.10.0/24', 'gateway': GW, 'color': 0,
         'notes': 'A rede de hoje. Fica até o último setor migrar.'},
        {'vid': 10, 'name': 'Administrativo', 'sector': 'Administrativo e RH', 'subnet': '192.168.11.0/24', 'gateway': '192.168.11.1', 'dhcp': 'No roteador', 'color': 1},
        {'vid': 20, 'name': 'Financeiro', 'sector': 'Financeiro', 'subnet': '192.168.12.0/24', 'gateway': '192.168.12.1', 'dhcp': 'No roteador', 'color': 2},
        {'vid': 30, 'name': 'Câmeras', 'sector': 'Segurança', 'subnet': '192.168.13.0/24', 'gateway': '192.168.13.1', 'color': 3,
         'notes': 'Sem acesso à internet. Só o gravador fala com as câmeras.'},
        {'vid': 40, 'name': 'Visitantes', 'sector': 'Wi-Fi de visitantes', 'subnet': '192.168.14.0/24', 'gateway': '192.168.14.1', 'dhcp': 'No roteador', 'color': 7},
        {'vid': 50, 'name': 'Telefonia', 'sector': 'Ramais IP', 'subnet': '192.168.15.0/24', 'gateway': '192.168.15.1', 'color': 4},
    ],
    'switches': [
        {'id': 'sw_rack', 'name': 'SW-RACK-01', 'model': 's2352g-a', 'syntax': 'comware', 'copper': 48, 'sfp': 4, 'prefix': 'GigabitEthernet1/0/',
         'sfpPrefix': 'GigabitEthernet1/0/', 'mgmtIp': '192.168.10.2', 'mac': 'AC-1E-A9-10-00-02', 'notes': 'Rack principal, segundo andar.', 'order': 1},
        {'id': 'sw_recepcao', 'name': 'SW-RECEPCAO', 'model': 's2328g-a', 'syntax': 'comware', 'copper': 24, 'sfp': 4, 'prefix': 'GigabitEthernet1/0/',
         'sfpPrefix': 'GigabitEthernet1/0/', 'mgmtIp': '192.168.10.3', 'mac': 'AC-1E-A9-10-00-03', 'notes': 'Recepção e sala de reunião.', 'order': 2},
        {'id': 'sw_estoque', 'name': 'SW-ESTOQUE', 'model': 'sf2842mr', 'syntax': 'manual', 'copper': 24, 'sfp': 4, 'prefix': '', 'sfpPrefix': '',
         'mgmtIp': '192.168.0.1', 'mac': '00-1A-3F-10-00-04', 'notes': 'Ainda com o IP de fábrica.', 'order': 3},
    ],
    'ports': {'sw_rack': {}, 'sw_recepcao': {}, 'sw_estoque': {}},
    'plan': {'steps': {'backup': True, 'mapa': True, 'gateway': True, 'vlans': True, 'trunk': True, 'piloto': True}},
}
RACK = [(range(1, 9), 10, 'Adm', 6), (range(9, 15), 20, 'Financeiro', 3), (range(15, 27), 30, 'Câmera', 12),
        (range(27, 33), 50, 'Ramal', 0), (range(33, 37), 40, 'AP visitantes', 0)]
for ports, vid, label, done in RACK:
    for i, n in enumerate(ports):
        PLAN['ports']['sw_rack']['p%d' % n] = {'mode': 'access', 'vlan': vid, 'allowed': [1], 'pvid': 1, 'desc': '%s %02d' % (label, i + 1),
                                               'status': 'testada' if vid == 30 and i < 8 else 'migrada' if i < done else 'pendente'}
ALL = [1, 10, 20, 30, 40, 50]
PLAN['ports']['sw_rack']['p47'] = {'mode': 'trunk', 'vlan': 1, 'allowed': ALL, 'pvid': 1, 'desc': 'Uplink roteador', 'role': 'uplink', 'status': 'migrada'}
PLAN['ports']['sw_rack']['p48'] = {'mode': 'access', 'vlan': 1, 'allowed': [1], 'pvid': 1, 'desc': 'Notebook da TI', 'role': 'mgmt', 'status': 'migrada'}
PLAN['ports']['sw_rack']['p49'] = {'mode': 'trunk', 'vlan': 1, 'allowed': ALL, 'pvid': 1, 'desc': 'Fibra SW-RECEPCAO', 'role': 'uplink', 'status': 'migrada'}
for n, vid, desc in ((1, 10, 'Recepção'), (2, 10, 'Recepção 2'), (3, 30, 'Câmera entrada'), (4, 40, 'AP salão'), (5, 50, 'Ramal recepção')):
    PLAN['ports']['sw_recepcao']['p%d' % n] = {'mode': 'access', 'vlan': vid, 'allowed': [1], 'pvid': 1, 'desc': desc, 'status': 'pendente'}
PLAN['ports']['sw_recepcao']['p25'] = {'mode': 'trunk', 'vlan': 1, 'allowed': ALL, 'pvid': 1, 'desc': 'Fibra SW-RACK-01', 'role': 'uplink', 'status': 'migrada'}


def host(ip, mac, ports=(), net='192.168.10.0/24', outside=False, **said):
    h = {'ip': ip, 'mac': mac, 'net': net, 'outside': outside, 'ports': list(ports), 'title': '', 'realm': '', 'server': '', 'words': '',
         'ssh': '', 'banner': '', 'pc': '', 'name': '', 'group': '', 'descr': '', 'model': '', 'klass': ''}
    h.update(said)
    h['name'] = h['name'] or h['pc']
    h['vendor'] = pl.vendor_of(mac)
    h['kind'] = pl.classify(h, {GW})
    return h


def pc(ip, mac, name):
    return host(ip, mac, (445,), pc=name, group='ESCRITORIO')


HOSTS = [
    host('192.168.0.1', '00-1A-3F-10-00-04', (23, 80, 443), net='192.168.0.0/24', outside=True, title='SF 2842 MR', server='Web Switch'),
    host('192.168.1.108', '58-10-8C-10-01-08', (80, 554, 37777), net='192.168.1.0/24', outside=True, klass='IPC', model='VIP 1130 B'),
    host(GW, '48-A9-8A-10-00-01', (22, 80, 8291, 53), title='RouterOS router configuration page', model='RB750Gr3', name='GW-ESCRITORIO'),
    host('192.168.10.2', 'AC-1E-A9-10-00-02', (22, 23, 80, 443), title='Web managerment Home', name='SW-RACK-01', model='INTELBRAS S2352G-A',
         descr='INTELBRAS Platform Software, Software Version 7.1.070, Release 6368 INTELBRAS S2352G-A Copyright (C) 2024'),
    host('192.168.10.3', 'AC-1E-A9-10-00-03', (22, 23, 80, 443), title='Web managerment Home', name='SW-RECEPCAO', model='INTELBRAS S2328G-A',
         descr='INTELBRAS Platform Software, Software Version 7.1.070, Release 6368 INTELBRAS S2328G-A Copyright (C) 2024'),
    host('192.168.10.5', '54-6C-AC-10-00-05', (80, 443, 37777), klass='TS', model='SMART S2110G-HPA', title='WEB'),
    host('192.168.10.6', '00-1B-54-10-00-06', (22, 23, 80, 443), name='SW-DEPOSITO', model='Cisco CBS350-24P-4G',
         descr='CBS350-24P-4G 24-Port Gigabit PoE Managed Switch'),
    host('192.168.10.7', '94-40-C9-10-00-07', (22, 23, 80, 443), name='SW-LAB', model='HPE 1950-24G-2SFP+-2XGT',
         descr='HPE Comware Platform Software, Software Version 7.1.045, Release 3208P10 HPE 1950-24G-2SFP+-2XGT Switch Copyright (c) 2010-2024'),
    host('192.168.10.20', '30-E1-F1-10-00-20', (80, 443, 554, 37777), klass='NVR', model='NVD 3316 P', title='WEB SERVICE'),
    host('192.168.10.21', '58-10-8C-10-00-21', (80, 554, 37777), klass='IPC', model='VIP 3230 B'),
    host('192.168.10.22', '58-10-8C-10-00-22', (80, 554, 37777), klass='IPC', model='VIP 3240 D'),
    host('192.168.10.23', '58-10-8C-10-00-23', (80, 554, 37777), klass='IPC', model='VIP 5280 Z'),
    host('192.168.10.30', '98-E5-5B-10-00-30', (80, 443, 37777), klass='BSC', model='SS 3530 MF W'),
    host('192.168.10.40', '58-38-79-10-00-40', (80, 443, 9100), pc='IMPRESSORA-FISCAL',
         descr='RICOH SP 3710SF V1.11 / RICOH Network Printer C model', model='RICOH SP 3710SF V1.11'),
    host('192.168.10.41', '00-21-B7-10-00-41', (80, 443, 9100), descr='Lexmark MX310dn version NH63.SB.N640', model='Lexmark MX310dn', title='Lexmark MX310dn'),
    pc('192.168.10.50', '00-E0-4C-10-00-50', 'PC-FINANCEIRO-01'),
    pc('192.168.10.51', '30-E3-A4-10-00-51', 'PC-FINANCEIRO-02'),
    pc('192.168.10.52', 'F4-8E-38-10-00-52', 'PC-RH-01'),
    pc('192.168.10.53', '88-D7-F6-10-00-53', 'NOTE-COMERCIAL'),
    host('192.168.10.54', '00-25-90-10-00-54', (80, 443, 445), pc='SERVIDOR-ARQUIVOS', group='ESCRITORIO'),
    host('192.168.10.60', 'E8-AA-CB-10-00-60', (), name='[TV] Sala de reunião', model='UN43T5300'),
    host('192.168.10.70', '30-E1-F1-10-00-70', (53, 80, 443), title='Intelbras'),
    host('192.168.10.101', 'C6-12-6E-10-01-01', ()),
    host('192.168.10.102', '5A-31-0C-10-01-02', ()),
]


def demo_frames():
    """A minute of announcements on the demo network, as the listening session would record them."""
    sw = 'AC-1E-A9-10-00-02'
    return [
        F.arp_request(MY_MAC, MY_IP, GW),
        F.arp_reply('48-A9-8A-10-00-01', GW, MY_MAC, MY_IP),
        F.arp_reply('00-1A-3F-10-00-04', '192.168.0.1', MY_MAC, '192.168.0.249'),
        F.arp_reply('AC-1E-A9-10-00-09', '192.168.0.1', MY_MAC, '192.168.0.249'),          # a second switch still on the factory address
        F.arp_request('58-10-8C-10-01-08', '192.168.1.108', '192.168.1.1'),
        F.arp_request('00-E0-4C-10-00-77', '10.0.0.15', '10.0.0.1'),                          # a PC left with an old address
        F.lldp('AC-1E-A9-10-00-07', sw, 'GigabitEthernet1/0/48', 'SW-RACK-01', 'Intelbras S2352G-A Software Version 7.1.070', mgmt='192.168.10.2', pvid=1),
        F.stp('AC-1E-A9-10-00-07', sw, sw, port=0x8030),
        F.cdp('00-1B-54-10-00-06', 'SW-DEPOSITO', 'GigabitEthernet24', 'Cisco CBS350-24P-4G', addr='192.168.10.6', native=1),
        F.mndp('48-A9-8A-10-00-01', GW, 'GW-ESCRITORIO', '7.15.3 (stable)', 'MikroTik', 'RB750Gr3', 'ether2', ipv4=GW),
        F.dhcp('00-21-B7-10-00-99', '0.0.0.0', 1, '00-21-B7-10-00-99', [(53, b'\x01'), (12, b'IMPRESSORA-RH'), (60, b'udhcp 1.30.1')]),
        F.dhcp('48-A9-8A-10-00-01', GW, 2, '00-21-B7-10-00-99', [(53, b'\x02'), (54, F.ip(GW)), (1, F.ip('255.255.255.0')), (3, F.ip(GW))], yiaddr='192.168.10.180'),
        F.dhcp('30-E1-F1-10-00-70', '192.168.10.70', 2, '00-21-B7-10-00-99', [(53, b'\x02'), (54, F.ip('192.168.10.70')), (1, F.ip('255.255.255.0')),
                                                                               (3, F.ip('192.168.10.70'))], yiaddr='192.168.10.200'),
        F.ssdp('E8-AA-CB-10-00-60', '192.168.10.60', 'Samsung-Linux/4.1, UPnP/1.0, Samsung_UPnP_SDK/1.0'),
    ]


class DemoSwitch(MockSwitch):
    """The simulated switch, dressed as SW-RACK-01 of the demo plan."""
    def execute(self, cmd, view):
        out = super().execute(cmd, view)
        return out.replace('192.168.0.254', GW).replace('192.168.0.1', '192.168.10.2') if isinstance(out, str) else out


def no_change(*args, **kwargs):
    raise pl.NetError('No modo demonstração a placa de rede deste computador não é alterada.')


def fake_elevated(script, timeout=240):
    """The listening session, without the capture tool: hands back the demo announcements a moment later."""
    os.makedirs(pl.LISTEN_DIR, exist_ok=True)
    open(os.path.join(pl.LISTEN_DIR, 'escuta.on'), 'w').close()
    time.sleep(2)
    with open(os.path.join(pl.LISTEN_DIR, 'escuta.pcapng'), 'wb') as f:
        f.write(F.pcapng(demo_frames()))
    return 0


def main():
    ap = argparse.ArgumentParser(description='Porteiro com uma rede fictícia')
    ap.add_argument('--porta', type=int, default=8780)
    ap.add_argument('--switch', type=int, default=2380, help='porta do switch simulado')
    ap.add_argument('--sem-navegador', action='store_true')
    args = ap.parse_args()

    with open(pl.PLAN, 'w', encoding='utf-8') as f:
        json.dump(PLAN, f, ensure_ascii=False, indent=1)
    pl.net_list = lambda: {'supported': True, 'adapters': [json.loads(json.dumps(ADAPTER))]}
    pl.scan_network = lambda dry=False: {'hosts': json.loads(json.dumps(HOSTS)), 'adapters': pl.net_list()['adapters'],
                                         'nets': [{'net': n, 'from': MY_IP, 'outside': o} for n, o in NETS]}
    pl.net_probe = lambda ip: {'reachable': ip in ('192.168.0.1', '192.168.1.108'), 'mac': ''}
    pl.net_add = pl.net_remove = pl.net_remove_all = pl.scan_prepare = no_change
    pl.run_elevated = fake_elevated

    sw = DemoSwitch(args.switch, name='SW-RACK-01')
    sw.vlans.update({10: 'Administrativo', 20: 'Financeiro', 30: 'Cameras', 40: 'Visitantes', 50: 'Telefonia'})
    for n in range(1, 53):
        sw.ports[n] = {'adm': False, 'up': n not in (7, 8, 13, 14, 30, 31, 32, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 50, 51, 52),
                       'type': 'A', 'pvid': 1, 'desc': '', 'permit': {1}}
    for ports, vid, label, done in RACK:
        for i, n in enumerate(ports):
            sw.ports[n].update(desc='%s %02d' % (label, i + 1), pvid=vid if i < max(done, 8 if vid == 30 else 0) else 1)
    sw.ports[47].update(type='T', desc='Uplink roteador', permit=set(ALL))
    sw.ports[48].update(desc='Notebook da TI')
    sw.ports[49].update(type='T', desc='Fibra SW-RECEPCAO', permit=set(ALL))
    sw.start()
    pl.SESSION.connect('127.0.0.1', args.switch, 'admin', sw.pw)
    pl.SESSION.host = '192.168.10.2'          # shown as the demo plan's address, not as this computer

    print('Modo demonstração: rede fictícia, nada é lido nem alterado na rede de verdade.')
    print('Dados temporários em ' + DATA)
    try:
        return pl.main(['--porta', str(args.porta), '--encerrar-sozinho', '0'] + (['--sem-navegador'] if args.sem_navegador else []))
    finally:
        sw.srv.close()
        shutil.rmtree(DATA, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
