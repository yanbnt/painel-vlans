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
    pl.identify(hosts, set(), ex, ports=(2401, 2402, 2403, 2404, 2415, 2410, 2499), web=((2415, False), (2410, False)), ask=False)
h = hosts[0]
check('procura: so ficam as portas com servico de verdade', h['ports'] == [2401, 2402, 2415, 2410], h)
check('procura: tela web que fecha rapido ainda conta e da o nome', h['title'] == 'Impressora' and h['kind'] == '', h)
for srv in servers:
    srv.close()

# ---- automatic identification: each "who are you?" question against a local stand-in that answers like the real device


def udp_serve(port, answer):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', port))

    def loop():
        while True:
            try:
                data, peer = s.recvfrom(8192)
            except OSError:
                return
            out = answer(data) if callable(answer) else answer
            if out:
                s.sendto(out, peer)
    threading.Thread(target=loop, daemon=True).start()
    return s


def nb_entry(name, suffix, group):
    return name.ljust(15).encode('ascii') + bytes([suffix]) + struct.pack('>H', 0x8400 if group else 0x0400)


def nb_answer(q):
    rd = bytes([3]) + nb_entry('PC-FINANCEIRO', 0x00, False) + nb_entry('WORKGROUP', 0x00, True) + nb_entry('PC-FINANCEIRO', 0x20, False) + b'\x00' * 46
    return q[:2] + struct.pack('>HHHHH', 0x8400, 0, 1, 0, 0) + b'\x20CK' + b'A' * 30 + b'\x00' + struct.pack('>HHIH', 0x21, 1, 0, len(rd)) + rd


def snmp_answer(descr, name):
    binds = pl.ber(0x30, pl.ber(0x06, pl.SNMP_OIDS[0]) + pl.ber(0x04, descr)) + pl.ber(0x30, pl.ber(0x06, pl.SNMP_OIDS[1]) + pl.ber(0x04, name))
    pdu = pl.ber(0xA2, pl.ber(0x02, b'\x12\x34\x56\x78') + b'\x02\x01\x00\x02\x01\x00' + pl.ber(0x30, binds))
    return pl.ber(0x30, b'\x02\x01\x01' + pl.ber(0x04, b'public') + pdu)


SW_DESCR = b'INTELBRAS Platform Software, Software Version 7.1.070, Release 6368 INTELBRAS S2352G-A Copyright (C) 2024 Intelbras S.A. All rights reserved.'
DH_BODY = (b'{"mac":"00:11:22:33:44:55","method":"client.notifyDevInfo","params":{"deviceInfo":{"DeviceClass":"TS","DeviceType":"SMART S2110G-HPA",'
           b'"Vendor":"General","SerialNo":"XXXX","Version":"1.000"}}}\n\x00')
ONVIF_BODY = (b'<?xml version="1.0"?><e:Envelope><e:Body><d:ProbeMatches><d:ProbeMatch><d:Scopes>onvif://www.onvif.org/location/ '
              b'onvif://www.onvif.org/name/Intelbras onvif://www.onvif.org/hardware/iMHDX_3132</d:Scopes></d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>')
UPNP_XML = '<?xml version="1.0"?><root><device><friendlyName>[TV] Sala &amp; Recep (43)</friendlyName><manufacturer>Samsung Electronics</manufacturer><modelName>UN43T5300</modelName></device></root>'
stubs = [udp_serve(2437, nb_answer), udp_serve(2461, snmp_answer(SW_DESCR, b'INTELBRAS')),
         udp_serve(2478, b'\x20\x00\x00\x00DHIP' + b'\x00' * 24 + DH_BODY), udp_serve(2472, ONVIF_BODY),
         udp_serve(2419, b'HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLOCATION: http://127.0.0.1:2420/desc.xml\r\nSERVER: Linux UPnP/1.0\r\nST: upnp:rootdevice\r\n\r\n'),
         udp_serve(2418, b'HTTP/1.1 200 OK\r\nLOCATION: http://10.9.9.9:2420/desc.xml\r\n\r\n'),
         udp_serve(2462, b'isto nao e SNMP'), udp_serve(2479, b'\x20\x00\x00\x00DHIP' + b'\x00' * 24 + b'{"params": 5}')]
