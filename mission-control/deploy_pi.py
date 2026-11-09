#!/usr/bin/env python3
"""Install the Mission Control HTML only. No controller or hardware writes."""
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/data/solar-fortress')
HERE = Path(__file__).resolve().parent
HTML = 'Solar_Fortress_Mission_Control_Live.html'
CURRENT = ROOT/'mission-control'/'current'
HISTORY = ROOT/'history'/'telemetry.sqlite3'
API = 'http://127.0.0.1:8080'
KEYS = ('Mains', 'Bulk', 'Absorption', 'Float',
        'Inverter', 'Overload', 'LowBattery', 'Temperature')


def require(ok, message):
    if not ok:
        raise RuntimeError('STOP: '+message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get(path):
    with urllib.request.urlopen(API+path, timeout=8) as response:
        require(response.status == 200, 'HTTP failure for '+path)
        return response.read()


def status():
    result = json.loads(get('/api/status'))
    require(isinstance(result, dict), 'invalid API status')
    require(result.get('controller_running') is True, 'controller is not running')
    require(result.get('victron_connected') is True and not result.get('error'),
            'Victron telemetry is not healthy')
    require(not result.get('telemetry_history_error'), 'history recorder reports an error')
    readings = result.get('vebus_leds')
    require(isinstance(readings, dict) and all(key in readings for key in KEYS),
            'API does not expose all eight VE.Bus LED keys; no UI change made')
    for key in KEYS:
        value = readings[key]
        require(value is None or (type(value) in (int, float) and value in (0, 1, 2, 3)),
                'unexpected VE.Bus LED value for '+key+': '+repr(value))
    require(any(readings[key] is not None for key in KEYS),
            'all VE.Bus LED readings are unavailable')
    return result


def processes():
    names = ('solar_fortress.py', 'web_dashboard.py')
    found = {name: [] for name in names}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            argv = (item/'cmdline').read_bytes().replace(b'\0', b' ').decode(
                'utf-8', 'replace').split()
            if not argv or 'python' not in Path(argv[0]).name:
                continue
            for name in names:
                if any(arg == name or arg.endswith('/'+name) for arg in argv[1:]):
                    if name != 'solar_fortress.py' or '--apply' in argv:
                        found[name].append(int(item.name))
        except OSError:
            pass
    return {name: sorted(pids) for name, pids in found.items()}


def switch_link(destination):
    temporary = CURRENT.with_name('current.ui-stage-'+str(os.getpid()))
    try:
        os.symlink(str(destination), str(temporary))
        os.replace(str(temporary), str(CURRENT))
    finally:
        if os.path.lexists(str(temporary)):
            os.unlink(str(temporary))


def verify(expected_html, core, pids, history_identity):
    require(hashlib.sha256(get('/')).hexdigest() == expected_html,
            'served HTML differs from the tested replacement')
    live = status()
    require(processes() == pids, 'controller or web process changed during UI installation')
    for relative, expected in core.items():
        require(sha(ROOT/relative) == expected, 'protected source changed: '+relative)
    st = HISTORY.stat()
    require((st.st_dev, st.st_ino) == history_identity, 'history database was replaced')
    require(json.loads(get('/api/history')).get('source') == 'pi-sqlite',
            'persistent history API did not confirm pi-sqlite')
    return live


def main():
    manifest = json.loads((HERE/'manifest.json').read_text())
    new_hash = manifest['html_sha256']
    require(sha(HERE/HTML) == new_hash, 'replacement HTML checksum mismatch')
    require(CURRENT.is_symlink(), 'current UI is not the expected versioned symlink')
    require((ROOT/'mission-control'/'serve-on-8080.enabled').exists(),
            'Mission Control serving flag is missing')
    require(HISTORY.is_file() and HISTORY.stat().st_size > 0, 'persistent SQLite history missing')
    previous = CURRENT.resolve(strict=True)
    old_hash = sha(CURRENT/HTML)
    require(old_hash in (manifest['previous_html_sha256'], new_hash),
            'unexpected live UI revision; refusing to overwrite it')
    core = manifest['protected_sha256']
    print('=== READ-ONLY UI PREFLIGHT ===', flush=True)
    for relative, expected in core.items():
        actual = sha(ROOT/relative)
        print(relative, actual, flush=True)
        require(actual == expected, 'unexpected live source: '+relative)
    pids = processes()
    require(all(len(value) == 1 for value in pids.values()),
            'expected exactly one applied controller and one web backend')
    st = HISTORY.stat()
    identity = (st.st_dev, st.st_ino)
    before = status()
    require(hashlib.sha256(get('/')).hexdigest() == old_hash,
            'served page is not the current UI symlink')
    require(json.loads(get('/api/history')).get('source') == 'pi-sqlite',
            'persistent history API is unavailable')
    print('Live VE.Bus LEDs:', json.dumps(before['vebus_leds'], sort_keys=True), flush=True)
    print('Controller / web PIDs:', json.dumps(pids, sort_keys=True), flush=True)
    if old_hash == new_hash:
        verify(new_hash, core, pids, identity)
        print('=== VERIFIED: SOLAR FORTRESS UI 0.2.8 ALREADY SERVED ===', flush=True)
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT/'backups'/('mission-control-ui-v028-'+stamp)
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(CURRENT/HTML, backup/HTML)
    (backup/'rollback.json').write_text(json.dumps({
        'previous_target': str(previous), 'previous_html_sha256': old_hash,
        'protected_sha256': core, 'processes': pids,
    }, indent=2)+'\n')
    release = ROOT/'mission-control'/'releases'/('mission-control-ui-v028-'+stamp)
    release.mkdir(parents=True, exist_ok=False)
    shutil.copy2(HERE/HTML, release/HTML)
    require(sha(release/HTML) == new_hash, 'staged UI checksum mismatch')
    switched = False
    try:
        switch_link(release)
        switched = True
        live = verify(new_hash, core, pids, identity)
    except Exception:
        if switched:
            switch_link(previous)
            require(hashlib.sha256(get('/')).hexdigest() == old_hash,
                    'automatic UI rollback could not be verified')
            print('ROLLBACK VERIFIED: previous UI restored', flush=True)
        raise
    print('=== VERIFIED: SOLAR FORTRESS UI 0.2.8 SERVED ===', flush=True)
    print('Live VE.Bus LEDs:', json.dumps(live['vebus_leds'], sort_keys=True), flush=True)
    print('Requested controller mode:', live.get('mode'), flush=True)
    print('Controller and web PIDs unchanged:', json.dumps(pids, sort_keys=True), flush=True)
    print('Policy, writer, backend and SQLite identity verified unchanged', flush=True)
    print('Backup:', backup, flush=True)
    print('Refresh Mission Control with Ctrl+F5; compare the live lamps with the unit.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('UI INSTALLATION FAILED:', exc, file=sys.stderr, flush=True)
        sys.exit(1)
