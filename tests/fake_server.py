# Runs a throwaway copy of the panel with the administrator part replaced, so the screen can be
# tested without any Windows permission prompt and without changing this computer.
# Usage: python tests/fake_server.py [folder of the program] [port]
import importlib.util
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import frames as F  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..')
PORT = sys.argv[2] if len(sys.argv) > 2 else '8770'
tmp = tempfile.mkdtemp(prefix='painel-tela-')
for name in ('painel_local.py', 'painel-vlans.html'):
    shutil.copy(os.path.join(SRC, name), tmp)
spec = importlib.util.spec_from_file_location('painel_local', os.path.join(tmp, 'painel_local.py'))
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


def fake(script, timeout=240):
    if 'pktmon' not in script:
        return pl.UAC_DECLINED              # a test never changes this computer's addresses
    ads = pl.net_list()['adapters']
    up = next((a for a in ads if a['status'] == 'Up' and a['ips']), None)
    me = (up['mac'] if up else F.ME).upper().replace(':', '-')
    my_ip = up['ips'][0]['ip'] if up else '192.168.16.145'
    os.makedirs(pl.LISTEN_DIR, exist_ok=True)
    with open(os.path.join(pl.LISTEN_DIR, 'escuta.on'), 'w') as f:
        f.write('on')
    time.sleep(6)
    if os.path.exists(os.path.join(tmp, 'falhar')):
        with open(os.path.join(pl.LISTEN_DIR, 'escuta.log'), 'w', encoding='utf-8-sig') as f:
            f.write('pktmon start -> 5 O Monitor de Pacotes ja esta em execucao.')
        return 5
    with open(os.path.join(pl.LISTEN_DIR, 'escuta.pcapng'), 'wb') as f:
        f.write(F.pcapng(F.sample(me, my_ip)))
    return 0


pl.run_elevated = fake
print('copia de teste em', tmp)
sys.stdout.flush()
sys.argv = [sys.argv[0], '--porta', PORT, '--sem-navegador']
pl.main()