webs = [serve(2420, web({'/desc.xml': ('200 OK', '', UPNP_XML)}))]
check('identifica: nome do computador e grupo (NetBIOS)', pl.netbios_name('127.0.0.1', 2437) == ('PC-FINANCEIRO', 'WORKGROUP'), pl.netbios_name('127.0.0.1', 2437))
d, n = pl.snmp_info('127.0.0.1', 2461)
check('identifica: descricao e nome por SNMP, com texto longo', d == SW_DESCR.decode() and n == 'INTELBRAS', (d, n))
check('identifica: modelo dentro da descricao SNMP do switch', pl.snmp_model(d) == 'INTELBRAS S2352G-A', pl.snmp_model(d))
check('identifica: modelo dentro da descricao SNMP de impressoras', pl.snmp_model('RICOH SP 3710SF V1.11 / RICOH Network Printer C model / RICOH Network Scanner C model') == 'RICOH SP 3710SF V1.11'
      and pl.snmp_model('Lexmark MX310dn version NH63.SB.N640 kernel 3.0.0 All-N-1') == 'Lexmark MX310dn' and pl.snmp_model('') == '')
check('identifica: modelo e classe pela pergunta da linha Intelbras/Dahua', pl.dahua_info('127.0.0.1', 2478) == {'model': 'SMART S2110G-HPA', 'klass': 'TS'}, pl.dahua_info('127.0.0.1', 2478))
check('identifica: equipamento de video pelo ONVIF', pl.onvif_info('127.0.0.1', 2472) == ('iMHDX 3132', 'Intelbras'), pl.onvif_info('127.0.0.1', 2472))
check('identifica: nome, marca e modelo pela descricao UPnP', pl.upnp_info('127.0.0.1', 2419) == ('[TV] Sala & Recep (43)', 'Samsung Electronics', 'UN43T5300'), pl.upnp_info('127.0.0.1', 2419))
check('identifica: descricao UPnP em outro equipamento e ignorada', pl.upnp_info('127.0.0.1', 2418) == ('', '', ''), pl.upnp_info('127.0.0.1', 2418))
check('identifica: respostas estranhas nao quebram', pl.snmp_info('127.0.0.1', 2462) == ('', '') and pl.dahua_info('127.0.0.1', 2479) == {} and pl.netbios_name('127.0.0.1', 2462) == ('', ''))
t0 = time.time()
quiet = (pl.netbios_name('127.0.0.1', 2498), pl.snmp_info('127.0.0.1', 2498), pl.dahua_info('127.0.0.1', 2498), pl.onvif_info('127.0.0.1', 2498), pl.upnp_info('127.0.0.1', 2498))
check('identifica: quem nao responde devolve vazio, sem demorar', quiet == (('', ''), ('', ''), {}, ('', ''), ('', '', '')) and time.time() - t0 < 12, (quiet, time.time() - t0))
for s in stubs + webs:
    s.close()
check('identifica: titulo que a pagina define por script', pl.script_title('<script>var stitle = "SF 2842 MR";</script><title></title><script>document.title = stitle;</script>') == 'SF 2842 MR'
      and pl.script_title('<script>document.title="Roteador X"</script>') == 'Roteador X' and pl.script_title('<p>document sem titulo</p>') == '')
check('identifica: modelo escrito na pagina de login da linha SF/SG', pl.page_model('var g_corporation = "Intelbras S.A."; banner += "<DIV><strong>SF</strong> 2842MR</DIV>";') == 'SF 2842 MR'
      and pl.page_model('<b>SG</b> 2404 PoE da intelbras') == 'SG 2404 PoE' and pl.page_model('<strong>SF</strong> 2842MR de outra marca') == '' and pl.page_model('intelbras sem modelo') == '')
vend = os.path.join(tempfile.mkdtemp(prefix='painel-fab-'), 'fabricantes.tsv')
with open(vend, 'w', encoding='utf-8') as f:
    f.write('AC1EA9\tIntelbras\n583879\tRICOH\nlinha torta\n')
pl.VENDOR_FILE, pl.VENDORS = vend, None
check('identifica: fabricante pelo comeco do MAC', pl.vendor_of('AC-1E-A9-00-00-E7') == 'Intelbras' and pl.vendor_of('58:38:79:00:00:01') == 'RICOH' and pl.vendor_of('ac1e-a900-00e7') == 'Intelbras')
check('identifica: MAC aleatorio, desconhecido ou torto fica sem fabricante', pl.vendor_of('C6-00-00-00-00-B8') == '' and pl.vendor_of('00-00-5E-00-00-01') == '' and pl.vendor_of('') == '' and pl.vendor_of(None) == '')
pl.VENDOR_FILE, pl.VENDORS = os.path.join(os.path.dirname(vend), 'nao-existe.tsv'), None
check('identifica: sem a tabela de fabricantes, a procura continua', pl.vendor_of('AC-1E-A9-00-00-E7') == '')
pl.VENDOR_FILE, pl.VENDORS = vend, None

