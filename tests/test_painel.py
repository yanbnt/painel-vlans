# Exercises painel_local.py against a mock switch and hand-made network captures. Nothing here changes the computer.
# Usage: python tests/test_painel.py [path to painel_local.py]
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mock_switch import MockSwitch  # noqa: E402

APP = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'painel_local.py'))
spec = importlib.util.spec_from_file_location('painel_local', APP)
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)

PW = 'Mock#2026'
fails = []


def check(name, cond, extra=''):
    print(('ok   ' if cond else 'FAIL ') + name + (' -> ' + str(extra) if (extra and not cond) else ''))
    if not cond:
        fails.append(name)


CREATE = ['system-view', 'vlan 10', ' name Financeiro', ' description Financeiro 192.168.10.0/24', ' quit', 'vlan 20', ' name Comercial', ' quit', 'return']
PORT2 = ['system-view', 'interface GigabitEthernet1/0/2', ' description PC Financeiro 01', ' port link-type access', ' port access vlan 10', ' shutdown', ' undo shutdown', ' quit', 'return']
BADVLAN = ['system-view', 'interface GigabitEthernet1/0/3', ' port link-type access', ' port access vlan 99', ' shutdown', ' undo shutdown', ' quit', 'return']
TRUNK = ['system-view', 'interface GigabitEthernet1/0/24', ' port link-type trunk', ' port trunk permit vlan 1 10 20', ' quit', 'return']
CHECK = ['display vlan brief', 'display port trunk', 'display interface brief']
MYPORT = ['system-view', 'interface GigabitEthernet1/0/1', ' port link-type access', ' port access vlan 10', ' quit', 'return']

for echo in (True, False):
    tag = 'eco ligado' if echo else 'eco desligado'
    port = 2331 if echo else 2332
    mock = MockSwitch(port, echo=echo)
    mock.start()
    s = pl.SwitchSession()
    try:
        s.connect('127.0.0.1', port, 'admin', 'errada')
        check(tag + ': senha errada recusada', False)
    except pl.SwitchError as e:
        check(tag + ': senha errada recusada', 'senha' in str(e).lower(), e)
    check(tag + ': sem sessao apos recusa', not s.connected)
    st = s.connect('127.0.0.1', port, 'admin', PW)
    check(tag + ': login', st['connected'] and st['name'] == 'S2300G', st)
    check(tag + ': porta de chegada identificada', st['myPort'] == 'ge1/0/1' and st['myVlan'] == '1', st)
    ok, res = s.run(CREATE)
    check(tag + ': criar VLANs', ok and all(not r['bad'] for r in res) and 10 in mock.vlans and 20 in mock.vlans, res)
    ok, res = s.run(PORT2)
    labels = [r['cmd'] for r in res]
    check(tag + ': migrar porta com reinicio', ok and 'shutdown + undo shutdown' in labels, res)
    check(tag + ': shutdown e undo chegaram', mock.seen.count('shutdown') == 1 and mock.seen.count('undo shutdown') == 1)
    before = len(mock.seen)
    ok, res = s.run(BADVLAN)
    check(tag + ': erro para o bloco', (not ok) and res[-1]['cmd'] == 'port access vlan 99' and bool(res[-1]['bad']), res)
    sent_after = mock.seen[before:]
    check(tag + ': nada enviado depois do erro', 'shutdown' not in sent_after, sent_after)
    ok, res = s.run(['system-view', 'return'])
    check(tag + ': sessao volta ao modo usuario depois do erro', ok, res)
    ok, res = s.run(TRUNK)
    check(tag + ': trunk', ok and 'Done' in res[3]['out'], res)
    ok, res = s.run(CHECK)
    check(tag + ': conferir nao acusa erro falso', ok and 'Financeiro' in res[0]['out'] and 'input errors' in res[2]['out'], res)
    t0 = time.time()
    ok, res = s.run(['save force'])
    check(tag + ': salvar espera a resposta', ok and 'successfully' in res[0]['out'] and time.time() - t0 > 1.0, res)
    ok, res = s.run(['system-view', 'interface GigabitEthernet1/0/5', ' port access vlan 10', ' quit', 'return'])
    check(tag + ': saida limpa (sem eco nem prompt)', ok and all(r['out'] == '' for r in res[1:]), res)
    s.close()
    mock.srv.close()

check('lista permitida: aceita bloco gerado', pl.check_lines(CREATE + PORT2 + TRUNK + CHECK + ['save force']) is None)
for bad in ('reboot', 'undo vlan 10', 'reset saved-configuration', 'save', 'display current-configuration', 'port access vlan 10; reboot', 'interface x'):
    check('lista permitida: recusa "%s"' % bad, pl.check_lines(['system-view', bad]) is not None)
check('protecao: bloco na minha porta e recusado', pl.cuts_my_port(MYPORT, 'ge1/0/1', '1'))
check('protecao: reinicio na minha porta e recusado', pl.cuts_my_port(['system-view', 'interface GigabitEthernet1/0/1', ' port access vlan 1', ' shutdown', ' undo shutdown', ' quit'], 'ge1/0/1', '1'))
check('protecao: outra porta passa', not pl.cuts_my_port(PORT2, 'ge1/0/1', '1'))
check('protecao: trunk com VLAN 1 sem tag na minha porta passa', not pl.cuts_my_port(['system-view', 'interface GigabitEthernet1/0/1', ' port link-type trunk', ' port trunk permit vlan 1 10', ' quit'], 'ge1/0/1', '1'))
check('protecao: trunk sem VLAN 1 na minha porta e recusado', pl.cuts_my_port(['system-view', 'interface GigabitEthernet1/0/1', ' port link-type trunk', ' port trunk permit vlan 10', ' undo port trunk permit vlan 1', ' quit'], 'ge1/0/1', '1'))
check('protecao: PVID diferente na minha porta e recusado', pl.cuts_my_port(['system-view', 'interface GigabitEthernet1/0/1', ' port link-type trunk', ' port trunk permit vlan 1 10', ' port trunk pvid vlan 10', ' quit'], 'ge1/0/1', '1'))
check('protecao: sem porta conhecida nao bloqueia', not pl.cuts_my_port(MYPORT, '', ''))

