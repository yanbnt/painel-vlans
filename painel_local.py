#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel de VLANs - programa local.

Abre o painel numa janela propria deste computador, guarda o plano em plano.json
e aplica blocos de comandos no switch por Telnet. Cada bloco so e enviado
depois de uma confirmacao na tela, e o programa nunca grava a configuracao
do switch por conta propria.

Uso:  python painel_local.py [--porta 8765] [--sem-navegador] [--encerrar-sozinho SEGUNDOS]

Instalado (PainelVLANs.exe), os dados ficam em %LOCALAPPDATA%\\Painel de VLANs e o
programa encerra sozinho alguns minutos depois que a janela do painel e fechada.
"""
import argparse
import base64
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urljoin, urlsplit

VERSION = '1.0.0'


def app_dirs(frozen, bundle, here, env):
    """(folder with the program's own files, folder for the user's data).

    Run as a script, both are the script's folder, as before. Installed (frozen by PyInstaller), the program's
    files are read-only and the data goes to the user's own folder, so it survives updates and uninstalling.
    PAINEL_DADOS chooses another data folder (the tests use it)."""
    base = (bundle or here) if frozen else here
    default = os.path.join(env.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Painel de VLANs') if frozen else here
    return base, env.get('PAINEL_DADOS') or default


FROZEN = bool(getattr(sys, 'frozen', False))
BASE, DATA = app_dirs(FROZEN, getattr(sys, '_MEIPASS', ''),
                      os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__)), os.environ)
PAGE = os.path.join(BASE, 'painel-vlans.html')
ICON = os.path.join(BASE, 'painel.ico')
PLAN = os.path.join(DATA, 'plano.json')
PLAN_BAK = os.path.join(DATA, 'plano.anterior.json')
LOG = os.path.join(DATA, 'historico.log')
TOKEN = secrets.token_urlsafe(24)
PORT = 8765
SEEN = {'at': time.time()}          # last time an open panel window talked to this program
IDLE_INSTALLED = 180                # installed: close this long after the last panel window went away

# ---------------------------------------------------------------- Telnet

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
PROMPT_END = re.compile(r'(?:^|\n)[ \t]*([<\[][\w.\-/:+ ]{1,80}[>\]])[ \t]*$', re.A)
PROMPT_ANY = re.compile(r'^[ \t]*[<\[][\w.\-/:+ ]{1,80}[>\]]', re.A | re.M)
LOGIN = re.compile(r'(?:login|username|user name)\s*:\s*$', re.I)
PASSWD = re.compile(r'password\s*:\s*$', re.I)
YESNO = re.compile(r'\[\s*y(?:es)?\s*/\s*n(?:o)?\s*\]\s*[:?]?\s*$', re.I)
ERRLINE = re.compile(r'^\s*%\s+\S')
BADWORD = re.compile(r'\b(?:error|fail(?:ed|ure)?|denied|invalid|incorrect|cannot|conflict)\b'
                     r'|not (?:exist|allowed|supported|permitted)', re.I)
ARPLINE = re.compile(r'^\s*(\d+\.\d+\.\d+\.\d+)\s+[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}\s+(\S+)\s+(\S+)')
SECRET = re.compile(r'(?:password|confirm)\s*:\s*$', re.I)
ASK = re.compile(r'[:?]\s*$')

# Free terminal: commands that can take the network or the access down ask for a confirmation first.
RISKY = (
    (('reboot',), 'Reinicia o switch. O que não foi salvo se perde e a rede ligada nele cai por alguns minutos.'),
    (('reset', 'saved-configuration'), 'Apaga a configuração gravada. No próximo reinício, o switch volta ao padrão de fábrica.'),
    (('restore', 'factory-default'), 'Restaura o padrão de fábrica.'),
    (('format',), 'Apaga todos os arquivos do switch.'),
    (('delete',), 'Apaga arquivos do switch.'),
    (('save',), 'Grava a configuração atual. Depois disso, reiniciar o switch não desfaz mais as alterações.'),
    (('startup', 'saved-configuration'), 'Troca o arquivo de configuração usado na inicialização.'),
    (('boot-loader',), 'Mexe no sistema usado na inicialização do switch.'),
    (('undo', 'vlan'), 'Apaga a VLAN. As portas de acesso dela voltam para a VLAN 1.'),
    (('undo', 'interface'), 'Apaga a interface e o que estiver configurado nela.'),
    (('undo', 'telnet', 'server'), 'Desliga o Telnet. Se o SSH não estiver funcionando, você fica sem acesso remoto.'),
    (('undo', 'ssh', 'server'), 'Desliga o SSH. Se você entra por ele, fica sem acesso remoto.'),
    (('protocol', 'inbound'), 'Muda por onde o acesso remoto é aceito. Você pode ficar sem acesso.'),
    (('authentication-mode',), 'Muda como o acesso remoto confere quem entra. Você pode ficar sem acesso.'),
    (('undo', 'local-user'), 'Apaga um usuário do switch.'),
    (('undo', 'ip', 'address'), 'Remove o IP desta interface. Se for o IP por onde você está conectado, a conexão cai.'),
    (('ip', 'address'), 'Troca o IP desta interface. Se for o IP por onde você está conectado, a conexão cai.'),
    (('shutdown',), 'Desliga esta porta. Quem estiver ligado nela fica sem rede.'),
)

# Only these commands are ever sent to the switch. Anything else is refused
# before a single byte goes out, whatever the page asks for.
ALLOWED = [re.compile(p + r'\Z', re.A) for p in (
    r'system-view', r'return', r'quit',
    r'vlan \d{1,4}', r'name [\w.\-/]{1,32}', r'description [\w .\-/]{1,80}', r'undo description',
    r'interface [A-Za-z][A-Za-z\-]*\d+(?:/\d+){1,3}',
    r'port link-type (?:access|trunk)', r'port access vlan \d{1,4}',
    r'port trunk permit vlan \d{1,4}(?: \d{1,4}){0,9}', r'undo port trunk permit vlan 1',
    r'port trunk pvid vlan \d{1,4}', r'shutdown', r'undo shutdown',
    r'display vlan brief', r'display port trunk', r'display interface brief',
    r'save force',
)]


# The live lists only ever ask the switch for these read-only listings.
SHOW = {
    'interfaces': 'display interface brief',
    'vlans': 'display vlan brief',
    'trunks': 'display port trunk',
    'mac': 'display mac-address',
    'arp': 'display arp',
    'ipif': 'display ip interface brief',
    'routes': 'display ip routing-table',
    'version': 'display version',
}


class SwitchError(Exception):
    """A problem worth showing to the person at the screen."""


def short_if(name):
    """GigabitEthernet1/0/5 and GE1/0/5 name the same port; compare them in one form."""
    n = name.strip()
    for full, ab in (('Ten-GigabitEthernet', 'XGE'), ('GigabitEthernet', 'GE'),
                     ('Bridge-Aggregation', 'BAGG'), ('Ethernet', 'Eth')):
        if n.lower().startswith(full.lower()):
            return (ab + n[len(full):]).lower()
    return n.lower()


def clean(text):
    text = ANSI.sub('', text).replace('\r', '').replace('\x00', '').replace('\x07', '')
    while '\x08' in text:
        new = re.sub('[^\x08\n]\x08', '', text, count=1)
        text = new if new != text else text.replace('\x08', '', 1)
    return text


def tail(text, n=160):
    text = ' '.join(clean(text).split())
    return text[-n:] if text else '(nada)'


class SwitchSession:
    def __init__(self):
        self.sock = None
        self.host = ''
        self.port = 23
        self.name = ''
        self.my_port = ''
        self.my_vlan = ''
        self.prompt = ''
        self.at_prompt = True
        self.secret_next = False
        self._raw = b''

    @property
    def connected(self):
        return self.sock is not None

    def status(self):
        return {'connected': self.connected, 'host': self.host if self.connected else '',
                'name': self.name if self.connected else '',
                'myPort': self.my_port if self.connected else '',
                'myVlan': self.my_vlan if self.connected else '',
                'prompt': self.prompt if self.connected else ''}

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self._raw = b''
        self.prompt = ''
        self.at_prompt = True
        self.secret_next = False

    def _note_prompt(self, p):
        """Remember the prompt; the device name is whatever is not the view suffix."""
        self.prompt = p
        inner = p[1:-1]
        if p.startswith('<') or not self.name or not (inner == self.name or inner.startswith(self.name + '-')):
            self.name = inner

    def current_view(self):
        """'GigabitEthernet1/0/5' while configuring that port, '' in user or system view."""
        inner = self.prompt[1:-1] if self.prompt.startswith('[') else ''
        return inner[len(self.name) + 1:] if self.name and inner.startswith(self.name + '-') else ''

    def show(self, command):
        """Run one read-only display command and hand back what the switch printed."""
        if not self.at_prompt:
            raise SwitchError('O switch está esperando uma resposta no Terminal. Responda lá antes.')
        self._send(command)
        text, k = self.read_until([PROMPT_END], 40)
        if k < 0:
            raise SwitchError('O switch não respondeu a tempo.')
        self._note_prompt(PROMPT_END.search(text).group(1))
        return self._tidy(text, (command,))

    def send_line(self, line):
        """Free terminal: one line out, whatever the switch answers back in. The view is kept between calls."""
        secret = self.secret_next
        self._send(line)
        slow = line.strip().lower()[:3] in ('sav', 'reb')
        text, k = self.read_until([YESNO, PROMPT_END, ASK], 150 if slow else 10)
        if k == 1:
            self._note_prompt(PROMPT_END.search(text).group(1))
        self.at_prompt = k == 1
        self.secret_next = k != 1 and bool(SECRET.search(text))
        return {'out': self._tidy(text, () if secret else (line.strip(),)),
                'prompt': self.prompt if k == 1 else '',
                'state': 'prompt' if k == 1 else ('ask' if k >= 0 else 'wait'),
                'secret': self.secret_next}

    # -- bytes in, text out; every Telnet option is politely refused
    def _recv(self, wait):
        self.sock.settimeout(wait)
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return ''
        except OSError as e:
            self.close()
            raise SwitchError('A conexão com o switch caiu (%s).' % e)
        if not data:
            self.close()
            raise SwitchError('O switch fechou a conexão.')
        data = self._raw + data
        self._raw = b''
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            b = data[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= n:
                self._raw = data[i:]
                break
            c = data[i + 1]
            if c in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    self._raw = data[i:]
                    break
                opt = data[i + 2]
                if c == DO:
                    self.sock.sendall(bytes((IAC, WONT, opt)))
                elif c == WILL:
                    self.sock.sendall(bytes((IAC, DONT, opt)))
                i += 3
            elif c == SB:
                j = data.find(bytes((IAC, SE)), i + 2)
                if j < 0:
                    self._raw = data[i:]
                    break
                i = j + 2
            elif c == IAC:
                out.append(IAC)
                i += 2
            else:
                i += 2
        return out.decode('utf-8', 'replace')

    def _send(self, line):
        try:
            self.sock.sendall(line.encode('utf-8', 'replace') + b'\r\n')
        except OSError as e:
            self.close()
            raise SwitchError('A conexão com o switch caiu (%s).' % e)

    def read_until(self, patterns, timeout, quiet=0.2, prompts=1):
        """Read until the text ends in one of `patterns` and the line stays quiet.

        Returns (clean text, index of the pattern), index -1 on timeout.
        `prompts` asks for that many prompts in the text first, for commands sent in pairs.
        """
        text = ''
        end = time.time() + timeout
        while True:
            chunk = self._recv(0.05)
            if chunk:
                text += chunk
                if time.time() > end + 60:
                    return clean(text), -1
                continue
            c = clean(text)
            hit = -1
            for k, p in enumerate(patterns):
                if p.search(c):
                    hit = k
                    break
            if hit >= 0 and (patterns[hit] is not PROMPT_END or len(PROMPT_ANY.findall(c)) >= prompts):
                # A line that merely ends in ":" may be the middle of a listing; wait longer before calling it a question.
                more = self._recv(0.6 if patterns[hit] is ASK else quiet)
                if more:
                    text += more
                    continue
                return c, hit
            if time.time() > end:
                return c, -1

    def connect(self, host, port, user, password):
        self.close()
        try:
            self.sock = socket.create_connection((host, port), timeout=8)
        except OSError as e:
            self.sock = None
            raise SwitchError('Não foi possível conectar em %s, porta %s (%s).' % (host, port, e))
        self.host, self.port, self.name, self.my_port, self.my_vlan = host, port, '', '', ''
        try:
            text, k = self.read_until([LOGIN, PASSWD, PROMPT_END], 12)
            if k < 0:
                raise SwitchError('O switch não mostrou a tela de login. Última resposta: ' + tail(text))
            if k == 0:
                self._send(user)
                text, k2 = self.read_until([PASSWD, PROMPT_END], 10)
                if k2 < 0:
                    raise SwitchError('O switch não pediu a senha. Última resposta: ' + tail(text))
                k = 1 if k2 == 0 else 2
            if k == 1:
                self._send(password)
                text, k3 = self.read_until([YESNO, PROMPT_END, LOGIN, PASSWD], 12)
                if k3 == 0:
                    raise SwitchError('O switch pediu uma confirmação logo no login. Entre uma vez pelo PuTTY, '
                                      'responda o que ele pede e tente de novo. Mensagem: ' + tail(text))
                if k3 != 1:
                    raise SwitchError('Usuário ou senha não aceitos pelo switch.')
            m = PROMPT_END.search(text)
            if m:
                self._note_prompt(m.group(1))
            self._send('screen-length disable')
            self.read_until([PROMPT_END], 8)
            self._find_my_port()
        except SwitchError:
            self.close()
            raise
        return self.status()

    def _find_my_port(self):
        """Which switch port does this computer arrive on? Best effort; empty when unknown."""
        try:
            my_ip = self.sock.getsockname()[0]
            self._send('display arp ' + my_ip)
            text, k = self.read_until([PROMPT_END], 8)
            if k < 0:
                return
            for line in text.split('\n'):
                m = ARPLINE.match(line)
                if m and m.group(1) == my_ip:
                    self.my_vlan = m.group(2) if m.group(2).isdigit() else ''
                    self.my_port = short_if(m.group(3))
                    return
        except SwitchError:
            raise
        except Exception:
            return

    def to_user_view(self):
        """Ctrl+Z leaves any configuration view without changing anything."""
        try:
            self.sock.sendall(b'\x1a')
        except OSError as e:
            self.close()
            raise SwitchError('A conexão com o switch caiu (%s).' % e)
        text, k = self.read_until([PROMPT_END], 3)
        if k < 0:
            self._send('')
            text, k = self.read_until([PROMPT_END], 6)
        if k < 0:
            self.close()
            raise SwitchError('O switch parou de responder. Conecte de novo.')
        m = PROMPT_END.search(text)
        if m and m.group(1).startswith('['):
            self._send('return')
            text, k = self.read_until([PROMPT_END], 6)
            m = PROMPT_END.search(text)
        if m:
            self._note_prompt(m.group(1))
        self.at_prompt = True
        self.secret_next = False

    @staticmethod
    def _tidy(text, sent):
        keep = []
        for line in text.split('\n'):
            m = PROMPT_ANY.match(line)
            rest = (line[m.end():] if m else line).strip()
            if not rest or rest in sent:
                continue
            keep.append(line.rstrip() if not m else rest)
        return '\n'.join(keep)

    def run(self, lines):
        """Send the block one command at a time and stop at the first refusal."""
        results, ok = [], True
        self.to_user_view()
        cmds = [l.strip() for l in lines if l.strip()]
        i = 0
        while i < len(cmds):
            cmd = cmds[i]
            pair = cmd == 'shutdown' and i + 1 < len(cmds) and cmds[i + 1] == 'undo shutdown'
            if pair:
                # Both in one packet, so the port comes back even if the path to the switch blinks.
                try:
                    self.sock.sendall(b'shutdown\r\nundo shutdown\r\n')
                except OSError as e:
                    self.close()
                    raise SwitchError('A conexão com o switch caiu (%s).' % e)
                text, k = self.read_until([YESNO, PROMPT_END], 25, quiet=0.6, prompts=2)
                sent, label = ('shutdown', 'undo shutdown'), 'shutdown + undo shutdown'
                i += 2
            else:
                self._send(cmd)
                text, k = self.read_until([YESNO, PROMPT_END], 150 if cmd.startswith('save') else 25)
                sent, label = (cmd,), cmd
                i += 1
            out = self._tidy(text, sent)
            bad = None
            if k < 0:
                bad = 'O switch não respondeu a tempo.'
            elif k == 0:
                bad = 'O switch pediu uma confirmação. O painel respondeu "não".'
                self._send('n')
                self.read_until([PROMPT_END], 8)
            elif any(ERRLINE.match(l) for l in out.split('\n')):
                bad = 'O switch recusou este comando.'
            elif not cmd.startswith('display') and BADWORD.search(out):
                bad = 'O switch respondeu com um aviso.'
            results.append({'cmd': label, 'out': out, 'bad': bad})
            if bad:
                ok = False
                break
        if self.connected:
            try:
                self.to_user_view()
            except SwitchError:
                pass
        return ok, results


def check_lines(lines):
    """Every line must be one of the commands the panel generates."""
    if not isinstance(lines, list) or not lines or len(lines) > 600:
        return 'Bloco de comandos vazio ou grande demais.'
    for raw in lines:
        if not isinstance(raw, str):
            return 'Bloco de comandos inválido.'
        c = raw.strip()
        if not c:
            continue
        if not any(p.match(c) for p in ALLOWED):
            return 'Comando fora da lista permitida: "%s". Nada foi enviado ao switch.' % c[:80]
    return None


def abbr(tokens, pattern, first=3):
    """True when the typed tokens are the pattern, or an abbreviation of it (Comware accepts prefixes)."""
    if len(tokens) < len(pattern) or len(tokens[0]) < min(first, len(pattern[0])):
        return False
    return all(tokens[i] and pattern[i].startswith(tokens[i]) for i in range(len(pattern)))


def term_risk(line, view, my_port):
    """Why a line typed in the free terminal deserves a confirmation, or None."""
    t = line.strip().lower().split()
    if not t:
        return None
    safe = any(abbr(t, p, 2) for p in (('display',), ('quit',), ('return',), ('description',)))
    if my_port and view and short_if(view) == my_port and not safe:
        return ('Você está configurando a porta %s, por onde este computador chega ao switch. '
                'Este comando pode derrubar a sua conexão.' % my_port.upper())
    for pattern, why in RISKY:
        if abbr(t, pattern, 2 if pattern[0] == 'ip' else 3):
            return why
    return None


def cuts_my_port(lines, my_port, my_vlan):
    """True when the block would cut the port this computer reaches the switch through."""
    if not my_port:
        return False
    v = str(my_vlan or '1')
    sections, cur = {}, None
    for raw in lines:
        c = raw.strip()
        if c.startswith('interface '):
            cur = short_if(c.split(None, 1)[1])
            sections.setdefault(cur, [])
        elif c in ('quit', 'return'):
            cur = None
        elif cur is not None:
            sections[cur].append(c)
    cmds = sections.get(my_port)
    if not cmds:
        return False
    if 'shutdown' in cmds:
        return True
    acc = [c.split()[-1] for c in cmds if c.startswith('port access vlan ')]
    if acc and acc[-1] != v:
        return True
    if 'port link-type access' in cmds and not acc and v != '1':
        return True
    pv = [c.split()[-1] for c in cmds if c.startswith('port trunk pvid vlan ')]
    if 'port link-type trunk' in cmds:
        permits = set()
        for c in cmds:
            if c.startswith('port trunk permit vlan '):
                permits.update(c.split()[4:])
        if 'undo port trunk permit vlan 1' in cmds:
            permits.discard('1')
        else:
            permits.add('1')
        return (pv[-1] if pv else '1') != v or v not in permits
    if pv and pv[-1] != v:
        return True
    return v == '1' and 'undo port trunk permit vlan 1' in cmds


# ---------------------------------------------------------------- plan file and history

def load_plan():
    try:
        with open(PLAN, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {'vlans': [], 'switches': [], 'ports': {}, 'plan': {'steps': {}}}


def save_plan(d):
    ok = (isinstance(d, dict) and isinstance(d.get('vlans'), list) and isinstance(d.get('switches'), list)
          and isinstance(d.get('ports'), dict) and isinstance(d.get('plan'), dict))
    if not ok:
        raise ValueError('plano inválido')
    tmp = PLAN + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'vlans': d['vlans'], 'switches': d['switches'], 'ports': d['ports'], 'plan': d['plan']},
                  f, ensure_ascii=False, indent=1)
    if os.path.exists(PLAN):
        os.replace(PLAN, PLAN_BAK)
    os.replace(tmp, PLAN)


def log(text):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(text.rstrip() + '\n')
    except OSError:
        pass


def stamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------- this computer's network adapters
#
# A device fresh from the factory answers on its own subnet. The panel reaches it by adding a
# second, temporary IPv4 address to one adapter: no gateway, nothing removed, gone at the next
# restart (netsh store=active). Windows asks the person for permission (UAC) on every change.

IS_WIN = os.name == 'nt'
NET_FILE = os.path.join(DATA, 'ips-temporarios.json')
NO_WINDOW = 0x08000000 if IS_WIN else 0
PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
UAC_DECLINED = 1223
LIST_PS = (
    "$a = Get-NetAdapter | Where-Object { $_.Status -ne 'Not Present' } | ForEach-Object { $i = $_.ifIndex; "
    "[pscustomobject]@{ index = $i; name = $_.Name; desc = $_.InterfaceDescription; status = [string]$_.Status; mac = $_.MacAddress; "
    "dhcp = ((Get-NetIPInterface -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue).Dhcp -eq 'Enabled'); "
    "ips = @(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "
    "[pscustomobject]@{ ip = $_.IPAddress; prefix = [int]$_.PrefixLength; state = [string]$_.AddressState } }); "
    "gateways = @(Get-NetRoute -InterfaceIndex $i -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | ForEach-Object { $_.NextHop }) } }; "
    "ConvertTo-Json -InputObject @($a) -Depth 4 -Compress"
)


class NetError(Exception):
    pass


def powershell(command, timeout=40):
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')


def run_elevated(script, timeout=240):
    """Run a short PowerShell script as administrator. Windows shows its own permission prompt."""
    b64 = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    code, _, _ = powershell(
        "try { $p = Start-Process -FilePath powershell -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','%s' "
        "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode } catch { exit %d }" % (b64, UAC_DECLINED), timeout=timeout)
    return code


def load_added():
    try:
        with open(NET_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return [x for x in d if isinstance(x, dict) and 'index' in x and 'ip' in x] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_added(items):
    try:
        with open(NET_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def net_list():
    if not IS_WIN:
        return {'supported': False, 'adapters': []}
    try:
        code, out, err = powershell(LIST_PS)
        data = json.loads(out.strip() or '[]')
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise NetError('Não foi possível ler as placas de rede deste computador (%s).' % e)
    if isinstance(data, dict):
        data = [data]
    added = load_added()
    adapters, still = [], []
    for a in data:
        if not isinstance(a, dict) or a.get('index') is None:
            continue
        ips = []
        for i in a.get('ips') or []:
            mine = any(x['index'] == a['index'] and x['ip'] == i.get('ip') for x in added)
            if mine:
                still.append({'index': a['index'], 'name': a.get('name') or '', 'ip': i.get('ip')})
            ips.append({'ip': i.get('ip'), 'prefix': i.get('prefix'), 'state': i.get('state') or '', 'mine': mine})
        adapters.append({'index': a['index'], 'name': a.get('name') or '', 'desc': a.get('desc') or '',
                         'status': a.get('status') or '', 'mac': a.get('mac') or '', 'dhcp': bool(a.get('dhcp')),
                         'ips': ips, 'gateways': [g for g in (a.get('gateways') or []) if g]})
    if len(still) != len(added):
        save_added(still)
    return {'supported': True, 'adapters': adapters}


def net_check_add(adapters, index, ip, prefix):
    """The adapter and the reason not to add the address (None when it is fine)."""
    ad = next((a for a in adapters if a['index'] == index), None)
    if ad is None:
        return None, 'Placa de rede não encontrada. Atualize a lista.'
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return ad, 'Escreva o endereço como 192.168.0.250.'
    if not any(addr in n for n in PRIVATE):
        return ad, 'Use um endereço de rede interna, como 192.168.0.250.'
    if not isinstance(prefix, int) or not 8 <= prefix <= 30:
        return ad, 'Tamanho de rede inválido.'
    net = ipaddress.IPv4Network('%s/%d' % (ip, prefix), strict=False)
    if addr in (net.network_address, net.broadcast_address):
        return ad, 'Este endereço é o primeiro ou o último da faixa e não pode ser usado.'
    for a in adapters:
        for i in a['ips']:
            if i['ip'] == ip:
                return ad, 'Este computador já tem o endereço %s, na placa %s.' % (ip, a['name'])
    for i in ad['ips']:
        try:
            if ipaddress.IPv4Address(i['ip']) in net:
                return ad, ('A placa %s já tem um endereço nessa faixa (%s). Não é preciso somar outro.' % (ad['name'], i['ip']))
        except ValueError:
            pass
    return ad, None


def net_add_script(ad, ip, prefix):
    mask = str(ipaddress.IPv4Network('%s/%d' % (ip, prefix), strict=False).netmask)
    lines = []
    if ad['dhcp']:
        # Lets a manual address live beside the automatic one instead of replacing it.
        lines.append('netsh interface ipv4 set interface interface=%d dhcpstaticipcoexistence=enabled | Out-Null' % ad['index'])
    lines.append('netsh interface ipv4 add address name=%d address=%s mask=%s store=active | Out-Null' % (ad['index'], ip, mask))
    lines.append('exit $LASTEXITCODE')
    return lines


def net_add(index, ip, prefix, dry=False):
    adapters = net_list()['adapters']
    ad, why = net_check_add(adapters, index, ip, prefix)
    if why:
        raise NetError(why)
    lines = net_add_script(ad, ip, prefix)
    if dry:
        return {'dry': True, 'commands': lines[:-1]}
    code = run_elevated('\n'.join(lines))
    if code == UAC_DECLINED:
        raise NetError('A alteração não foi autorizada na janela do Windows. Nada mudou.')
    now = net_list()
    found = None
    for a in now['adapters']:
        if a['index'] == index:
            found = next((i for i in a['ips'] if i['ip'] == ip), None)
    if found is None:
        raise NetError('O Windows não aceitou o endereço (código %s). Nada mudou.' % code)
    save_added(load_added() + [{'index': index, 'name': ad['name'], 'ip': ip}])
    now = net_list()
    log('[%s] placa %s  somado o IP temporário %s/%d' % (stamp(), ad['name'], ip, prefix))
    if found.get('state') == 'Duplicate':
        now['warning'] = ('O Windows avisou que %s já está em uso por outro equipamento nessa rede. '
                          'Remova este endereço e escolha outro.' % ip)
    return now


def net_remove(index, ip):
    if not any(x['index'] == index and x['ip'] == ip for x in load_added()):
        raise NetError('O painel só remove os endereços que ele mesmo somou.')
    code = run_elevated('netsh interface ipv4 delete address name=%d address=%s store=active | Out-Null\nexit $LASTEXITCODE' % (index, ip))
    if code == UAC_DECLINED:
        raise NetError('A alteração não foi autorizada na janela do Windows. Nada mudou.')
    now = net_list()
    if any(i['ip'] == ip for a in now['adapters'] if a['index'] == index for i in a['ips']):
        raise NetError('O Windows não removeu o endereço (código %s).' % code)
    log('[%s] placa %s  removido o IP temporário %s' % (stamp(), index, ip))
    return now


def net_probe(ip):
    """Does anything answer on that address, and with which MAC? Read-only."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        raise NetError('Endereço IP inválido.')
    if not any(addr in n for n in PRIVATE):
        raise NetError('O teste só vale para endereços de rede interna.')
    if not IS_WIN:
        return {'reachable': False, 'mac': ''}
    try:
        r = subprocess.run(['ping', '-n', '2', '-w', '800', ip], capture_output=True, timeout=15, creationflags=NO_WINDOW)
        reachable = b'TTL=' in r.stdout.upper()
        a = subprocess.run(['arp', '-a', ip], capture_output=True, timeout=10, creationflags=NO_WINDOW)
        m = re.search(r'([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})', a.stdout.decode('ascii', 'replace'))
    except (OSError, subprocess.SubprocessError):
        return {'reachable': False, 'mac': ''}
    return {'reachable': reachable, 'mac': m.group(1).upper() if m and reachable else ''}