check('tipo: switch smart que responde como camera', K([80, 443, 37777], klass='TS', model='SMART S2110G-HPA') == 'Switch')
check('tipo: switch pela descricao SNMP', K([23, 80, 443], descr=SW_DESCR.decode(), model='INTELBRAS S2352G-A', name='INTELBRAS') == 'Switch')
check('tipo: switch pelo modelo SF/SG na pagina', K([23, 80, 443], title='SF 2842 MR') == 'Switch')
check('tipo: camera', K([80, 554, 37777], klass='IPC', model='VIP-7430-A-FT') == 'Câmera' and K([80, 554], model='VIP-9420-IA-FT') == 'Câmera')
check('tipo: gravador', K([80, 37777], klass='MHDX', model='iMHDX 3132') == 'Gravador de vídeo' and K([80, 37777], klass='NVR', model='iNVD 9216 PE FT') == 'Gravador de vídeo')
check('tipo: controle de acesso', K([80, 37777], klass='BSC', model='SS 1530 MF W') == 'Controle de acesso' and K([23, 80, 554], model='XPE-3200-PLUS-IP') == 'Controle de acesso')
check('tipo: decodificador de video', K([37777], klass='Matrix', model='DWL7804_G2') == 'Decodificador de vídeo')
check('tipo: sem classe conhecida continua camera ou gravador', K([80, 37777]) == 'Câmera ou gravador' and K([80, 37777], klass='XYZ') == 'Câmera ou gravador')
check('tipo: servidor Windows que compartilha impressora e computador', K([80, 443, 445, 9100], pc='SERVIDOR', name='SERVIDOR') == 'Computador')
check('tipo: impressora com nome NetBIOS continua impressora', K([80, 443, 9100], pc='3710SF-000001', name='3710SF-000001', descr='RICOH SP 3710SF V1.11 / RICOH Network Printer C model') == 'Impressora')
check('tipo: impressora so pela descricao', K([80], descr='Lexmark MX310dn version NH63 Network Printer') == 'Impressora')
check('tipo: computador com firewall, pelo nome', K([], pc='DESKTOP-X', name='DESKTOP-X', mac='C6-00-00-00-00-B8') == 'Computador')
check('tipo: TV', K([], name='[TV] Samsung 5 Series (43)', model='UN43T5300AGXZD') == 'TV' and K([], name='Samsung BE 55 TV - Projetos') == 'TV')
check('tipo: nome que nao e de computador nao vira computador', K([80], name='nobreak') == 'Equipamento com tela web')
check('tipo: modelo de celular nao vira switch', K([], model='SM-S9010') == '' and K([], model='SS 1530') == '')
hosts = [{'ip': '127.0.0.1', 'mac': 'AC-1E-A9-00-00-E7'}]
real = (pl.netbios_name, pl.snmp_info, pl.upnp_info, pl.dahua_info, pl.onvif_info)
pl.netbios_name, pl.snmp_info, pl.upnp_info = (lambda ip: ('', '')), (lambda ip: (SW_DESCR.decode(), 'INTELBRAS')), (lambda ip: ('', '', ''))
pl.dahua_info, pl.onvif_info = (lambda ip: {}), (lambda ip: ('', ''))
with ThreadPoolExecutor(max_workers=8) as ex:
    pl.identify(hosts, set(), ex, ports=(2499,), web=())
h = hosts[0]
check('identifica: a procura junta fabricante, nome, modelo e tipo', (h['vendor'], h['name'], h['model'], h['kind'], h['pc']) == ('Intelbras', 'INTELBRAS', 'INTELBRAS S2352G-A', 'Switch', ''), h)
pl.netbios_name, pl.snmp_info, pl.upnp_info, pl.dahua_info, pl.onvif_info = real
shutil.rmtree(os.path.dirname(vend), ignore_errors=True)
pl.VENDOR_FILE, pl.VENDORS = os.path.join(os.path.dirname(APP), 'fabricantes.tsv'), None
check('identifica: a tabela de fabricantes que acompanha o programa carrega', pl.vendor_of('AC-1E-A9-00-00-E7') == 'Intelbras' and len(pl.VENDORS) > 30000, len(pl.VENDORS or {}))

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