# ---- discovery, unit level: nothing here touches the network adapters
ADS = [{'index': 7, 'name': 'Ethernet', 'status': 'Up', 'dhcp': False, 'gateways': ['192.168.16.1'],
        'ips': [{'ip': '192.168.16.145', 'prefix': 24, 'state': 'Preferred', 'mine': False}, {'ip': '192.168.0.249', 'prefix': 24, 'state': 'Preferred', 'mine': True}]},
       {'index': 9, 'name': 'Wi-Fi', 'status': 'Disconnected', 'dhcp': True, 'gateways': [], 'ips': [{'ip': '10.0.0.5', 'prefix': 24, 'state': 'Preferred', 'mine': False}]}]
t = [(s, str(n), o) for s, n, o in pl.scan_targets(ADS)]
check('procura: usa as faixas das placas ligadas', t == [('192.168.16.145', '192.168.16.0/24', False), ('192.168.0.249', '192.168.0.0/24', True)], t)
_, plan = pl.factory_plan(ADS, 7, ['192.168.0', '192.168.88', '192.168.16', '10.0.0'])
check('procura: so soma endereco onde o PC ainda nao esta', plan == ['192.168.88.249', '10.0.0.249'], plan)
for bad in (['8.8.8'], ['abc'], [], ['192.168.300'], 'x'):
    try:
        pl.factory_plan(ADS, 7, bad)
        refused = False
    except pl.NetError:
        refused = True
    check('procura: recusa faixa invalida %r' % (bad,), refused)
check('procura: classifica MikroTik', pl.classify({'ip': '1', 'ports': [8291, 80], 'title': '', 'banner': ''}, set()) == 'MikroTik')
check('procura: classifica switch pela tela web', pl.classify({'ip': '1', 'ports': [23, 80], 'title': 'Web managerment Home', 'banner': 'Login:'}, set()) == 'Switch')
check('procura: classifica o gateway', pl.classify({'ip': '192.168.16.1', 'ports': [80], 'title': 'x', 'banner': ''}, {'192.168.16.1'}) == 'Roteador (gateway)')
check('procura: classifica computador', pl.classify({'ip': '1', 'ports': [445], 'title': '', 'banner': ''}, set()) == 'Computador')
rows = pl.arp_table()
check('procura: le a tabela ARP do Windows', isinstance(rows, list) and all(len(r) == 3 for r in rows), rows[:3])


def K(ports, **kw):
    h = {'ip': '1', 'ports': ports, 'title': '', 'server': '', 'banner': '', 'ssh': '', 'mac': '00-11-22-33-44-55'}
    h.update(kw)
    return pl.classify(h, set())


check('procura: impressora com telnet continua impressora', K([23, 80, 9100], banner='HP JetDirect login:') == 'Impressora')
check('procura: controle de acesso pela tela web', K([23, 80, 443, 554], title='Access Control', banner='RV1126 login:') == 'Controle de acesso')
check('procura: switch pelo cabecalho do servidor web', K([23, 80, 443], server='Web Switch') == 'Switch')
check('procura: switch pelo SSH Comware', K([22], ssh='SSH-2.0-Comware-7.1.070') == 'Switch')
check('procura: MikroTik pelo SSH', K([22], ssh='SSH-2.0-ROSSSH') == 'MikroTik')
check('procura: roteador ou AP pelo DNS', K([80, 443, 53], title='Intelbras') == 'Roteador ou AP Wi-Fi')
check('procura: servidor Windows com DNS e computador', K([445, 53, 80]) == 'Computador')
check('procura: MAC aleatorio sem servicos e celular ou notebook', K([], mac='C6-00-00-00-00-B8') == 'Celular ou notebook (Wi-Fi)')
check('procura: MAC de fabrica sem servicos fica sem palpite', K([], mac='00-E0-4C-00-00-50') == '')

# Local stand-ins for devices: a silent service, one that speaks first, and one that accepts anything and drops it.
import socket  # noqa: E402
import struct  # noqa: E402
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402


def serve(port, on_conn):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(20)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=on_conn, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv


def silent(c):
    time.sleep(4)
    c.close()


def speaker(c):
    c.sendall(b'SSH-2.0-ROSSSH\r\n')
    time.sleep(2)
    c.close()


def reset_now(c):
    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
    c.close()


def fin_later(c):
    time.sleep(1.1)
    c.close()


asked = []


def web(routes, idle=3.0, server='HTTPD'):
    def on_conn(c):
        c.settimeout(idle)
        try:
            req = c.recv(2000).decode('latin-1')
        except OSError:
            c.close()
            return
        if not req:
            c.close()
            return
        path = req.split(' ')[1] if ' ' in req else '/'
        asked.append(path)
        status, headers, body = routes.get(path, ('404 Not Found', '', 'nada aqui'))
        c.sendall(('HTTP/1.0 %s\r\nServer: %s\r\n%sContent-Type: text/html\r\n\r\n%s' % (status, server, headers, body)).encode('utf-8'))
        c.close()
    return on_conn