# ---------------------------------------------------------------- discovery
#
# Finds what is plugged into the same cabling, whatever address it uses, as long as this
# computer has an address in that range. For each range: touch every address so Windows asks
# "who has it?" on the wire, then read the answers from the ARP table. A device that blocks
# ping still answers that question. Ranges this computer is not in are reached by adding
# temporary addresses first (the same mechanism as above, one permission prompt for all).

SCAN_PORTS = (23, 22, 80, 443, 8291, 37777, 554, 445, 9100, 53)
ARP_IF = re.compile(r'^\S+:\s+(\d+\.\d+\.\d+\.\d+)\s+---')
ARP_ROW = re.compile(r'^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s')
SCAN_LOCK = threading.Lock()


def arp_table():
    r = subprocess.run(['arp', '-a'], capture_output=True, timeout=30, creationflags=NO_WINDOW)
    rows, cur = [], ''
    for line in r.stdout.decode('cp850', 'replace').splitlines():
        m = ARP_IF.match(line)
        if m:
            cur = m.group(1)
            continue
        m = ARP_ROW.match(line)
        if m:
            mac = m.group(2).upper()
            if mac != 'FF-FF-FF-FF-FF-FF' and not mac.startswith(('01-00-5E', '33-33', '01-80-C2')):
                rows.append((cur, m.group(1), mac))
    return rows