# ---- running as an installed app: where things go, which window opens, one copy at a time
here, data_env = r'C:\Programa', {'LOCALAPPDATA': r'C:\Users\x\AppData\Local'}
check('app: como script, programa e dados na mesma pasta', pl.app_dirs(False, '', here, data_env) == (here, here))
check('app: instalado, dados na pasta do usuario', pl.app_dirs(True, r'C:\Programa\_internal', here, data_env) == (r'C:\Programa\_internal', os.path.join(r'C:\Users\x\AppData\Local', 'Painel de VLANs')))
check('app: instalado sem pasta interna usa a do programa', pl.app_dirs(True, '', here, data_env)[0] == here)
check('app: PAINEL_DADOS escolhe a pasta de dados', pl.app_dirs(True, 'b', here, dict(data_env, PAINEL_DADOS=r'D:\dados'))[1] == r'D:\dados'
      and pl.app_dirs(False, '', here, {'PAINEL_DADOS': r'D:\dados'})[1] == r'D:\dados')
fresh_spec = importlib.util.spec_from_file_location('painel_local_novo', APP)      # earlier tests repointed some paths
fresh = importlib.util.module_from_spec(fresh_spec)
fresh_spec.loader.exec_module(fresh)
check('app: dados na pasta de dados, arquivos do programa na pasta do programa', all(p.startswith(fresh.DATA) for p in (fresh.PLAN, fresh.PLAN_BAK, fresh.LOG, fresh.NET_FILE, fresh.LISTEN_DIR))
      and all(p.startswith(fresh.BASE) for p in (fresh.PAGE, fresh.ICON, fresh.VENDOR_FILE)))