servers = [serve(2401, silent), serve(2402, speaker), serve(2403, reset_now), serve(2404, fin_later),
           serve(2410, web({'/': ('301 Moved Permanently', 'Location: /web/index.html\r\n', ''),
                            '/web/index.html': ('200 OK', '', '<html><head><TITLE> Web  managerment\n Home </TITLE></head></html>')})),
           serve(2411, web({'/': ('200 OK', '', '<html><head><meta http-equiv="refresh" content="0; url=login.html?a=1&amp;b=2"></head></html>'),
                            '/login.html?a=1&b=2': ('200 OK', '', '<title>Roteador &amp; Wi-Fi</title>')})),
           serve(2412, web({'/': ('302 Found', 'Location: http://10.9.9.9/admin\r\n', '')}, server='Web Switch')),
           serve(2413, web({'/': ('200 OK', '', '<html><script>var a = "<b>x</b>";</script><body><p>404: File not found</p> Use MPFS</body></html>')}, server='')),
           serve(2414, web({'/': ('302 Found', 'Location: /a\r\n', ''), '/a': ('302 Found', 'Location: /\r\n', '')})),
           serve(2415, web({'/': ('200 OK', '', '<title>Impressora</title>')}, idle=0.5)),
           serve(2416, web({'/': ('200 OK', '', '<script>location.href = "/reboot.cgi";</script>'), '/reboot.cgi': ('200 OK', '', '<title>NAO DEVIA ABRIR</title>')})),
           serve(2417, web({'/': ('302 Found', 'Location: /cgi-bin/dynamic/topbar.html\r\n', '<html><head><title>302 Found</title></head><body>Found</body></html>'),
                            '/cgi-bin/dynamic/topbar.html': ('200 OK', '', '<title>Lexmark MX310dn</title>')})),
           serve(2418, web({'/': ('401 Unauthorized', 'WWW-Authenticate: Basic realm="TL-SG3428"\r\n', '<title>401 Unauthorized</title>')}))]
got = {p: pl.tcp_probe('127.0.0.1', p) for p in (2401, 2402, 2403, 2404, 2499)}
check('procura: servico calado conta como aberto', got[2401] == ('open', b''), got[2401])
check('procura: servico que fala primeiro conta como aberto', got[2402][0] == 'open' and got[2402][1].startswith(b'SSH-2.0-ROSSSH'), got[2402])
check('procura: aceita e derruba na hora nao conta', got[2403] == ('dropped', b''), got[2403])
check('procura: aceita e fecha um segundo depois nao conta', got[2404] == ('dropped', b''), got[2404])
check('procura: porta fechada', got[2499] == ('closed', b''), got[2499])
i = pl.http_info('127.0.0.1', False, 2410)
check('procura: segue o redirecionamento ate o titulo', i['ok'] and i['title'] == 'Web managerment Home' and i['server'] == 'HTTPD', i)
i = pl.http_info('127.0.0.1', False, 2411)
check('procura: segue a atualizacao automatica da pagina', i['title'] == 'Roteador & Wi-Fi', i)
i = pl.http_info('127.0.0.1', False, 2412)
check('procura: nao segue redirecionamento para outro equipamento', i['ok'] and i['title'] == '' and i['server'] == 'Web Switch', i)
i = pl.http_info('127.0.0.1', False, 2413)
check('procura: sem titulo, mostra as primeiras palavras', i['title'] == '' and i['server'] == '' and i['words'] == '404: File not found Use MPFS', i)
i = pl.http_info('127.0.0.1', False, 2414)
check('procura: redirecionamento em circulo para', i['ok'] and i['title'] == '', i)
i = pl.http_info('127.0.0.1', False, 2416)
check('procura: nao abre endereco que so um clique abriria', i['title'] == '' and '/reboot.cgi' not in asked, (i, asked))
i = pl.http_info('127.0.0.1', False, 2499)
check('procura: sem tela web', i == {'ok': False, 'title': '', 'realm': '', 'server': '', 'words': ''}, i)
i = pl.http_info('127.0.0.1', False, 2417)
check('procura: titulo "302 Found" nao vira nome, segue ate a pagina', i['title'] == 'Lexmark MX310dn', i)
i = pl.http_info('127.0.0.1', False, 2418)
check('procura: nome do pedido de senha identifica o equipamento', i['title'] == '' and i['realm'] == 'TL-SG3428' and i['ok'], i)
check('procura: switch pelo nome do pedido de senha', K([80], realm='Managed Switch') == 'Switch')
for bad in ('http://10.9.9.9/x', '//10.9.9.9/x', 'javascript:alert(1)', 'http://127.0.0.1:9999/x', '/a b', '/x\r\nHost: y', 'ftp://127.0.0.1/x'):
    check('procura: destino recusado %r' % bad, pl.next_page(bad, '127.0.0.1', False, '/', 80) is None)
check('procura: destino relativo', pl.next_page('login.html', '127.0.0.1', False, '/web/index.html', 80) == (False, '/web/login.html', 80))
check('procura: destino com https no mesmo equipamento', pl.next_page('https://127.0.0.1/login', '127.0.0.1', False, '/', 80) == (True, '/login', 443))
hosts = [{'ip': '127.0.0.1', 'mac': '58-38-79-00-00-01'}]
with ThreadPoolExecutor(max_workers=16) as ex:
    pl.identify(hosts, set(), ex, ports=(2401, 2402, 2403, 2404, 2415, 2410, 2499), web=((2415, False), (2410, False)))
h = hosts[0]
check('procura: so ficam as portas com servico de verdade', h['ports'] == [2401, 2402, 2415, 2410], h)
check('procura: tela web que fecha rapido ainda conta e da o nome', h['title'] == 'Impressora' and h['kind'] == '', h)
for srv in servers:
    srv.close()

# ---- listening on the wire: decoding a hand-made capture, and the session with the administrator part replaced
import ipaddress  # noqa: E402
import frames as F  # noqa: E402

NETS = [ipaddress.ip_network('192.168.16.0/24'), ipaddress.ip_network('192.168.0.0/24')]
cap_dir = tempfile.mkdtemp(prefix='painel-escuta-')
cap = os.path.join(cap_dir, 'a.pcapng')
for order in ('<', '>'):
    with open(cap, 'wb') as f:
        f.write(F.pcapng(F.sample(), order, extra_iface=True))
    got = pl.pcapng_frames(cap)
    check('escuta: le a gravacao (%s)' % ('ordem normal' if order == '<' else 'ordem invertida'), got == F.sample(), len(got))
with open(cap, 'wb') as f:
    f.write(F.pcapng(F.sample())[:-7])
check('escuta: gravacao cortada no fim nao quebra', len(pl.pcapng_frames(cap)) == len(F.sample()) - 1)
with open(cap, 'wb') as f:
    f.write(b'isto nao e uma gravacao')