def touch_range(src_ip, net):
    """One tiny UDP datagram per address, sent from src_ip: enough to make Windows resolve each one."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((src_ip, 0))
    except OSError:
        return
    hosts = [str(h) for h in net.hosts()][:1022]
    for _ in range(2):
        for h in hosts:
            if h != src_ip:
                try:
                    s.sendto(b'\x00', (h, 9))
                except OSError:
                    pass
        time.sleep(0.6)
    s.close()


def tcp_probe(ip, port, timeout=0.7, hold=1.4):
    """Is a real service listening there? Returns (state, first bytes); state is 'open', 'dropped' or 'closed'.

    Some devices (seen on printers) accept a connection on any port and drop it right away or about
    a second later. So a port only counts as open when the connection stays up for `hold` seconds,
    or when the device speaks first."""
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        return 'closed', b''
    try:
        s.settimeout(hold)
        data = s.recv(200)
        return ('open', data) if data else ('dropped', b'')
    except socket.timeout:
        return 'open', b''
    except OSError:
        return 'dropped', b''
    finally:
        s.close()


def http_get(ip, tls, path='/', port=None):
    """(headers, start of the body) of one page of the device's own web screen; ('', '') if it does not answer HTTP."""
    raw, data = None, b''
    try:
        raw = socket.create_connection((ip, port or (443 if tls else 80)), timeout=2.5)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                ctx.set_ciphers('DEFAULT:@SECLEVEL=0')
            except ssl.SSLError:
                pass
            raw = ctx.wrap_socket(raw)
        raw.settimeout(2.5)
        raw.sendall(('GET %s HTTP/1.0\r\nHost: %s\r\nAccept: */*\r\n\r\n' % (path, ip)).encode('ascii'))
        while len(data) < 16384:
            chunk = raw.recv(4096)
            if not chunk:
                break
            data += chunk
    except (OSError, ValueError):
        pass
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass
    text = data.decode('utf-8', 'replace')
    if not text.startswith('HTTP/'):
        return '', ''
    parts = re.split(r'\r?\n\r?\n', text, 1)
    return parts[0], parts[1] if len(parts) > 1 else ''


def next_page(url, ip, tls, path, port):
    """Where a redirect leads, as (tls, path, port), and only when it stays on the same device."""
    try:
        u = urlsplit(urljoin('%s://%s:%d%s' % ('https' if tls else 'http', ip, port, path), html.unescape(url)))
        to = u.port or (443 if u.scheme == 'https' else 80)
    except ValueError:
        return None
    if u.scheme not in ('http', 'https') or u.hostname != ip:
        return None
    if (u.scheme == 'https') != tls and to != (443 if u.scheme == 'https' else 80):
        return None
    if (u.scheme == 'https') == tls and to != port:
        return None
    p = (u.path or '/') + ('?' + u.query if u.query else '')
    if not re.match(r'^/[\x21-\x7e]{0,300}$', p):
        return None
    return u.scheme == 'https', p, to