check('app: janela propria, sem abas', pl.app_window_command('x.exe', 'http://127.0.0.1:8765/')[:2] == ['x.exe', '--app=http://127.0.0.1:8765/'])
fake_root = tempfile.mkdtemp(prefix='painel-navegador-')
fake_edge = os.path.join(fake_root, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
os.makedirs(os.path.dirname(fake_edge))
open(fake_edge, 'wb').close()
found = pl.browser_candidates({'ProgramFiles(x86)': fake_root, 'ProgramFiles': os.path.join(fake_root, 'nao-existe')})
check('app: acha o Edge e ignora caminhos que nao existem', fake_edge in found and all(os.path.isfile(f) for f in found) and len({f.lower() for f in found}) == len(found), found)
shutil.rmtree(fake_root, ignore_errors=True)
check('app: encerra so depois do tempo sem janela', not pl.idle_too_long(100, 50, 60, False) and pl.idle_too_long(100, 30, 60, False))
check('app: nao encerra no meio de uma procura ou escuta', not pl.idle_too_long(1000, 0, 60, True))
check('app: tempo zero desliga o encerramento', not pl.idle_too_long(10 ** 9, 0, 0, False))


def answer_with(head):
    def on_conn(c):
        try:
            c.settimeout(2)
            c.recv(500)
            c.sendall(head)
        except OSError:
            pass
        c.close()
    return on_conn


twins = [serve(2441, answer_with(b'HTTP/1.0 403 Forbidden\r\nServer: PainelVLANs/1.0.0 Python/3.12.10\r\n\r\n')),
         serve(2442, answer_with(b'HTTP/1.0 200 OK\r\nServer: nginx\r\n\r\n'))]
check('app: reconhece outra copia do painel aberta', pl.running_panel(2441))
check('app: outro programa na porta nao e o painel', not pl.running_panel(2442) and not pl.running_panel(2499))
for s in twins:
    s.close()

# ---- HTTP layer, in a throwaway copy so the real plano.json is never touched
tmp = tempfile.mkdtemp(prefix='painel-teste-')
shutil.copy(APP, tmp)
shutil.copy(os.path.join(os.path.dirname(APP), 'painel-vlans.html'), tmp)
with open(os.path.join(tmp, 'painel.ico'), 'wb') as f:
    f.write(b'\x00\x00\x01\x00icone de teste')
data_dir = os.path.join(tmp, 'dados do usuario')
HP = 8791
proc = subprocess.Popen([sys.executable, os.path.join(tmp, 'painel_local.py'), '--porta', str(HP), '--sem-navegador'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        env=dict(os.environ, PAINEL_DADOS=data_dir))
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
    check('app: o plano vai para a pasta de dados', os.path.exists(os.path.join(data_dir, 'plano.json')) and not os.path.exists(os.path.join(tmp, 'plano.json')))
    code, r = call('GET', '/api/ping', token=token)
    check('app: a janela avisa que continua aberta', code == 200 and r.get('ok') is True and r.get('version') == pl.VERSION, r)
    check('app: sem o codigo da pagina, o aviso e recusado', call('GET', '/api/ping')[0] == 403)
    req = urllib.request.Request(base + '/favicon.ico')
    with urllib.request.urlopen(req, timeout=10) as resp:
        check('app: o icone da janela vem do programa', resp.status == 200 and resp.read().startswith(b'\x00\x00\x01\x00'))
    check('app: o programa se identifica para outra copia', pl.running_panel(HP))
    t0 = time.time()
    second = subprocess.run([sys.executable, os.path.join(tmp, 'painel_local.py'), '--porta', str(HP), '--sem-navegador'],
                            capture_output=True, timeout=60, env=dict(os.environ, PAINEL_DADOS=data_dir))
    said = second.stdout.decode('utf-8', 'replace')
    check('app: a segunda copia usa a que ja esta aberta e sai', second.returncode == 0 and 'já está aberto' in said and str(HP) in said and time.time() - t0 < 20, said)
    check('app: a segunda copia nao abriu outra porta', not pl.running_panel(HP + 1))
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
    hist0 = open(os.path.join(data_dir, 'historico.log'), encoding='utf-8').read()
    check('historico: registra o terminal sem a senha', 'terminal' in hist0 and '(senha omitida)' in hist0 and 'Segredo#9aB' not in hist0)
    hist = open(os.path.join(data_dir, 'historico.log'), encoding='utf-8').read()
    check('historico: registra os blocos', 'Criar as VLANs  OK' in hist and 'PAROU' in hist)
    check('historico: sem senha', PW not in hist and 'errada' not in hist)
    check('app: o historico vai para a pasta de dados', not os.path.exists(os.path.join(tmp, 'historico.log')))
    saved = open(os.path.join(data_dir, 'plano.json'), encoding='utf-8').read()
    check('plano.json: sem senha', PW not in saved)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)

# ---- closing by itself: alive while a window checks in, gone once none does
tmp2 = tempfile.mkdtemp(prefix='painel-sozinho-')
shutil.copy(APP, tmp2)
shutil.copy(os.path.join(os.path.dirname(APP), 'painel-vlans.html'), tmp2)
P2 = 8797
lone = subprocess.Popen([sys.executable, os.path.join(tmp2, 'painel_local.py'), '--porta', str(P2), '--sem-navegador', '--encerrar-sozinho', '6'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ, PAINEL_DADOS=os.path.join(tmp2, 'dados')))
try:
    for _ in range(40):
        if pl.running_panel(P2):
            break
        time.sleep(0.25)
    page = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/' % P2), timeout=10).read().decode('utf-8')
    tok = json.loads(re.search(r'window\.PAINEL_LOCAL=(\{.*?\});', page).group(1))['token']
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/favicon.ico' % P2), timeout=10) as resp:
        check('app: sem icone, a janela usa o padrao do navegador', resp.status == 204)
    for _ in range(6):
        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/api/ping' % P2, headers={'X-Painel-Token': tok}), timeout=10).read()
        time.sleep(2)
    check('app: continua aberto enquanto a janela avisa', lone.poll() is None)
    t0 = time.time()
    try:
        lone.wait(timeout=25)
    except subprocess.TimeoutExpired:
        pass
    check('app: encerra sozinho depois que a janela some', lone.poll() == 0 and time.time() - t0 < 20, (lone.poll(), round(time.time() - t0, 1)))
    out = lone.stdout.read().decode('utf-8', 'replace') if lone.poll() is not None else ''
    check('app: avisa por que encerrou', 'Encerrando' in out, out[-300:])
finally:
    if lone.poll() is None:
        lone.kill()
    shutil.rmtree(tmp2, ignore_errors=True)

print()
print('TODOS OS TESTES PASSARAM' if not fails else 'FALHAS: %d -> %s' % (len(fails), fails))
sys.exit(1 if fails else 0)