check('escuta: arquivo que nao e gravacao devolve vazio', pl.pcapng_frames(cap) == [])

A = pl.analyse(F.sample() + [b'', b'\x00' * 9], NETS, {F.ME.replace('-', ':').lower()})
H = {(h['ip'], h['mac']): h for h in A['hosts']}
check('escuta: este computador nao entra na lista', not any(h['mac'] == F.ME for h in A['hosts']), A['hosts'])
cam = H.get(('192.168.1.108', F.CAM))
check('escuta: acha equipamento de outra faixa pelo ARP', bool(cam) and not cam['known'] and cam['net'] == '192.168.1.0/24' and cam['via'] == ['ARP'], cam)
check('escuta: mostra por quem ele pergunta', bool(cam) and cam['asks'] == ['192.168.1.1'], cam)
gw = H.get(('192.168.16.1', F.GW))
check('escuta: equipamento da faixa conhecida', bool(gw) and gw['known'] and gw['net'] == '192.168.16.0/24' and gw['via'] == ['ARP', 'DHCP'], gw)
check('escuta: IP repetido', A['dups'] == [{'ip': '192.168.0.1', 'macs': sorted([F.SW1, F.SW2]), 'me': False}], A['dups'])
check('escuta: roteador que responde pelos outros nao vira IP repetido', not any(d['ip'].startswith('192.168.16.20') for d in A['dups']), A['dups'])
N = {n['proto']: n for n in A['neighbors']}
check('escuta: os quatro tipos de anuncio', sorted(N) == ['CDP', 'LLDP', 'MNDP', 'RSTP'], sorted(N))
n = N.get('LLDP', {})
check('escuta: LLDP diz nome, porta, IP, VLAN e que e switch', n.get('name') == 'SW-TESTE' and n.get('mac') == F.SW2 and n.get('port') == 'GigabitEthernet1/0/5' and
      n.get('ip') == '192.168.16.83' and n.get('vlan') == '1' and n.get('caps') == 'switch' and 'S2328G-PA' in n.get('desc', ''), n)
n = N.get('RSTP', {})
check('escuta: spanning tree diz quem e a raiz', n.get('mac') == F.SW1 and n.get('root') == F.SW1 and n.get('isroot') is True and n.get('port') == '5' and n.get('ip') == '192.168.0.1', n)
n = N.get('CDP', {})
check('escuta: CDP', n.get('name') == 'SW-CISCO.exemplo.local' and n.get('port') == 'FastEthernet0/1' and 'WS-C2960' in n.get('desc', '') and n.get('ip') == '10.1.1.2' and
      n.get('vlan') == '10' and n.get('caps') == 'switch' and n.get('mac') == F.CISCO, n)
n = N.get('MNDP', {})
check('escuta: MikroTik (MNDP)', n.get('name') == 'MikroTik' and n.get('mac') == F.MT and n.get('ip') == '192.168.88.1' and n.get('port') == 'bridge/ether2' and
      n.get('desc') == 'MikroTik RB750Gr3 7.15.3 (stable)', n)
mt = H.get(('192.168.88.1', F.MT))
check('escuta: MikroTik em outra faixa entra na lista', bool(mt) and not mt['known'] and mt['via'] == ['MNDP'] and mt['net'] == '192.168.88.0/24', mt)
check('escuta: dois servidores DHCP', [(d['ip'], d['mac'], d['router'], d['mask']) for d in A['dhcp']] ==
      [('192.168.16.1', F.GW, '192.168.16.1', '255.255.255.0'), ('192.168.16.51', F.AP, '192.168.16.51', '255.255.255.0')], A['dhcp'])
check('escuta: equipamento sem endereco pedindo DHCP', A['waiting'] == [{'mac': F.PRN, 'name': 'IMPRESSORA-RH', 'vendor': 'udhcp 1.30.1'}], A['waiting'])
rt = H.get(('192.168.2.1', F.RT2))
check('escuta: anuncio SSDP traz a identificacao', bool(rt) and rt['vendor'] == 'Linux/3.10 UPnP/1.0 Intelbras/1.0' and rt['via'] == ['SSDP'] and not rt['known'], rt)
v20 = H.get(('10.20.0.5', F.V20))
check('escuta: quadro com VLAN marcada', bool(v20) and v20['vlan'] == '20' and v20['net'] == '10.20.0.0/24', v20)
ap = H.get(('169.254.10.20', F.APIPA))
check('escuta: equipamento sem DHCP (169.254)', bool(ap) and ap['net'] == '169.254.0.0/16' and ap['via'] == ['NetBIOS'], ap)
check('escuta: endereco de internet e ignorado', not any(h['ip'] == '8.8.8.8' for h in A['hosts']))
check('escuta: conta os anuncios', A['frames'] == len(F.sample()) + 2, A['frames'])
A2 = pl.analyse([F.arp_reply(F.CAM, '192.168.16.145', F.ME, '192.168.16.145'), F.arp_request(F.ME, '192.168.16.145', '192.168.16.1')], NETS, {F.ME})
check('escuta: outro equipamento usando o IP deste computador', A2['dups'] == [{'ip': '192.168.16.145', 'macs': sorted([F.ME, F.CAM]), 'me': True}], A2['dups'])

script = pl.listen_script("C:\\pasta d'agua\\escuta", 15)
check('escuta: o roteiro grava so anuncios e limpa tudo no fim', all(x in script for x in (
    "'painel-arp','-d','ARP'", "'painel-lldp','-d','0x88CC'", "'-m','FF-FF-FF-FF-FF-FF'", "'--comp','nics'", "'--pkt-size','0'", 'Start-Sleep -Seconds 15',
    "'etl2pcap'", "@('filter','remove')", "@('stop')", "pasta d''agua", "-match 'painel-'")) and 'netsh' not in script and 'add address' not in script and 'unload' not in script, script)