def http_info(ip, tls, port=None):
    """What the device's own web screen says it is: {'ok', 'title', 'realm', 'server', 'words'}.

    Follows only what a browser follows by itself when the page is opened (redirects and
    automatic refreshes), and never leaves the device."""
    out = {'ok': False, 'title': '', 'realm': '', 'server': '', 'words': ''}
    path, port, seen = '/', port or (443 if tls else 80), set()
    for _ in range(3):
        seen.add((tls, path, port))
        head, body = http_get(ip, tls, path, port)
        if not head:
            break
        out['ok'] = True
        m = re.search(r'^Server:(.*)$', head, re.I | re.M)
        if m and m.group(1).strip():
            out['server'] = m.group(1).strip()[:60]
        m = re.search(r'^WWW-Authenticate:.*?realm="([^"]+)"', head, re.I | re.M)
        if m and m.group(1).strip():
            out['realm'] = m.group(1).strip()[:60]      # the name on the password prompt, often the model
        m = re.search(r'<title[^>]*>(.*?)</title>', body, re.I | re.S)
        title = ' '.join(html.unescape(m.group(1)).replace('\ufeff', ' ').split())[:80] if m else ''
        if re.match(r'\d{3}\b', title):
            title = ''                                  # "302 Found", "401 Unauthorized": a status, not a name
        if not title:
            title = script_title(body) or page_model(body)
        m = re.search(r'^Location:[ \t]*(\S+)', head, re.I | re.M) if re.match(r'HTTP/\S+\s+3\d\d\b', head) else None
        if not m and not title:
            m = re.search(r'<meta[^>]+http-equiv\s*=\s*["\']?refresh["\']?[^>]*?url\s*=\s*["\']?([^"\'\s>]+)', body, re.I)
        step = next_page(m.group(1), ip, tls, path, port) if m else None
        if step and step not in seen:
            tls, path, port = step
            continue
        if title:
            out['title'] = title
        else:
            text = re.sub(r'<(script|style)\b.*?</\1\s*>|<[^>]*>', ' ', body, flags=re.I | re.S)
            out['words'] = ' '.join(html.unescape(text).replace('\ufeff', ' ').split())[:60]
        break
    return out


def script_title(body):
    """A page title that the page sets by script, as some switches do: document.title = "Model", or = a variable."""
    m = re.search(r'document\.title\s*=\s*(?:["\']([^"\'<>]{2,60})["\']|([A-Za-z_]\w{0,30})\s*;)', body)
    if m and not m.group(1):
        m = re.search(r'\b%s\s*=\s*["\']([^"\'<>]{2,60})["\']' % re.escape(m.group(2)), body)
    return ' '.join(html.unescape(m.group(1)).split())[:80] if m else ''


def page_model(body):
    """The model an Intelbras switch of the SF/SG line writes on its untitled login page, e.g. "<strong>SF</strong> 2842MR"."""
    if 'intelbras' not in body.lower():
        return ''
    m = re.search(r'\b(S[GF]) ?(\d{4}) ?(MR|PoE)?\b', re.sub(r'<[^>]*>', '', body))
    return ' '.join(x for x in m.groups() if x) if m else ''


# ---- asking each device who it is: standard questions, each answered without any password

VENDOR_FILE = os.path.join(BASE, 'fabricantes.tsv')
VENDORS = None
SNMP_OIDS = (b'\x2b\x06\x01\x02\x01\x01\x01\x00', b'\x2b\x06\x01\x02\x01\x01\x05\x00')      # what it is, its name


def vendor_of(mac):
    """The manufacturer that owns the MAC prefix, from the public IEEE list ('' for a randomised address)."""
    global VENDORS
    if VENDORS is None:
        table = {}
        try:
            with open(VENDOR_FILE, encoding='utf-8') as f:
                for line in f:
                    prefix, _, name = line.rstrip('\r\n').partition('\t')
                    if len(prefix) == 6 and name:
                        table[prefix] = name
        except OSError:
            pass
        VENDORS = table
    m = re.sub(r'[^0-9A-Fa-f]', '', mac or '').upper()
    if len(m) != 12 or m[1] in '26AE':
        return ''
    return VENDORS.get(m[:6], '')


