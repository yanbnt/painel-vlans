# Gera as imagens do README em docs/imagens: a capa (desenhada por installer/arte.ps1) e as telas do painel.
#
# As telas vêm do modo demonstração (tools/demonstracao.py), com a rede fictícia: nenhuma foto mostra uma rede
# de verdade. O Edge abre sem janela, num perfil temporário, e é comandado pelo protocolo de depuração dele.
#
# Uso:  python tools/capturar_telas.py
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'docs', 'imagens')
PORT, SWITCH, DEBUG = 8781, 2381, 9333
WIDTH, HEIGHT = 1440, 860


class Tab:
    """Just enough of a WebSocket client to talk to the browser's debugging protocol."""

    def __init__(self, url):
        u = urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        self.sock.sendall(('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (u.path, u.hostname, u.port, key)).encode('ascii'))
        self.buf = bytearray()
        while b'\r\n\r\n' not in self.buf:
            self.buf += self.sock.recv(4096)
        head, _, rest = bytes(self.buf).partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise RuntimeError('O navegador recusou a conexão: %r' % head[:80])
        self.buf = bytearray(rest)
        self.seq = 0

    def _take(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError('o navegador fechou a conexão')
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _send(self, op, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n) if n < 65536
                                     else bytes([0x80 | 127]) + struct.pack('>Q', n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload)))

    def _message(self):
        parts = []
        while True:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._take(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._take(8))[0]
            mask = self._take(4) if b1 & 0x80 else None
            data = self._take(n)
            if mask:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            op = b0 & 0x0F
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op == 0x8:
                raise ConnectionError('o navegador fechou a conexão')
            parts.append(data)
            if b0 & 0x80:
                return json.loads(b''.join(parts).decode('utf-8'))

    def call(self, method, **params):
        self.seq += 1
        self._send(0x1, json.dumps({'id': self.seq, 'method': method, 'params': params}).encode('utf-8'))
        while True:
            msg = self._message()
            if msg.get('id') == self.seq:
                if 'error' in msg:
                    raise RuntimeError('%s: %s' % (method, msg['error']))
                return msg.get('result', {})

    def run(self, script):
        r = self.call('Runtime.evaluate', expression='(async () => {%s})()' % script, awaitPromise=True, returnByValue=True)
        if r.get('exceptionDetails'):
            raise RuntimeError('Erro na página: %s' % json.dumps(r['exceptionDetails'])[:400])
        return r.get('result', {}).get('value')


# Helpers available to every scene: wait for something, keep only some windows, place a window on the desk.
HELPERS = r'''
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(test, ms = 15000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (test()) return true; } catch (e) {} await wait(100); } throw new Error('demorou: ' + test); }
function $(s) { return document.querySelector(s); }
function only(ids) { document.querySelectorAll('.win').forEach(w => { if (!w.hidden && ids.indexOf(w.dataset.w) < 0) w.querySelector('.win-x').click(); }); }
function place(id, left, top, width, height) {
  const el = $('#w-' + id), desk = $('#desk');
  el.style.left = left + 'px'; el.style.top = top + 'px';
  el.style.width = (width || desk.clientWidth - left - 14) + 'px';
  if (height !== undefined) el.style.height = (height || desk.clientHeight - top - 14) + 'px';
}
function key(el, text) { el.value = text; el.dispatchEvent(new Event('input', { bubbles: true })); }
function enter(el) { el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); }
await until(() => $('#desk') && document.readyState === 'complete' && $('.topbar .state'));
await wait(600);
'''

SCENES = [
    ('equipamentos', 'equip', r'''
only(['equip']); place('equip', 14, 14, 0, 0);
$('#eq-scan').click();
await until(() => document.querySelectorAll('#tb-equip tr').length > 15 && !/Procurando/.test($('#ls-equip').textContent));
document.querySelector('#tb-equip tr[data-k^="192.168.10.2|"]').click();
'''),
    ('escuta', 'equip', r'''
only(['equip']); place('equip', 14, 14, 0, 0);
$('#eq-scan').click();
await until(() => document.querySelectorAll('#tb-equip tr').length > 15 && !/Procurando/.test($('#ls-equip').textContent));
$('#eq-listen').click();
await until(() => $('#dlg-esc') && $('#dlg-esc').open);
$('#esc-secs').value = '40'; $('#esc-go').click();
await until(() => $('#dlg-heard') && $('#dlg-heard').open, 20000);
'''),
    ('projeto', 'plano', r'''
only(['projeto']); place('projeto', 14, 14, 0);
await until(() => $('#v-plano') && $('#v-plano').textContent.length > 200);
return $('#w-projeto').getBoundingClientRect().bottom + 14;
'''),
    ('comandos', 'comandos', r'''
only(['projeto']); place('projeto', 14, 14, 0, 0);
await until(() => $('#v-comandos') && $('#v-comandos').textContent.length > 200);
'''),
    ('interfaces', 'interfaces', r'''
only(['interfaces']); place('interfaces', 14, 14, 0, 0);
await until(() => document.querySelectorAll('#v-interfaces tbody tr').length > 20);
const row = document.querySelector('#v-interfaces tbody tr[data-k*="1/0/9"]') || document.querySelectorAll('#v-interfaces tbody tr')[8];
row.click(); row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
await until(() => $('#dlg-if') && $('#dlg-if').open);
'''),
    ('terminal', 'terminal', r'''
only(['terminal']); place('terminal', 14, 14, 0);
await until(() => $('#tt-in') && !$('#tt-in').disabled);
key($('#tt-in'), 'display vlan brief'); enter($('#tt-in'));
await until(() => /VLAN ID/.test($('#tt-out').textContent) && !$('#tt-in').disabled);
key($('#tt-in'), 'port access vlan 20');
return $('#w-terminal').getBoundingClientRect().bottom + 14;
'''),
    ('inicio-rapido', 'assistente', r'''
only(['assistente']); place('assistente', 14, 14, 0, 0);
await until(() => document.querySelector('[data-act="g-open"][data-id="vlan"]'));
document.querySelector('[data-act="g-open"][data-id="vlan"]').click();
await wait(400);
'''),
]


def wait_http(url, seconds=30):
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            return urllib.request.urlopen(url, timeout=3).read()
        except OSError:
            time.sleep(0.3)
    raise RuntimeError('Não respondeu: ' + url)


def main():
    os.makedirs(OUT, exist_ok=True)
    version = re.search(r"^VERSION = '([^']+)'", open(os.path.join(ROOT, 'painel_local.py'), encoding='utf-8').read(), re.M).group(1)
    arte = os.path.join(ROOT, 'installer', 'arte.ps1')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
                    ". '%s'; [ArtePainel]::Capa('%s', '%s')" % (arte, os.path.join(OUT, 'capa.png'), version)], check=True)
    print('capa.png')

    sys.path.insert(0, ROOT)
    from painel_local import browser_candidates
    edge = next((b for b in browser_candidates() if b.lower().endswith('msedge.exe')), None) or (browser_candidates() or [None])[0]
    if not edge:
        raise SystemExit('Precisa do Edge ou do Chrome para tirar as fotos.')
    demo = subprocess.Popen([sys.executable, os.path.join(HERE, 'demonstracao.py'), '--porta', str(PORT), '--switch', str(SWITCH), '--sem-navegador'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    profile = tempfile.mkdtemp(prefix='painel-fotos-')
    browser = None
    try:
        wait_http('http://127.0.0.1:%d/' % PORT)
        browser = subprocess.Popen([edge, '--headless=new', '--remote-debugging-port=%d' % DEBUG, '--user-data-dir=' + profile, '--no-first-run',
                                    '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars', 'about:blank'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        targets = []
        for _ in range(100):
            try:
                targets = [t for t in json.loads(wait_http('http://127.0.0.1:%d/json/list' % DEBUG)) if t.get('type') == 'page']
            except (RuntimeError, ValueError):
                targets = []
            if targets:
                break
            time.sleep(0.2)
        tab = Tab(targets[0]['webSocketDebuggerUrl'])
        tab.call('Page.enable')
        tab.call('Emulation.setDeviceMetricsOverride', width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        tab.call('Emulation.setEmulatedMedia', features=[{'name': 'prefers-color-scheme', 'value': 'dark'}])
        for name, view, scene in SCENES:
            tab.call('Page.navigate', url='about:blank')
            time.sleep(0.3)
            tab.call('Page.navigate', url='http://127.0.0.1:%d/#%s' % (PORT, view))
            time.sleep(0.5)
            bottom = tab.run(HELPERS + scene)          # a scene may return where its window ends, to leave out the empty desk
            time.sleep(0.6)
            shot = {'format': 'png'}
            if bottom:
                shot['clip'] = {'x': 0, 'y': 0, 'width': WIDTH, 'height': min(HEIGHT, int(bottom)), 'scale': 1}
            png = base64.b64decode(tab.call('Page.captureScreenshot', **shot)['data'])
            with open(os.path.join(OUT, name + '.png'), 'wb') as f:
                f.write(png)
            print('%s.png (%d KB)' % (name, len(png) // 1024))
    finally:
        for p in (browser, demo):
            if p is not None:
                p.kill()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        time.sleep(0.5)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    main()