check('escuta: o roteiro para e limpa os filtros em todos os caminhos de saida', script.count("@('filter','remove')") == 4 and script.index("@('stop'))\n$c = Run @('etl2pcap'") > script.index('Start-Sleep'), script)
# The administrator script itself, run for real in PowerShell (not elevated) with pktmon and arp replaced by stand-ins.
import base64  # noqa: E402

STUB = r'''
function pktmon {
  $global:LASTEXITCODE = 0
  Add-Content -LiteralPath $env:PK_TRACE -Value ($args -join ' ')
  if ($args[0] -eq 'filter' -and $args[1] -eq 'list') { if ($env:PK_CASE -eq 'sobra') { 'painel-arp   ARP' } else { 'Nao ha filtros.' }; return }
  if ($args[0] -eq 'filter' -and $args[1] -eq 'add' -and $env:PK_CASE -eq 'semfiltro') { $global:LASTEXITCODE = 87; 'filtro recusado'; return }
  if ($args[0] -eq 'start' -and $env:PK_CASE -eq 'ocupado') { $global:LASTEXITCODE = 5; Write-Error 'ja em execucao'; return }
  if ($args[0] -eq 'etl2pcap') { if ($env:PK_CASE -eq 'semconversao') { $global:LASTEXITCODE = 2; return }; Set-Content -LiteralPath $args[3] -Value 'pcap' }
  'feito'
}
function arp { Add-Content -LiteralPath $env:PK_TRACE -Value ('arp ' + ($args -join ' ')) }
'''


def run_script(case):
    d = tempfile.mkdtemp(prefix="painel-ps d'agua-")
    text = STUB + pl.listen_script(d, 15).replace('Start-Sleep -Seconds 15', 'Start-Sleep -Milliseconds 300')
    trace = os.path.join(d, 'trace.txt')
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', base64.b64encode(text.encode('utf-16-le')).decode('ascii')],
                       capture_output=True, timeout=120, env=dict(os.environ, PK_CASE=case, PK_TRACE=trace))
    lines = open(trace).read().splitlines() if os.path.exists(trace) else []
    files = sorted(os.listdir(d))
    try:
        with open(os.path.join(d, 'escuta.log'), encoding='utf-8-sig') as f:
            said = f.read()
    except OSError:
        said = ''
    shutil.rmtree(d, ignore_errors=True)
    return r.returncode, [l.replace(d, 'DIR') for l in lines], files, said, r.stderr.decode('utf-8', 'replace')


ADDS = ['filter add ' + ' '.join(f) for f in pl.LISTEN_FILTERS]
START = 'start --capture --comp nics --type flow --pkt-size 0 --file-name DIR\\escuta.etl --file-size 16'
code, tr, files, said, err = run_script('normal')
check('roteiro: escuta normal segue a ordem certa e limpa os filtros', code == 0 and tr == ['filter list'] + ADDS + [START, 'arp -d *', 'stop', 'etl2pcap DIR\\escuta.etl --out DIR\\escuta.pcapng', 'filter remove'], (code, tr, err[:300]))
check('roteiro: avisa o inicio e entrega a gravacao', 'escuta.on' in files and 'escuta.pcapng' in files and 'escuta.log' in files, files)
check('roteiro: o registro guarda cada comando e o resultado', 'pktmon stop -> 0' in said and 'pktmon filter add painel-arp -d ARP -> 0' in said and 'feito' in said, said[:400])
code, tr, files, said, err = run_script('sobra')
check('roteiro: sobra de escuta anterior e parada e limpa antes', code == 0 and tr[:3] == ['filter list', 'stop', 'filter remove'] and tr[3:10] == ADDS and 'sobra de uma escuta anterior' in said, (code, tr[:5]))
code, tr, files, said, err = run_script('ocupado')
check('roteiro: capturador ocupado sai com 5 e tira os filtros', code == 5 and tr[-2:] == [START, 'filter remove'] and 'escuta.on' not in files and 'arp -d *' not in tr and 'ja em execucao' in said, (code, tr[-3:], files, said[-200:]))
code, tr, files, said, err = run_script('semfiltro')
check('roteiro: filtro recusado sai com 4 sem comecar a gravar', code == 4 and tr[-1] == 'filter remove' and START not in tr and 'escuta.on' not in files, (code, tr[-3:], files))
code, tr, files, said, err = run_script('semconversao')
check('roteiro: conversao falha sai com 6 e ainda tira os filtros', code == 6 and tr[-3:] == ['stop', 'etl2pcap DIR\\escuta.etl --out DIR\\escuta.pcapng', 'filter remove'] and 'escuta.pcapng' not in files, (code, tr[-3:], files))

px = [h for h in A['hosts'] if h['mac'] == F.PROXY]
check('escuta: marca quem responde por varios enderecos', len(px) == 4 and all(h['proxy'] for h in px) and not any(h['proxy'] for h in A['hosts'] if h['mac'] != F.PROXY), px)

pl.LISTEN_DIR = os.path.join(cap_dir, 'escuta')
pl.LOG = os.path.join(cap_dir, 'historico.log')
real_elevated = pl.run_elevated
asked = []


def fake_listen(result, write=True, note=''):
    def run(script, timeout=240):
        asked.append(script)
        os.makedirs(pl.LISTEN_DIR, exist_ok=True)
        with open(os.path.join(pl.LISTEN_DIR, 'escuta.log'), 'w', encoding='utf-8-sig') as f:
            f.write(note)
        if result == pl.UAC_DECLINED:
            return result
        with open(os.path.join(pl.LISTEN_DIR, 'escuta.etl'), 'wb') as f:
            f.write(b'bruto')
        with open(os.path.join(pl.LISTEN_DIR, 'escuta.on'), 'w') as f:
            f.write('on')
        time.sleep(1.5)
        if write:
            with open(os.path.join(pl.LISTEN_DIR, 'escuta.pcapng'), 'wb') as f:
                f.write(F.pcapng(F.sample()))
        return result
    return run


def left():
    return sorted(os.listdir(pl.LISTEN_DIR)) if os.path.isdir(pl.LISTEN_DIR) else []