def udp_ask(ip, port, payload, timeout=1.0, tries=2, size=16384):
    """One question over UDP and the device's answer (b'' when nobody answers)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return b''
    try:
        s.settimeout(timeout)
        for _ in range(tries):
            try:
                s.sendto(payload, (ip, port))
                data, peer = s.recvfrom(size)
                if peer[0] == ip:
                    return data
            except socket.timeout:
                continue
            except OSError:
                return b''            # the device answered "nothing listens on this port"
        return b''
    finally:
        s.close()


def netbios_name(ip, port=137):
    """(computer name, workgroup) the way Windows machines and many printers announce them."""
    q = struct.pack('>HHHHHH', 0x5056, 0, 1, 0, 0, 0) + b'\x20CK' + b'A' * 30 + b'\x00' + struct.pack('>HH', 0x21, 1)
    d = udp_ask(ip, port, q)
    if len(d) < 57 or d[:2] != q[:2]:
        return '', ''
    name = group = ''
    for i in range(d[56]):
        e = d[57 + i * 18:75 + i * 18]
        if len(e) < 18:
            break
        if e[15] == 0x00:
            text = ''.join(c if c.isprintable() else ' ' for c in e[:15].decode('cp850', 'replace')).strip()
            if struct.unpack('>H', e[16:18])[0] & 0x8000:
                group = group or text
            else:
                name = name or text
    return name, group


def ber(tag, value):
    n = len(value)
    return bytes([tag]) + (bytes([n]) if n < 128 else bytes([0x81, n]) if n < 256 else bytes([0x82, n >> 8, n & 255])) + value


def snmp_info(ip, port=161):
    """(what the device says it is, its name), by the standard read-only question with the usual community "public"."""
    binds = b''.join(ber(0x30, ber(0x06, o) + b'\x05\x00') for o in SNMP_OIDS)
    pdu = ber(0xA0, ber(0x02, b'\x12\x34\x56\x78') + b'\x02\x01\x00\x02\x01\x00' + ber(0x30, binds))
    d = udp_ask(ip, port, ber(0x30, b'\x02\x01\x01' + ber(0x04, b'public') + pdu))
    out = []
    for o in SNMP_OIDS:
        i, text = d.find(b'\x06' + bytes([len(o)]) + o), ''
        j = i + 2 + len(o)
        if i >= 0 and j + 2 <= len(d) and d[j] == 0x04:
            n, k = d[j + 1], j + 2
            if n & 0x80:
                c = n & 0x7F
                n, k = int.from_bytes(d[k:k + c], 'big'), k + c
            text = wire_text(d[k:k + n], 160)
        out.append(text)
    return out[0], out[1]


def snmp_model(descr):
    """The product name inside an SNMP description, which every maker writes its own way."""
    m = re.search(r'Release \S+\s+(.+?)\s+Copyright', descr)            # "... Release 6368 INTELBRAS S2352G-A Copyright ..."
    if m:
        return m.group(1)[:50]
    return re.split(r'\s+/\s+|\s+version\s+|,\s*Software|\s+Copyright|;|\s+-\s+', descr, maxsplit=1, flags=re.I)[0].strip()[:50]


def dahua_info(ip, port=37810):
    """{'model', 'klass'} of an Intelbras or Dahua camera, recorder or smart switch, by their own discovery question."""
    body = b'{ "method" : "DHDiscover.search", "params" : { "mac" : "", "uni" : 1 } }\n'
    head = b'\x20\x00\x00\x00DHIP' + b'\x00' * 8 + struct.pack('<I', len(body)) + b'\x00' * 4 + struct.pack('<I', len(body)) + b'\x00' * 4
    d = udp_ask(ip, port, head + body)
    i = d.find(b'{')
    if i < 0:
        return {}
    try:
        info = json.JSONDecoder().raw_decode(d[i:].decode('utf-8', 'replace'))[0]['params']['deviceInfo']
        return {'model': wire_text(str(info.get('DeviceType') or '').encode(), 40), 'klass': wire_text(str(info.get('DeviceClass') or '').encode(), 20)}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def onvif_info(ip, port=3702):
    """(hardware, name) from the standard discovery question of video devices (ONVIF)."""
    u = secrets.token_hex(16)
    probe = ('<?xml version="1.0" encoding="UTF-8"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
             'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
             'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:%s-%s-%s-%s-%s</w:MessageID>'
             '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
             '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>'
             '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'
             % (u[:8], u[8:12], u[12:16], u[16:20], u[20:])).encode('ascii')
    d = udp_ask(ip, port, probe).decode('utf-8', 'replace')
    found = {k: unquote(v) for k, v in re.findall(r'onvif://www\.onvif\.org/(name|hardware)/([^\s<"]+)', d)}
    return wire_text(found.get('hardware', '').replace('_', ' ').encode(), 40), wire_text(found.get('name', '').replace('_', ' ').encode(), 40)


def upnp_info(ip, port=1900):
    """(name, maker, model) from the description a device publishes about itself (TVs, routers, media boxes)."""
    q = ('M-SEARCH * HTTP/1.1\r\nHOST: %s:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: upnp:rootdevice\r\n\r\n' % ip).encode('ascii')
    d = udp_ask(ip, port, q, tries=1).decode('utf-8', 'replace')
    m = re.search(r'^LOCATION:[ \t]*http://([0-9.]+):(\d{1,5})(/[\x21-\x7e]{0,300})\s*$', d, re.I | re.M)
    if not m or m.group(1) != ip or not 0 < int(m.group(2)) < 65536:
        return '', '', ''
    _, body = http_get(ip, False, m.group(3), int(m.group(2)))

    def tag(name):
        t = re.search(r'<%s>([^<]{1,80})</%s>' % (name, name), body)
        return wire_text(html.unescape(t.group(1)).encode(), 60) if t else ''
    return tag('friendlyName'), tag('manufacturer'), tag('modelName')


def telnet_banner(ip):
    s = SwitchSession()
    try:
        s.sock = socket.create_connection((ip, 23), timeout=2)
        text, _ = s.read_until([LOGIN, PASSWD, PROMPT_END], 2.5)
    except (OSError, SwitchError):
        return ''
    finally:
        s.close()
    lines = [l.strip() for l in text.split('\n') if l.strip() and not set(l.strip()) <= set('*-=#')]
    return ' '.join(lines)[-70:] if lines else ''


def classify(h, gateways):
    """A best guess of what the device is, from what it answered. Shown as "tipo provável"."""
    p = set(h['ports'])
    t = ' '.join(h.get(k) or '' for k in ('title', 'realm', 'server')).lower()
    b, s, mac = (h.get('banner') or '').lower(), (h.get('ssh') or '').lower(), h.get('mac') or ''
    name, pc, klass = (h.get('name') or '').lower(), h.get('pc') or '', (h.get('klass') or '').upper()
    said = ' '.join(h.get(k) or '' for k in ('model', 'descr')).lower()       # what the device answered about itself
    if h['ip'] in gateways:
        return 'Roteador (gateway)'
    if 8291 in p or 'mikrotik' in b or 'mikrotik' in t or 'routeros' in t + said or 'rosssh' in s:
        return 'MikroTik'
    if (klass == 'TS' or 'switch' in t or 'managerment' in t or 'comware' in s
            or re.search(r'switch|platform software|\bs[gf] ?\d{4}\b|\bs\d{4}g\b', t + ' ' + said)):
        return 'Switch'
    if klass in ('BSC', 'ASI', 'ASA', 'ASC', 'VTO', 'VTH') or 'access control' in t or 'controle de acesso' in t or re.search(r'\bxpe\b', said):
        return 'Controle de acesso'
    if klass in ('NVR', 'DVR', 'XVR', 'HCVR', 'HDVR', 'MHDX') or re.search(r'\b(i?mhdx|i?nvd|nvr|dvr|xvr)\b', said):
        return 'Gravador de vídeo'
    if klass == 'MATRIX':
        return 'Decodificador de vídeo'
    if klass.startswith(('IPC', 'TPC', 'SD', 'PTZ')) or re.search(r'\bvip[- ]\w', said):
        return 'Câmera'
    if klass or 37777 in p:
        return 'Câmera ou gravador'
    if 445 in p and pc:
        return 'Computador'                       # a Windows machine that also shares a printer is still a computer
    if 9100 in p or 'printer' in said:
        return 'Impressora'
    if re.search(r'\btv\b', name + ' ' + said):
        return 'TV'
    if 23 in p and 'login' in b and 445 not in p:
        return 'Switch ou roteador'
    if 53 in p and 445 not in p and (80 in p or 443 in p):
        return 'Roteador ou AP Wi-Fi'
    if 554 in p:
        return 'Câmera'
    if 445 in p or pc:
        return 'Computador'                       # it answered with a Windows-style name
    if 80 in p or 443 in p:
        return 'Equipamento com tela web'
    if 23 in p or 22 in p:
        return 'Equipamento de rede'
    if not p and len(mac) > 1 and mac[1] in '26AEae':
        return 'Celular ou notebook (Wi-Fi)'      # a randomised address: what phones and laptops use on Wi-Fi
    return ''


def identify(hosts, gateways, ex, ports=SCAN_PORTS, web=((80, False), (443, True)), ask=True):
    """Fills in, for every host: the ports with a real service, what it says about itself, and the best guess.

    With ask, each device also gets the standard "who are you?" questions (name, model), none of which needs a password."""
    jobs = {(h['ip'], p): ex.submit(tcp_probe, h['ip'], p) for h in hosts for p in ports}
    names = {h['ip']: ex.submit(netbios_name, h['ip']) for h in hosts} if ask else {}
    snmps = {h['ip']: ex.submit(snmp_info, h['ip']) for h in hosts} if ask else {}
    upnps = {h['ip']: ex.submit(upnp_info, h['ip']) for h in hosts} if ask else {}
    state = {k: j.result() for k, j in jobs.items()}
    pages, banners, cams, onvifs = {}, {}, {}, {}
    for h in hosts:
        ip = h['ip']
        alive = [(p, tls) for p, tls in web if p in ports and state[(ip, p)][0] != 'closed']
        # The first web port gives the name; a web port that dropped the idle connection still counts if it answers a page.
        for i, (p, tls) in enumerate(alive):
            if i == 0 or state[(ip, p)][0] == 'dropped':
                pages[(ip, p)] = ex.submit(http_info, ip, tls, p)
        if 23 in ports and state[(ip, 23)][0] == 'open':
            banners[ip] = ex.submit(telnet_banner, ip)
        if ask and any(p in ports and state[(ip, p)][0] == 'open' for p in (37777, 554)):
            cams[ip] = ex.submit(dahua_info, ip)
            onvifs[ip] = ex.submit(onvif_info, ip)
    for h in hosts:
        ip = h['ip']
        info = [pages[(ip, p)].result() for p, _ in web if (ip, p) in pages]
        answered = {p for p, _ in web if (ip, p) in pages and pages[(ip, p)].result()['ok']}
        h['ports'] = [p for p in ports if state[(ip, p)][0] == 'open' or (state[(ip, p)][0] == 'dropped' and p in answered)]
        for key in ('title', 'realm', 'server', 'words'):
            h[key] = next((i[key] for i in info if i[key]), '')
        first = state[(ip, 22)][1] if 22 in ports else b''
        h['ssh'] = first.decode('latin-1').split('\n')[0].strip()[:60] if first.startswith(b'SSH-') else ''
        h['banner'] = banners[ip].result() if ip in banners else ''
        pc, group = names[ip].result() if ip in names else ('', '')
        descr, sysname = snmps[ip].result() if ip in snmps else ('', '')
        friendly, _, upnp_model = upnps[ip].result() if ip in upnps else ('', '', '')
        cam = cams[ip].result() if ip in cams else {}
        hardware = onvifs[ip].result()[0] if ip in onvifs else ''
        h['pc'], h['name'], h['group'], h['descr'] = pc, pc or friendly or sysname, group, descr
        h['model'] = cam.get('model') or hardware or upnp_model or snmp_model(descr)
        h['klass'] = cam.get('klass', '')
        h['vendor'] = vendor_of(h.get('mac'))
        h['kind'] = classify(h, gateways)


def scan_targets(adapters):
    """(source address, range, is it a range reached through a temporary address?) for every usable address."""
    out, seen = [], set()
    for a in adapters:
        if a['status'] != 'Up':
            continue
        for i in a['ips']:
            try:
                addr = ipaddress.IPv4Address(i['ip'])
                prefix = int(i['prefix'])
            except (ValueError, TypeError):
                continue
            if not any(addr in n for n in PRIVATE) or i.get('state') == 'Duplicate':
                continue
            net = ipaddress.IPv4Network('%s/%d' % (i['ip'], max(prefix, 24)), strict=False)
            if str(net) not in seen:
                seen.add(str(net))
                out.append((i['ip'], net, bool(i.get('mine'))))
    return out


def scan_network(dry=False):
    data = net_list()
    if not data['supported']:
        raise NetError('A procura só funciona no Windows.')
    targets = scan_targets(data['adapters'])
    if not targets:
        raise NetError('Este computador não tem nenhuma placa de rede ligada com endereço de rede interna.')
    nets = [{'net': str(n), 'from': src, 'outside': outside} for src, n, outside in targets]
    if dry:
        return {'dry': True, 'nets': nets, 'hosts': []}
    mine = {i['ip'] for a in data['adapters'] for i in a['ips']}
    gateways = {g for a in data['adapters'] for g in a['gateways']}
    found = {}
    with SCAN_LOCK:
        for src, net, outside in targets:
            touch_range(src, net)
            time.sleep(0.8)
            for _, ip, mac in arp_table():
                try:
                    if ipaddress.IPv4Address(ip) in net and ip not in mine:
                        found[ip] = {'ip': ip, 'mac': mac, 'net': str(net), 'outside': outside}
                except ValueError:
                    pass
        hosts = list(found.values())
        with ThreadPoolExecutor(max_workers=96) as ex:
            identify(hosts, gateways, ex)
    hosts.sort(key=lambda h: tuple(int(x) for x in h['ip'].split('.')))
    log('[%s] procura na rede  %d equipamentos em %s' % (stamp(), len(hosts), ', '.join(n['net'] for n in nets)))
    return {'hosts': hosts, 'nets': nets, 'adapters': data['adapters']}


def factory_plan(adapters, index, ranges):
    """Which temporary addresses to add so this computer can see the given factory ranges."""
    ad = next((a for a in adapters if a['index'] == index), None)
    if ad is None:
        raise NetError('Placa de rede não encontrada. Atualize a lista.')
    if not isinstance(ranges, list) or not ranges or len(ranges) > 16:
        raise NetError('Escolha de 1 a 16 faixas.')
    have = [ipaddress.IPv4Address(i['ip']) for a in adapters if a['status'] == 'Up' for i in a['ips']
            if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', i['ip'] or '')]
    plan = []
    for r in ranges:
        if not isinstance(r, str) or not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}$', r):
            raise NetError('Faixa inválida: %s.' % str(r)[:20])
        try:
            net = ipaddress.IPv4Network(r + '.0/24')
        except ValueError:
            raise NetError('Faixa inválida: %s.' % r)
        if not any(net.subnet_of(n) for n in PRIVATE):
            raise NetError('A faixa %s não é de rede interna.' % r)
        if not any(h in net for h in have):
            plan.append(r + '.249')
    return ad, plan


def scan_prepare(index, ranges, dry=False):
    adapters = net_list()['adapters']
    ad, plan = factory_plan(adapters, index, ranges)
    lines = ['$f = 0']
    if ad['dhcp'] and plan:
        lines.append('netsh interface ipv4 set interface interface=%d dhcpstaticipcoexistence=enabled | Out-Null' % ad['index'])
    for ip in plan:
        lines.append('netsh interface ipv4 add address name=%d address=%s mask=255.255.255.0 store=active | Out-Null; if ($LASTEXITCODE -ne 0) { $f++ }' % (ad['index'], ip))
    lines.append('exit $f')
    if dry:
        return {'dry': True, 'add': plan, 'commands': lines[1:-1]}
    if plan:
        code = run_elevated('\n'.join(lines))
        if code == UAC_DECLINED:
            raise NetError('A alteração não foi autorizada na janela do Windows. Nada mudou.')
        time.sleep(3.5)
        now = net_list()
        got = [ip for ip in plan if any(i['ip'] == ip for a in now['adapters'] if a['index'] == index for i in a['ips'])]
        save_added(load_added() + [{'index': index, 'name': ad['name'], 'ip': ip} for ip in got])
        log('[%s] placa %s  somados os IPs temporários %s' % (stamp(), ad['name'], ', '.join(got) or 'nenhum'))
    now = net_list()
    now['added'] = plan
    return now


def net_remove_all():
    items = load_added()
    if not items:
        return net_list()
    lines = ['netsh interface ipv4 delete address name=%d address=%s store=active | Out-Null' % (x['index'], x['ip'])
             for x in items if isinstance(x.get('index'), int) and re.match(r'^\d+\.\d+\.\d+\.\d+$', str(x.get('ip')))]
    code = run_elevated('\n'.join(lines + ['exit 0']))
    if code == UAC_DECLINED:
        raise NetError('A alteração não foi autorizada na janela do Windows. Nada mudou.')
    log('[%s] removidos os IPs temporários %s' % (stamp(), ', '.join(x['ip'] for x in items)))
    return net_list()


# ---------------------------------------------------------------- listening on the wire
#
# Asking only finds devices in ranges this computer has an address in. Listening finds the rest:
# every device shouts "who has this address?" (ARP) now and then, switches announce themselves
# (LLDP, CDP, MNDP, spanning tree) and devices without an address call for one (DHCP). Windows
# ships a capture tool, pktmon. It needs administrator rights, so it runs through the same
# permission prompt as the address changes. Only those announcements are captured, never ordinary
# traffic, and the capture file is deleted as soon as it has been read.

LISTEN_DIR = os.path.join(DATA, 'escuta')
LISTEN_KEPT = 'escuta-sem-leitura.pcapng'
LISTEN_LOCK = threading.Lock()
LISTEN_FILTERS = (('painel-arp', '-d', 'ARP'), ('painel-lldp', '-d', '0x88CC'), ('painel-difusao', '-m', 'FF-FF-FF-FF-FF-FF'),
                  ('painel-stp', '-m', '01-80-C2-00-00-00'), ('painel-cdp', '-m', '01-00-0C-CC-CC-CC'),
                  ('painel-ssdp', '-m', '01-00-5E-7F-FF-FA'), ('painel-mdns', '-m', '01-00-5E-00-00-FB'))
LISTEN_WHY = {4: 'O capturador do Windows (pktmon) não aceitou os filtros.',
              5: 'O capturador do Windows (pktmon) não começou a gravar. Ele pode já estar em uso neste computador.',
              6: 'O capturador do Windows (pktmon) não entregou a gravação.'}
LINK_LOCAL = ipaddress.ip_network('169.254.0.0/16')
UDP_NAMES = {67: 'DHCP', 68: 'DHCP', 5678: 'MNDP', 1900: 'SSDP', 5353: 'mDNS', 137: 'NetBIOS', 138: 'NetBIOS',
             5355: 'LLMNR', 3702: 'WS-Discovery', 37810: 'Dahua', 10001: 'Ubiquiti'}


def mac_text(b):
    return '-'.join('%02X' % x for x in b)


def ip_text(b):
    return '.'.join(str(x) for x in b)


def wire_text(b, n=60):
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in b.decode('utf-8', 'replace')).split())[:n]


def pcapng_frames(path, limit=300000):
    """The Ethernet frames stored in a pcapng capture file."""
    with open(path, 'rb') as f:
        data = f.read(96 * 1024 * 1024)
    out, links, order, pos = [], [], '<', 0
    while pos + 12 <= len(data) and len(out) < limit:
        if data[pos:pos + 4] == b'\x0a\x0d\x0d\x0a':            # a new section says its own byte order
            magic = data[pos + 8:pos + 12]
            if magic == b'\x4d\x3c\x2b\x1a':
                order = '<'
            elif magic == b'\x1a\x2b\x3c\x4d':
                order = '>'
            else:
                break
            links = []
        kind, size = struct.unpack(order + 'II', data[pos:pos + 8])
        if size < 12 or size % 4 or pos + size > len(data):
            break
        body = data[pos + 8:pos + size - 4]
        if kind == 1 and len(body) >= 4:                        # interface description: its link type
            links.append(struct.unpack(order + 'H', body[:2])[0])
        elif kind == 6 and len(body) >= 20:                     # one captured packet
            iface, _, _, cap, _ = struct.unpack(order + 'IIIII', body[:20])
            if iface < len(links) and links[iface] == 1 and 14 <= cap <= len(body) - 20:
                out.append(body[20:20 + cap])
        pos += size
    return out


def lldp_info(p):
    """A switch (or phone, access point) introducing itself to whatever is plugged into it."""
    out = {'proto': 'LLDP', 'mac': '', 'name': '', 'port': '', 'desc': '', 'ip': '', 'vlan': '', 'caps': ''}
    alias, i = '', 0
    while i + 2 <= len(p):
        t, n = p[i] >> 1, ((p[i] & 1) << 8) | p[i + 1]
        v = p[i + 2:i + 2 + n]
        i += 2 + n
        if t == 0:
            break
        if t == 1 and v and v[0] == 4 and len(v) >= 7:
            out['mac'] = mac_text(v[1:7])
        elif t == 2 and v:
            out['port'] = mac_text(v[1:7]) if v[0] == 3 and len(v) >= 7 else wire_text(v[1:], 40)
        elif t == 4:
            alias = wire_text(v, 40)
        elif t == 5:
            out['name'] = wire_text(v)
        elif t == 6:
            out['desc'] = wire_text(v, 120)
        elif t == 7 and len(v) >= 4:
            on = int.from_bytes(v[2:4], 'big')
            out['caps'] = ', '.join(name for bit, name in ((0x04, 'switch'), (0x10, 'roteador'), (0x08, 'Wi-Fi'), (0x20, 'telefone')) if on & bit)
        elif t == 8 and len(v) >= 6 and v[0] == 5 and v[1] == 1:
            out['ip'] = ip_text(v[2:6])
        elif t == 127 and len(v) >= 6 and v[:4] == b'\x00\x80\xc2\x01':
            out['vlan'] = str(int.from_bytes(v[4:6], 'big'))
    if alias and (not out['port'] or re.match(r'^([0-9A-F]{2}-){5}[0-9A-F]{2}$', out['port'])):
        out['port'] = alias
    return out


def cdp_info(p):
    out = {'proto': 'CDP', 'mac': '', 'name': '', 'port': '', 'desc': '', 'ip': '', 'vlan': '', 'caps': ''}
    i = 4
    while i + 4 <= len(p):
        t, n = struct.unpack('>HH', p[i:i + 4])
        if n < 4:
            break
        v = p[i + 4:i + n]
        i += n
        if t == 1:
            out['name'] = wire_text(v)
        elif t == 3:
            out['port'] = wire_text(v, 40)
        elif t == 6:
            out['desc'] = wire_text(v, 120)
        elif t == 10 and len(v) >= 2:
            out['vlan'] = str(int.from_bytes(v[:2], 'big'))
        elif t == 4 and len(v) >= 4:
            on = int.from_bytes(v[:4], 'big')
            out['caps'] = ', '.join(name for bit, name in ((0x08, 'switch'), (0x01, 'roteador'), (0x80, 'telefone')) if on & bit)
        elif t == 2 and len(v) >= 13 and v[4:8] == b'\x01\x01\xcc\x00' and v[8] == 4:
            out['ip'] = ip_text(v[9:13])
    return out


def mndp_info(p):
    """A MikroTik announcing itself (the list WinBox shows under Neighbors)."""
    out = {'proto': 'MNDP', 'mac': '', 'name': '', 'port': '', 'desc': '', 'ip': '', 'vlan': '', 'caps': ''}
    parts, i = {}, 4
    while i + 4 <= len(p):
        t, n = struct.unpack('>HH', p[i:i + 4])
        v = p[i + 4:i + 4 + n]
        i += 4 + n
        if t == 1 and len(v) == 6:
            out['mac'] = mac_text(v)
        elif t == 5:
            out['name'] = wire_text(v)
        elif t == 16:
            out['port'] = wire_text(v, 40)
        elif t == 17 and len(v) == 4:
            out['ip'] = ip_text(v)
        elif t in (7, 8, 12):
            parts[t] = wire_text(v, 40)
    out['desc'] = ' '.join(parts[k] for k in (8, 12, 7) if parts.get(k))
    return out


def stp_info(p):
    """A spanning tree hello: which switch sent it and which switch it considers the root."""
    if len(p) < 35 or p[0:2] != b'\x00\x00' or p[3] == 0x80:
        return None
    root, bridge = p[5:13], p[17:25]
    return {'proto': {0: 'STP', 2: 'RSTP', 3: 'MSTP'}.get(p[2], 'STP'), 'mac': mac_text(bridge[2:]), 'name': '',
            'port': str(int.from_bytes(p[25:27], 'big') & 0x0FFF), 'desc': '', 'ip': '', 'vlan': '', 'caps': 'switch',
            'root': mac_text(root[2:]), 'isroot': root == bridge}


def dhcp_info(p):
    if len(p) < 240 or p[236:240] != b'\x63\x82\x53\x63':
        return None
    out = {'op': p[0], 'mac': mac_text(p[28:34]), 'yiaddr': ip_text(p[16:20]), 'type': 0, 'name': '', 'vendor': '',
           'want': '', 'server': '', 'router': '', 'mask': ''}
    i = 240
    while i < len(p) and p[i] != 255:
        if p[i] == 0:
            i += 1
            continue
        if i + 1 >= len(p):
            break
        c, n = p[i], p[i + 1]
        v = p[i + 2:i + 2 + n]
        i += 2 + n
        if c == 53 and v:
            out['type'] = v[0]
        elif c == 12:
            out['name'] = wire_text(v, 40)
        elif c == 60:
            out['vendor'] = wire_text(v, 40)
        elif c == 50 and len(v) == 4:
            out['want'] = ip_text(v)
        elif c == 54 and len(v) == 4:
            out['server'] = ip_text(v)
        elif c == 3 and len(v) >= 4:
            out['router'] = ip_text(v[:4])
        elif c == 1 and len(v) == 4:
            out['mask'] = ip_text(v)
    return out


def analyse(frames, nets, mine):
    """What the captured announcements say: who is on the wire, repeated addresses, switches nearby, DHCP servers.

    nets are the ranges this computer has an address in; mine are its own MAC addresses."""
    mine = {str(m).upper().replace(':', '-') for m in mine}
    hosts, claims, neigh, servers, labels, calling = {}, {}, {}, {}, {}, {}

    def host(ip, mac, via, vlan=''):
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            return None
        if mac in mine or mac.startswith('FF-FF') or not (any(addr in n for n in PRIVATE) or addr in LINK_LOCAL):
            return None
        h = hosts.setdefault((ip, mac), {'ip': ip, 'mac': mac, 'via': [], 'asks': [], 'name': '', 'vendor': '', 'vlan': vlan})
        if via not in h['via']:
            h['via'].append(via)
        return h

    def one(fr):
        src = mac_text(fr[6:12])
        et, off, vlan = int.from_bytes(fr[12:14], 'big'), 14, ''
        while et in (0x8100, 0x88A8) and len(fr) >= off + 4:
            vlan = vlan or str(int.from_bytes(fr[off:off + 2], 'big') & 0x0FFF)
            et, off = int.from_bytes(fr[off + 2:off + 4], 'big'), off + 4
        p = fr[off:]
        if et == 0x0806 and len(p) >= 28 and p[0:6] == b'\x00\x01\x08\x00\x06\x04':
            sha, spa, tpa = mac_text(p[8:14]), ip_text(p[14:18]), ip_text(p[24:28])
            if spa != '0.0.0.0':
                claims.setdefault(spa, set()).add(sha)
                h = host(spa, sha, 'ARP', vlan)
                if h is not None and p[7] == 1 and tpa != spa and tpa not in h['asks'] and len(h['asks']) < 4:
                    h['asks'].append(tpa)
        elif et == 0x88CC:
            info = lldp_info(p)
            if src not in mine and (info['mac'] or info['name'] or info['port']):
                info['mac'] = info['mac'] or src
                info['vlan'] = info['vlan'] or vlan
                neigh[('LLDP', src)] = info
        elif et <= 1500 and src not in mine:
            if p[0:2] == b'\x42\x42':
                info = stp_info(p[3:])
                if info:
                    neigh[('STP', info['mac'])] = info
            elif p[0:8] == b'\xaa\xaa\x03\x00\x00\x0c\x20\x00':
                info = cdp_info(p[8:])
                info['mac'] = src
                neigh[('CDP', src)] = info
        elif et == 0x0800 and len(p) >= 20:
            ihl, sip = (p[0] & 0x0F) * 4, ip_text(p[12:16])
            udp = p[9] == 17 and not int.from_bytes(p[6:8], 'big') & 0x1FFF and len(p) >= ihl + 8
            sport, dport = struct.unpack('>HH', p[ihl:ihl + 4]) if udp else (0, 0)
            u = p[ihl + 8:] if udp else b''
            if fr[0] & 1 and sip != '0.0.0.0':                 # sent to everybody: the sender is on this wire
                host(sip, src, UDP_NAMES.get(dport) or UDP_NAMES.get(sport) or 'difusão', vlan)
            if udp and (dport in (67, 68) or sport in (67, 68)):
                d = dhcp_info(u)
                if d and d['op'] == 1:
                    labels.setdefault(d['mac'], {}).update({k: d[k] for k in ('name', 'vendor') if d[k]})
                    if d['type'] == 1 and d['mac'] not in mine:
                        calling[d['mac']] = True
                elif d and d['op'] == 2 and src not in mine:
                    ip = d['server'] or sip
                    servers[ip] = {'ip': ip, 'mac': src, 'router': d['router'], 'mask': d['mask']}
            elif udp and 5678 in (sport, dport) and len(u) > 8 and src not in mine:
                info = mndp_info(u)
                info['mac'] = info['mac'] or src
                info['ip'] = info['ip'] or ('' if sip == '0.0.0.0' else sip)
                neigh[('MNDP', src)] = info
            elif udp and dport == 1900:
                m = re.search(rb'^SERVER:[ \t]*([^\r\n]+)', u, re.I | re.M)
                if m:
                    labels.setdefault(src, {}).setdefault('vendor', wire_text(m.group(1), 40))

    for fr in frames:
        try:
            if len(fr) >= 14:
                one(fr)
        except (IndexError, struct.error, ValueError):
            continue

    # One address answering with a second MAC many times is a router answering for others, not a repeated address.
    clash = {}
    for ip, macs in claims.items():
        if len(macs) > 1:
            for m in macs:
                clash[m] = clash.get(m, 0) + 1
    proxies = {m for m, n in clash.items() if n >= 3}
    dups = sorted(({'ip': ip, 'macs': sorted(macs - proxies), 'me': bool(macs & mine)} for ip, macs in claims.items()
                   if len(macs - proxies) > 1), key=lambda d: tuple(int(x) for x in d['ip'].split('.')))
    out = []
    for h in hosts.values():
        addr = ipaddress.IPv4Address(h['ip'])
        home = next((n for n in nets if addr in n), None)
        h['known'] = home is not None
        h['proxy'] = h['mac'] in proxies
        h['maker'] = vendor_of(h['mac'])
        h['net'] =str(home) if home else str(LINK_LOCAL) if addr in LINK_LOCAL else str(ipaddress.IPv4Network(h['ip'] + '/24', strict=False))
        h.update({k: v for k, v in labels.get(h['mac'], {}).items() if v and not h.get(k)})
        out.append(h)
    out.sort(key=lambda h: (tuple(int(x) for x in h['ip'].split('.')), h['mac']))
    known = {h['mac'] for h in out}
    by_mac = {}
    for h in out:
        by_mac.setdefault(h['mac'], h['ip'])
    near = []
    for info in neigh.values():
        info['ip'] = info['ip'] or by_mac.get(info['mac'], '')
        near.append(info)
    near.sort(key=lambda n: (n['proto'], n['mac']))
    waiting = [{'mac': m, 'name': labels.get(m, {}).get('name', ''), 'vendor': labels.get(m, {}).get('vendor', '')}
               for m in sorted(calling) if m not in known]
    return {'frames': len(frames), 'hosts': out, 'dups': dups, 'neighbors': near,
            'dhcp': sorted(servers.values(), key=lambda s: s['ip']), 'waiting': waiting}


def listen_script(folder, seconds):
    """The administrator part of a listening session: record only announcements for a while, then put everything back."""
    filters = ', '.join('@(%s)' % ','.join("'%s'" % x for x in f) for f in LISTEN_FILTERS)
    return '\n'.join([
        "$d = '%s'" % folder.replace("'", "''"),
        "$log = Join-Path $d 'escuta.log'",
        "function Say($t) { ($t | Out-String).Trim() | Add-Content -LiteralPath $log -Encoding UTF8 }",
        "function Run([string[]]$a) { $o = & pktmon @a 2>&1; $c = $LASTEXITCODE; Say ('pktmon ' + ($a -join ' ') + ' -> ' + $c); Say $o; return $c }",
        # A listening session that was cut short (computer shut down in the middle) leaves its own filters behind: clear them.
        "if ((& pktmon filter list 2>&1 | Out-String) -match 'painel-') { Say 'sobra de uma escuta anterior'; [void](Run @('stop')); [void](Run @('filter','remove')) }",
        "$bad = 0",
        "foreach ($f in @(%s)) { if ((Run (@('filter','add') + $f)) -ne 0) { $bad++ } }" % filters,
        "if ($bad) { [void](Run @('filter','remove')); exit 4 }",
        "if ((Run @('start','--capture','--comp','nics','--type','flow','--pkt-size','0','--file-name',(Join-Path $d 'escuta.etl'),'--file-size','16')) -ne 0) "
        "{ [void](Run @('filter','remove')); exit 5 }",
        "& arp -d * 2>&1 | Out-Null",
        "Set-Content -LiteralPath (Join-Path $d 'escuta.on') -Value 'on'",
        "Start-Sleep -Seconds %d" % seconds,
        "[void](Run @('stop'))",
        "$c = Run @('etl2pcap',(Join-Path $d 'escuta.etl'),'--out',(Join-Path $d 'escuta.pcapng'))",
        "[void](Run @('filter','remove'))",
        "if ($c -ne 0) { exit 6 }",
        "exit 0",
    ])


def listen_assist(targets, stop):
    """While the recording runs: ask every known range again and call for MikroTik neighbours, so the answers get recorded."""
    marker = os.path.join(LISTEN_DIR, 'escuta.on')
    while not os.path.exists(marker):
        if stop.wait(0.25):
            return
    time.sleep(0.8)
    for src, _, _ in targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((src, 0))
            s.sendto(b'\x00\x00\x00\x00', ('255.255.255.255', 5678))
            s.close()
        except OSError:
            pass
    for src, net, _ in targets:
        if stop.is_set():
            return
        touch_range(src, net)


def listen_clean(keep_log=True):
    for name in ('escuta.etl', 'escuta.pcapng', 'escuta.on') + (() if keep_log else ('escuta.log', LISTEN_KEPT)):
        try:
            os.remove(os.path.join(LISTEN_DIR, name))
        except OSError:
            pass


def listen(seconds=40, dry=False):
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 15 <= seconds <= 180:
        raise NetError('Escolha um tempo de escuta entre 15 e 180 segundos.')
    data = net_list()
    if not data['supported']:
        raise NetError('A escuta só funciona no Windows.')
    if not any(a['status'] == 'Up' for a in data['adapters']):
        raise NetError('Este computador não tem nenhuma placa de rede com cabo ligado.')
    targets = scan_targets(data['adapters'])
    script = listen_script(LISTEN_DIR, seconds)
    if dry:
        return {'dry': True, 'seconds': seconds, 'commands': script.split('\n')}
    if not LISTEN_LOCK.acquire(blocking=False):
        raise NetError('Já existe uma escuta em andamento. Espere ela terminar.')
    stop = threading.Event()
    try:
        os.makedirs(LISTEN_DIR, exist_ok=True)
        listen_clean(keep_log=False)
        threading.Thread(target=listen_assist, args=(targets, stop), daemon=True).start()
        try:
            code = run_elevated(script, timeout=seconds + 300)
        except subprocess.SubprocessError:
            raise NetError('A autorização do Windows demorou demais e a escuta foi cancelada. Tente de novo.')
        if code == UAC_DECLINED:
            raise NetError('A escuta não foi autorizada na janela do Windows. Nada mudou.')
        pcap = os.path.join(LISTEN_DIR, 'escuta.pcapng')
        if code != 0 or not os.path.exists(pcap):
            try:
                with open(os.path.join(LISTEN_DIR, 'escuta.log'), encoding='utf-8-sig', errors='replace') as f:
                    detail = ' '.join(f.read().split())[-400:]
            except OSError:
                detail = ''
            log('[%s] escuta do cabo  falhou (código %s) %s' % (stamp(), code, detail))
            raise NetError('%s Nada ficou alterado no computador.%s' % (
                LISTEN_WHY.get(code, 'A escuta não terminou (código %s).' % code), ' Detalhe: ' + detail if detail else ''))
        try:
            frames = pcapng_frames(pcap)
        except (OSError, struct.error) as e:
            raise NetError('Não foi possível ler a gravação da escuta (%s).' % e)
        if not frames:
            # A network in use always has announcements, so an empty reading means the file is not in the
            # shape this program expects. Keep it for analysis instead of reporting "nothing found".
            try:
                size = os.path.getsize(pcap)
                os.replace(pcap, os.path.join(LISTEN_DIR, LISTEN_KEPT))
            except OSError:
                size = -1
            log('[%s] escuta do cabo  a gravação veio sem nenhum anúncio legível (%d bytes), guardada em escuta/%s' % (stamp(), size, LISTEN_KEPT))
            raise NetError('O Windows gravou, mas o painel não conseguiu ler nenhum anúncio na gravação. Ela ficou guardada em '
                           'escuta\\%s para análise. Nada ficou alterado no computador.' % LISTEN_KEPT)
        out = analyse(frames, [n for _, n, _ in targets], {a['mac'] for a in data['adapters']})
        out['seconds'] = seconds
        out['nets'] = [str(n) for _, n, _ in targets]
        log('[%s] escuta do cabo  %d s, %d anúncios, %d equipamentos (%d fora das faixas deste computador), %d IPs repetidos, %d vizinhos' % (
            stamp(), seconds, out['frames'], len(out['hosts']), sum(1 for h in out['hosts'] if not h['known']), len(out['dups']), len(out['neighbors'])))
        return out
    finally:
        stop.set()
        listen_clean()
        LISTEN_LOCK.release()


# ---------------------------------------------------------------- local web server

SESSION = SwitchSession()
SESSION_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = 'PainelVLANs/' + VERSION
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _host_ok(self):
        h = (self.headers.get('Host') or '').lower()
        return h in ('127.0.0.1:%d' % PORT, 'localhost:%d' % PORT)

    def _authorized(self):
        if not self._host_ok():
            return False
        origin = self.headers.get('Origin')
        if origin and origin.lower() not in ('http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT):
            return False
        ok = secrets.compare_digest(self.headers.get('X-Painel-Token') or '', TOKEN)
        if ok:
            SEEN['at'] = time.time()
        return ok

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 2000000:
            raise ValueError('corpo grande demais')
        d = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
        if not isinstance(d, dict):
            raise ValueError('corpo inválido')
        return d

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if not self._host_ok():
            return self._json(403, {'error': 'Abra o painel pelo endereço http://127.0.0.1:%d' % PORT})
        if path in ('/', '/index.html'):
            try:
                with open(PAGE, encoding='utf-8') as f:
                    page = f.read()
            except OSError:
                return self._send(500, 'painel-vlans.html não encontrado ao lado do programa.'.encode('utf-8'),
                                  'text/plain; charset=utf-8')
            head = ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                    '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui}img{max-width:100%}'
                    '[hidden]{display:none!important}</style>'
                    '<script>window.PAINEL_LOCAL=' + json.dumps({'token': TOKEN}) + ';</script></head><body>')
            return self._send(200, (head + page + '</body></html>').encode('utf-8'), 'text/html; charset=utf-8')
        if path == '/favicon.ico':
            try:
                with open(ICON, 'rb') as f:
                    return self._send(200, f.read(), 'image/x-icon')
            except OSError:
                return self._send(204, b'', 'image/x-icon')
        if not self._authorized():
            return self._json(403, {'error': 'Acesso negado.'})
        if path == '/api/ping':
            return self._json(200, {'ok': True, 'version': VERSION})
        if path == '/api/plan':
            return self._json(200, load_plan())
        if path == '/api/status':
            return self._json(200, SESSION.status())
        if path == '/api/net':
            try:
                return self._json(200, net_list())
            except NetError as e:
                return self._json(409, {'error': str(e)})
        if path == '/api/log':
            try:
                with open(LOG, encoding='utf-8') as f:
                    lines = f.read().splitlines()[-500:]
            except OSError:
                lines = []
            return self._json(200, {'lines': lines})
        return self._json(404, {'error': 'Não encontrado.'})

    def do_PUT(self):
        if not self._authorized():
            return self._json(403, {'error': 'Acesso negado.'})
        if self.path.split('?', 1)[0] != '/api/plan':
            return self._json(404, {'error': 'Não encontrado.'})
        try:
            save_plan(self._body())
        except (ValueError, OSError) as e:
            return self._json(400, {'error': 'Não foi possível gravar o plano (%s).' % e})
        return self._json(200, {'ok': True})

    def do_POST(self):
        if not self._authorized():
            return self._json(403, {'error': 'Acesso negado.'})
        path = self.path.split('?', 1)[0]
        try:
            body = self._body()
        except ValueError as e:
            return self._json(400, {'error': 'Pedido inválido (%s).' % e})

        if path == '/api/connect':
            host = str(body.get('host') or '').strip()
            user = str(body.get('user') or '')
            password = str(body.get('password') or '')
            try:
                port = int(body.get('port') or 23)
            except (TypeError, ValueError):
                port = 0
            if not re.match(r'^[A-Za-z0-9.\-:]{1,100}$', host) or not 1 <= port <= 65535:
                return self._json(400, {'error': 'Informe o endereço IP do switch e a porta Telnet.'})
            if not user or not password:
                return self._json(400, {'error': 'Informe o usuário e a senha do switch.'})
            with SESSION_LOCK:
                try:
                    st = SESSION.connect(host, port, user, password)
                except SwitchError as e:
                    log('[%s] %s  conexão recusada: %s' % (stamp(), host, e))
                    return self._json(409, {'error': str(e)})
            log('[%s] %s (%s)  conectado' % (stamp(), host, st.get('name') or '?'))
            return self._json(200, st)

        if path == '/api/disconnect':
            with SESSION_LOCK:
                SESSION.close()
            return self._json(200, SESSION.status())

        if path in ('/api/scan', '/api/scan/prepare', '/api/net/remove-all', '/api/listen'):
            try:
                if path == '/api/scan':
                    return self._json(200, scan_network(bool(body.get('dry'))))
                if path == '/api/listen':
                    return self._json(200, listen(body.get('seconds', 40), bool(body.get('dry'))))
                if path == '/api/net/remove-all':
                    return self._json(200, net_remove_all())
                if not isinstance(body.get('index'), int):
                    raise NetError('Escolha a placa de rede.')
                return self._json(200, scan_prepare(body['index'], body.get('ranges'), bool(body.get('dry'))))
            except NetError as e:
                return self._json(409, {'error': str(e)})
            except (OSError, subprocess.SubprocessError) as e:
                return self._json(409, {'error': 'O Windows não respondeu ao pedido (%s).' % e})

        if path in ('/api/net/add', '/api/net/remove', '/api/net/check'):
            ip = str(body.get('ip') or '').strip()
            try:
                if path == '/api/net/check':
                    return self._json(200, net_probe(ip))
                index = body.get('index')
                if not isinstance(index, int):
                    raise NetError('Escolha a placa de rede.')
                if path == '/api/net/remove':
                    return self._json(200, net_remove(index, ip))
                return self._json(200, net_add(index, ip, body.get('prefix'), bool(body.get('dry'))))
            except NetError as e:
                return self._json(409, {'error': str(e)})
            except (OSError, subprocess.SubprocessError) as e:
                return self._json(409, {'error': 'O Windows não respondeu ao pedido (%s).' % e})

        if path == '/api/show':
            command = SHOW.get(str(body.get('what') or ''))
            if not command:
                return self._json(400, {'error': 'Lista desconhecida.'})
            with SESSION_LOCK:
                if not SESSION.connected:
                    return self._json(409, {'error': 'Não há conexão com o switch.', 'lost': True})
                try:
                    out = SESSION.show(command)
                except SwitchError as e:
                    return self._json(409, {'error': str(e), 'lost': not SESSION.connected})
            return self._json(200, {'out': out, 'command': command})

        if path == '/api/term':
            line = body.get('line')
            if not isinstance(line, str) or len(line) > 300 or re.search(r'[\x00-\x1f\x7f]', line):
                return self._json(400, {'error': 'Comando inválido.'})
            with SESSION_LOCK:
                if not SESSION.connected:
                    return self._json(409, {'error': 'Não há conexão com o switch. Conecte de novo.', 'lost': True})
                secret = SESSION.secret_next
                if SESSION.at_prompt and not body.get('confirm'):
                    why = term_risk(line, SESSION.current_view(), SESSION.my_port)
                    if why:
                        return self._json(409, {'error': why, 'needConfirm': why})
                host, name = SESSION.host, SESSION.name
                try:
                    r, error = SESSION.send_line(line), None
                except SwitchError as e:
                    r, error = None, str(e)
                st = SESSION.status()
            entry = ['[%s] %s (%s)  terminal' % (stamp(), host, name or '?'),
                     '    > ' + ('(senha omitida)' if secret else line.strip())]
            if r:
                shown = [l for l in r['out'].split('\n') if l.strip()]
                entry += ['      ' + l for l in shown[:60]]
                if len(shown) > 60:
                    entry.append('      (... mais %d linhas)' % (len(shown) - 60))
            if error:
                entry.append('    !! ' + error)
                log('\n'.join(entry))
                return self._json(409, {'error': error, 'lost': not st['connected'], 'status': st})
            log('\n'.join(entry))
            r['status'] = st
            return self._json(200, r)

        if path == '/api/run':
            lines = body.get('lines')
            label = str(body.get('label') or 'bloco')[:80]
            err = check_lines(lines)
            if err:
                return self._json(409, {'error': err})
            with SESSION_LOCK:
                if not SESSION.connected:
                    return self._json(409, {'error': 'Não há conexão com o switch. Conecte de novo.', 'lost': True})
                if not SESSION.at_prompt:
                    return self._json(409, {'error': 'O switch está esperando uma resposta no Terminal. Responda lá '
                                                     'antes de aplicar um bloco.'})
                if cuts_my_port(lines, SESSION.my_port, SESSION.my_vlan):
                    return self._json(409, {'error': 'Este bloco altera a porta %s, por onde este computador chega ao '
                                                     'switch. Aplicar por aqui derrubaria a sua conexão. Faça essa '
                                                     'porta por último, ligado em outra porta ou pelo cabo console.'
                                                     % SESSION.my_port.upper()})
                host, name = SESSION.host, SESSION.name
                try:
                    ok, results = SESSION.run(lines)
                    error = None
                except SwitchError as e:
                    ok, results, error = False, [], str(e)
                st = SESSION.status()
            entry = ['[%s] %s (%s)  %s  %s' % (stamp(), host, name or '?', label, 'OK' if ok else 'PAROU')]
            for r in results:
                entry.append('    > ' + r['cmd'])
                for l in (r['out'] or '').split('\n'):
                    if l.strip():
                        entry.append('      ' + l)
                if r['bad']:
                    entry.append('      !! ' + r['bad'])
            if error:
                entry.append('    !! ' + error)
            log('\n'.join(entry))
            return self._json(200, {'ok': ok, 'results': results, 'error': error, 'status': st})

        return self._json(404, {'error': 'Não encontrado.'})


# ---------------------------------------------------------------- running as an app

class PanelServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR would let a second copy take over a port that is already serving the panel.
    allow_reuse_address = not IS_WIN


def say(text):
    """A line for whoever started the program; installed, there is no console and it goes to programa.log."""
    if sys.stdout is not None:
        try:
            print(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


def running_panel(port, timeout=1.0):
    """Is a copy of this program already serving the panel on that port?"""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
            s.settimeout(2.0)
            s.sendall(('GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n' % port).encode('ascii'))
            head = s.recv(1024)
    except OSError:
        return False
    return bool(re.search(rb'^Server:[ \t]*PainelVLANs/', head, re.M | re.I))


def browser_candidates(env=None):
    """Edge or Chrome on this computer, best first: they can show the panel in a window of its own, like an app."""
    env = os.environ if env is None else env
    found = []
    if IS_WIN:
        try:
            import winreg
            for exe in ('msedge.exe', 'chrome.exe'):
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + exe) as k:
                            found.append(winreg.QueryValue(k, None))
                    except OSError:
                        pass
        except ImportError:
            pass
    for var, rel in (('ProgramFiles(x86)', 'Microsoft\\Edge\\Application\\msedge.exe'), ('ProgramFiles', 'Microsoft\\Edge\\Application\\msedge.exe'),
                     ('LOCALAPPDATA', 'Google\\Chrome\\Application\\chrome.exe'), ('ProgramFiles', 'Google\\Chrome\\Application\\chrome.exe'),
                     ('ProgramFiles(x86)', 'Google\\Chrome\\Application\\chrome.exe')):
        if env.get(var):
            found.append(os.path.join(env[var], rel))
    out = []
    for f in found:
        f = (f or '').strip().strip('"')
        if f and os.path.isfile(f) and f.lower() not in [o.lower() for o in out]:
            out.append(f)
    return out


def app_window_command(exe, url):
    return [exe, '--app=' + url, '--window-size=1280,860']


def open_app_window(url):
    """The panel in a window of its own (no tabs or address bar); the default browser when Edge and Chrome are missing."""
    for exe in browser_candidates():
        try:
            subprocess.Popen(app_window_command(exe, url), close_fds=True)
            return True
        except OSError:
            continue
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def idle_too_long(now, seen, idle, busy):
    return idle > 0 and not busy and now - seen > idle


def close_when_unused(server, idle):
    """Ends the program once no panel window has talked to it for `idle` seconds (open windows check in every 20 s)."""
    while True:
        time.sleep(max(1.0, min(5.0, idle / 3.0)))
        if idle_too_long(time.time(), SEEN['at'], idle, SCAN_LOCK.locked() or LISTEN_LOCK.locked()):
            say('Nenhuma janela do painel aberta há %d segundos. Encerrando.' % idle)
            server.shutdown()
            return


def main(argv=None):
    global PORT
    ap = argparse.ArgumentParser(description='Painel de VLANs - programa local')
    ap.add_argument('--porta', type=int, default=8765)
    ap.add_argument('--sem-navegador', action='store_true', help='não abre a janela do painel')
    ap.add_argument('--encerrar-sozinho', type=int, default=IDLE_INSTALLED if FROZEN else 0, metavar='SEGUNDOS',
                    help='encerra quando nenhuma janela do painel fala com o programa por esse tempo (0 = nunca)')
    args = ap.parse_args(argv)
    try:
        os.makedirs(DATA, exist_ok=True)
    except OSError as e:
        say('Não foi possível criar a pasta de dados %s (%s).' % (DATA, e))
        return 1
    if FROZEN and sys.stdout is None:
        try:
            out = open(os.path.join(DATA, 'programa.log'), 'a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = out
        except OSError:
            pass
    if not os.path.exists(PAGE):
        say('Nao encontrei painel-vlans.html na mesma pasta deste programa.')
        return 1
    server = None
    for p in range(args.porta, args.porta + 20):
        if running_panel(p):
            url = 'http://127.0.0.1:%d/' % p
            say('O Painel de VLANs já está aberto em ' + url)
            if not args.sem_navegador:
                open_app_window(url)
            return 0
        try:
            server = PanelServer(('127.0.0.1', p), Handler)
            PORT = p
            break
        except OSError:
            continue
    if server is None:
        say('Nenhuma porta livre entre %d e %d.' % (args.porta, args.porta + 19))
        return 1
    url = 'http://127.0.0.1:%d/' % PORT
    SEEN['at'] = time.time()
    say('[%s] Painel de VLANs %s aberto em %s (dados em %s)' % (stamp(), VERSION, url, DATA))
    if args.encerrar_sozinho > 0:
        threading.Thread(target=close_when_unused, args=(server, args.encerrar_sozinho), daemon=True).start()
    else:
        say('Deixe esta janela aberta enquanto usa o painel. Para encerrar, feche a janela ou aperte Ctrl+C.')
    if not args.sem_navegador:
        threading.Timer(0.6, open_app_window, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SESSION.close()
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