def refused(fn):
    try:
        fn()
        return ''
    except pl.NetError as e:
        return str(e)


r = pl.listen(40, dry=True)
check('escuta: simulacao mostra o roteiro e nao pede permissao', r.get('dry') and r['seconds'] == 40 and any('pktmon' in c for c in r['commands']) and not left(), r)
for bad in (5, 500, 'x', True, None, 40.5):
    check('escuta: recusa tempo %r' % (bad,), 'segundos' in refused(lambda: pl.listen(bad, dry=True)))
pl.run_elevated = fake_listen(0)
r = pl.listen(15)
check('escuta: sessao completa devolve o que foi ouvido', r['seconds'] == 15 and len(r['dups']) == 1 and len(r['neighbors']) == 4 and len(r['dhcp']) == 2 and
      any(h['ip'] == '192.168.1.108' for h in r['hosts']) and isinstance(r['nets'], list), {k: r[k] for k in ('seconds', 'dups', 'nets')})
check('escuta: a gravacao e apagada depois de lida', left() == ['escuta.log'], left())
check('escuta: o pedido de permissao levou o roteiro de escuta', len(asked) == 1 and 'pktmon' in asked[0] and 'Start-Sleep -Seconds 15' in asked[0])
with open(pl.LOG, encoding='utf-8') as f:
    check('escuta: fica registrada no historico', 'escuta do cabo  15 s' in f.read())
pl.run_elevated = fake_listen(pl.UAC_DECLINED)
check('escuta: sem autorizacao, avisa que nada mudou', 'autorizada' in refused(lambda: pl.listen(15)))
check('escuta: sem autorizacao nao sobra arquivo de gravacao', left() == ['escuta.log'], left())
pl.run_elevated = fake_listen(5, write=False, note='pktmon start -> 5\nO Monitor de Pacotes ja esta em execucao.')
why = refused(lambda: pl.listen(15))
check('escuta: falha do capturador explica e traz o detalhe', 'pktmon' in why and 'ja esta em execucao' in why and 'Nada ficou alterado' in why, why)
check('escuta: depois da falha nao sobra arquivo de gravacao', left() == ['escuta.log'], left())
pl.run_elevated = fake_listen(0, write=False)
check('escuta: sem gravacao entregue, avisa', 'código 0' in refused(lambda: pl.listen(15)) or 'gravação' in refused(lambda: pl.listen(15)))
def fake_unreadable(script, timeout=240):
    os.makedirs(pl.LISTEN_DIR, exist_ok=True)
    with open(os.path.join(pl.LISTEN_DIR, 'escuta.on'), 'w') as f:
        f.write('on')
    with open(os.path.join(pl.LISTEN_DIR, 'escuta.pcapng'), 'wb') as f:
        f.write(F.pcapng([]) + b'formato que o painel nao conhece')
    return 0


pl.run_elevated = fake_unreadable
why = refused(lambda: pl.listen(15))
check('escuta: gravacao sem anuncio legivel avisa em vez de dizer que nao achou nada', 'não conseguiu ler' in why and pl.LISTEN_KEPT in why, why)
check('escuta: a gravacao ilegivel fica guardada para analise', left() == sorted(['escuta.log', pl.LISTEN_KEPT]) or left() == sorted([pl.LISTEN_KEPT]), left())
pl.run_elevated = fake_listen(0)
check('escuta: depois de um erro, a proxima escuta funciona', len(pl.listen(15)['hosts']) > 5)
check('escuta: a proxima escuta apaga a gravacao guardada', pl.LISTEN_KEPT not in left(), left())
pl.run_elevated = real_elevated
shutil.rmtree(cap_dir, ignore_errors=True)

# ---- HTTP layer, in a throwaway copy so the real plano.json is never touched
tmp = tempfile.mkdtemp(prefix='painel-teste-')
shutil.copy(APP, tmp)
shutil.copy(os.path.join(os.path.dirname(APP), 'painel-vlans.html'), tmp)
HP = 8791
proc = subprocess.Popen([sys.executable, os.path.join(tmp, 'painel_local.py'), '--porta', str(HP), '--sem-navegador'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
mock = MockSwitch(2333)
mock.start()
base = 'http://127.0.0.1:%d' % HP


def call(method, path, body=None, token=None, host=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('X-Painel-Token', token)
    if host:
        req.add_header('Host', host)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if 'json' in r.headers.get('Content-Type', '') else raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()


try:
    for _ in range(40):
        try:
            urllib.request.urlopen(base + '/', timeout=1).read()
            break
        except Exception:
            time.sleep(0.25)
    code, page = call('GET', '/')
    m = re.search(r'window\.PAINEL_LOCAL=(\{.*?\});', page)
    token = json.loads(m.group(1))['token'] if m else ''
    check('http: pagina servida com o token', code == 200 and bool(token) and 'Painel de VLANs' in page)
    check('http: API sem token e negada', call('GET', '/api/plan')[0] == 403)
    check('http: token errado e negado', call('GET', '/api/plan', token='x')[0] == 403)
    check('http: outro Host e negado', call('GET', '/api/plan', token=token, host='evil.example:%d' % HP)[0] == 403)
    code, plan = call('GET', '/api/plan', token=token)
    check('http: plano vazio no inicio', code == 200 and plan['vlans'] == [])
    plan['vlans'].append({'vid': 10, 'name': 'Financeiro'})
    check('http: grava o plano', call('PUT', '/api/plan', plan, token=token)[0] == 200)
    check('http: le o plano gravado', call('GET', '/api/plan', token=token)[1]['vlans'][0]['vid'] == 10)
    check('http: plano invalido e recusado', call('PUT', '/api/plan', {'vlans': 'x'}, token=token)[0] == 400)
    check('http: aplicar sem conexao e recusado', call('POST', '/api/run', {'lines': CREATE}, token=token)[0] == 409)
    code, st = call('POST', '/api/connect', {'host': '127.0.0.1', 'port': 2333, 'user': 'admin', 'password': 'errada'}, token=token)
    check('http: senha errada', code == 409 and 'senha' in st['error'].lower(), st)
    code, st = call('POST', '/api/connect', {'host': '127.0.0.1', 'port': 2333, 'user': 'admin', 'password': PW}, token=token)
    check('http: conecta', code == 200 and st['connected'] and st['myPort'] == 'ge1/0/1', st)
    code, r = call('POST', '/api/run', {'lines': CREATE, 'label': 'Criar as VLANs'}, token=token)
    check('http: aplica bloco', code == 200 and r['ok'] and len(r['results']) == len(CREATE), r)
    code, r = call('POST', '/api/run', {'lines': ['system-view', 'reboot']}, token=token)
    check('http: comando fora da lista recusado', code == 409 and 'lista permitida' in r['error'], r)
    code, r = call('POST', '/api/run', {'lines': MYPORT}, token=token)
    check('http: bloco na minha porta recusado', code == 409 and 'GE1/0/1' in r['error'], r)
    check('http: nada da minha porta chegou ao switch', 'interface GigabitEthernet1/0/1' not in mock.seen)
    code, r = call('POST', '/api/run', {'lines': BADVLAN, 'label': 'VLAN inexistente'}, token=token)
    check('http: erro do switch volta para a tela', code == 200 and not r['ok'] and r['results'][-1]['bad'], r)
    # ---- network adapters: only reads and dry runs, so this computer is never changed
    code, net = call('GET', '/api/net', token=token)
    check('placa: lista as placas do computador', code == 200 and net['supported'] and len(net['adapters']) >= 1 and all('index' in a and 'ips' in a for a in net['adapters']), net)
    ad = next((a for a in net['adapters'] if a['ips']), net['adapters'][0])
    have = ad['ips'][0]['ip'] if ad['ips'] else None
    used = {i['ip'].rsplit('.', 1)[0] for a in net['adapters'] for i in a['ips']}
    base3 = next(b for b in ('192.168.88', '192.168.77', '10.77.77', '172.20.77') if b not in used)
    code, r = call('POST', '/api/net/add', {'index': ad['index'], 'ip': base3 + '.250', 'prefix': 24, 'dry': True}, token=token)
    check('placa: simulacao mostra os comandos', code == 200 and r.get('dry') and any('store=active' in c and base3 + '.250' in c for c in r['commands']) and not any('gateway' in c for c in r['commands']), r)
    code, net2 = call('GET', '/api/net', token=token)
    check('placa: simulacao nao mudou o computador', [i['ip'] for a in net2['adapters'] for i in a['ips']] == [i['ip'] for a in net['adapters'] for i in a['ips']])
    for bad, why in (({'index': ad['index'], 'ip': '8.8.8.8', 'prefix': 24}, 'endereco publico'), ({'index': 999999, 'ip': base3 + '.250', 'prefix': 24}, 'placa inexistente'),
                     ({'index': ad['index'], 'ip': base3 + '.0', 'prefix': 24}, 'primeiro endereco da faixa'), ({'index': ad['index'], 'ip': base3 + '.250', 'prefix': 31}, 'tamanho de rede invalido'),
                     ({'index': ad['index'], 'ip': 'abc', 'prefix': 24}, 'texto no lugar do IP'), ({'index': 'x', 'ip': base3 + '.250', 'prefix': 24}, 'placa sem numero')):
        bad['dry'] = True
        code, r = call('POST', '/api/net/add', bad, token=token)
        check('placa: recusa ' + why, code == 409 and bool(r.get('error')), r)
    if have:
        code, r = call('POST', '/api/net/add', {'index': ad['index'], 'ip': have, 'prefix': 24, 'dry': True}, token=token)
        check('placa: recusa endereco que o PC ja tem', code == 409, r)
        near = have.rsplit('.', 1)[0] + ('.251' if have.endswith('.250') else '.250')
        code, r = call('POST', '/api/net/add', {'index': ad['index'], 'ip': near, 'prefix': 24, 'dry': True}, token=token)
        check('placa: avisa quando a placa ja esta na faixa', code == 409 and 'faixa' in r.get('error', ''), r)
    code, r = call('POST', '/api/net/remove', {'index': ad['index'], 'ip': base3 + '.250'}, token=token)
    check('placa: so remove o que o painel somou', code == 409, r)
    code, r = call('POST', '/api/net/check', {'ip': '8.8.8.8'}, token=token)
    check('placa: teste recusa endereco de fora', code == 409, r)
    code, r = call('POST', '/api/net/check', {'ip': base3 + '.1'}, token=token)
    check('placa: teste de alcance responde', code == 200 and r['reachable'] is False, r)

    # ---- discovery through the API: dry runs only
    code, r = call('POST', '/api/scan', {'dry': True}, token=token)
    check('procura: simulacao lista as faixas', code == 200 and r.get('dry') and len(r['nets']) >= 1, r)
    code, r = call('POST', '/api/scan/prepare', {'index': ad['index'], 'ranges': ['192.168.88', '192.168.77'], 'dry': True}, token=token)
    check('procura: simulacao das faixas de fabrica', code == 200 and r.get('dry') and len(r['add']) >= 1 and all('store=active' in c for c in r['commands'] if 'add address' in c), r)
    code, r = call('POST', '/api/scan/prepare', {'index': ad['index'], 'ranges': ['8.8.8'], 'dry': True}, token=token)
    check('procura: recusa faixa publica', code == 409, r)
    code, r = call('POST', '/api/net/remove-all', {}, token=token)
    check('procura: remover sem nada somado nao pede permissao', code == 200 and 'adapters' in r, r)
    code, r = call('POST', '/api/listen', {'seconds': 40, 'dry': True}, token=token)
    check('escuta: simulacao pela API', code == 200 and r.get('dry') and any('etl2pcap' in c for c in r['commands']), r)
    code, r = call('POST', '/api/listen', {'seconds': 5, 'dry': True}, token=token)
    check('escuta: API recusa tempo invalido', code == 409 and 'segundos' in r.get('error', ''), r)
    check('escuta: API sem token e negada', call('POST', '/api/listen', {'seconds': 40, 'dry': True})[0] == 403)

    # ---- live lists
    code, r = call('POST', '/api/show', {'what': 'interfaces'}, token=token)
    check('ao vivo: lista de interfaces', code == 200 and 'GE1/0/1 ' in r['out'] and 'bridge mode' in r['out'] and 'display interface brief' not in r['out'], r)
    code, r = call('POST', '/api/show', {'what': 'vlans'}, token=token)
    check('ao vivo: lista de VLANs', code == 200 and 'VLAN ID' in r['out'] and 'Financeiro' in r['out'], r)
    for what in ('mac', 'arp', 'ipif', 'routes', 'version', 'trunks'):
        code, r = call('POST', '/api/show', {'what': what}, token=token)
        check('ao vivo: lista ' + what, code == 200 and len(r['out']) > 20, r)
    code, r = call('POST', '/api/show', {'what': 'reboot'}, token=token)
    check('ao vivo: so aceita listas conhecidas', code == 400, r)
    code, r = call('POST', '/api/run', {'lines': ['system-view', 'interface GigabitEthernet1/0/6', ' undo description', ' description Sala 2', ' quit', 'return'], 'label': 'Descricao'}, token=token)
    check('ao vivo: alterar descricao pela janela da porta', code == 200 and r['ok'] and mock.ports[6]['desc'] == 'Sala 2', r)
    code, r = call('GET', '/api/log', token=token)
    check('log: devolve o historico', code == 200 and any('conectado' in l for l in r['lines']), r)

    # ---- free terminal
    def term(line, confirm=False):
        return call('POST', '/api/term', {'line': line, 'confirm': confirm}, token=token)

    code, r = term('system-view')
    check('terminal: entra no modo de configuracao', code == 200 and r['prompt'] == '[S2300G]' and r['state'] == 'prompt', r)
    code, r = term('vlan 30')
    check('terminal: guarda o modo entre comandos', code == 200 and r['prompt'] == '[S2300G-vlan30]', r)
    term('quit')
    code, r = term('display vlan brief')
    check('terminal: mostra a resposta do switch', code == 200 and 'VLAN 0030' in r['out'] and 'display vlan brief' not in r['out'], r)
    term('interface GigabitEthernet1/0/1')
    code, r = term('port access vlan 30')
    check('terminal: avisa ao mexer na minha porta', code == 409 and 'GE1/0/1' in (r.get('needConfirm') or ''), r)
    check('terminal: comando arriscado nao saiu', 'port access vlan 30' not in mock.seen)
    code, r = term('display this')
    check('terminal: leitura na minha porta passa direto', code == 200, r)
    term('quit')
    term('interface GigabitEthernet1/0/7')
    code, r = term('shutdown')
    check('terminal: shutdown pede confirmacao', code == 409 and bool(r.get('needConfirm')), r)
    code, r = term('shutdown', True)
    check('terminal: confirmado, o comando sai', code == 200 and 'shutdown' in mock.seen, r)
    code, r = term('return')
    check('terminal: return volta ao modo usuario', code == 200 and r['prompt'] == '<S2300G>', r)
    code, r = term('reb')
    check('terminal: abreviacao de reboot pede confirmacao', code == 409 and bool(r.get('needConfirm')), r)
    code, r = term('reboot', True)
    check('terminal: pergunta do switch volta para a tela', code == 200 and r['state'] == 'ask' and '[Y/N]' in r['out'], r)
    code, r2 = call('POST', '/api/run', {'lines': CHECK}, token=token)
    check('terminal: bloco recusado no meio de uma pergunta', code == 409, r2)
    code, r = term('n')
    check('terminal: resposta devolve o controle', code == 200 and r['state'] == 'prompt' and 'answer:n' in mock.seen, r)
    term('system-view')
    term('local-user admin class manage')
    code, r = term('password')
    check('terminal: pedido de senha marca o proximo campo como secreto', code == 200 and r['secret'] and r['state'] == 'ask', r)
    code, r = term('Segredo#9aB')
    check('terminal: segunda pergunta de senha continua secreta', code == 200 and r['secret'], r)
    code, r = term('Segredo#9aB')
    check('terminal: depois da senha, volta ao normal', code == 200 and not r['secret'] and r['state'] == 'prompt', r)
    check('terminal: a senha chegou ao switch', mock.secrets == ['Segredo#9aB'])
    term('quit')
    code, r = term('sysname NOVO')
    check('terminal: acompanha a troca de nome', code == 200 and r['prompt'] == '[NOVO]' and r['status']['name'] == 'NOVO', r)
    code, r = term('linha\x03ruim')
    check('terminal: caractere de controle recusado', code == 400, r)
    code, r = call('POST', '/api/run', {'lines': CHECK, 'label': 'Conferir'}, token=token)
    check('terminal: bloco funciona depois do terminal', code == 200 and r['ok'], r)
    code, st = call('POST', '/api/disconnect', {}, token=token)
    check('http: desconecta', code == 200 and not st['connected'])
    hist0 = open(os.path.join(tmp, 'historico.log'), encoding='utf-8').read()
    check('historico: registra o terminal sem a senha', 'terminal' in hist0 and '(senha omitida)' in hist0 and 'Segredo#9aB' not in hist0)
    hist = open(os.path.join(tmp, 'historico.log'), encoding='utf-8').read()
    check('historico: registra os blocos', 'Criar as VLANs  OK' in hist and 'PAROU' in hist)
    check('historico: sem senha', PW not in hist and 'errada' not in hist)
    saved = open(os.path.join(tmp, 'plano.json'), encoding='utf-8').read()
    check('plano.json: sem senha', PW not in saved)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('TODOS OS TESTES PASSARAM' if not fails else 'FALHAS: %d -> %s' % (len(fails), fails))
sys.exit(1 if fails else 0)
