import contextlib
import importlib.util
import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ui_deployer', HERE/'deploy_pi.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = self.root/'payload'
        self.payload.mkdir()
        self.live = self.root/'live'
        self.previous = self.live/'mission-control'/'releases'/'previous'
        self.previous.mkdir(parents=True)
        (self.previous/deploy.HTML).write_text('old verified UI')
        self.current = self.live/'mission-control'/'current'
        self.current.symlink_to(self.previous)
        (self.live/'mission-control'/'serve-on-8080.enabled').touch()
        self.history = self.live/'history'/'telemetry.sqlite3'
        self.history.parent.mkdir()
        self.history.write_bytes(b'preserved history fixture')
        self.core = {}
        for name in ('maintenance_policy.py', 'maintenance_runtime.py', 'solar_fortress.py', 'web_dashboard.py'):
            p = self.live/'src'/name
            p.parent.mkdir(exist_ok=True)
            p.write_text('protected '+name)
            self.core['src/'+name] = deploy.sha(p)
        (self.payload/deploy.HTML).write_text('new verified UI')
        self.manifest = dict(html_sha256=deploy.sha(self.payload/deploy.HTML),
                             previous_html_sha256=deploy.sha(self.previous/deploy.HTML),
                             protected_sha256=self.core)
        (self.payload/'manifest.json').write_text(json.dumps(self.manifest))
        self.pids = {'solar_fortress.py': [740], 'web_dashboard.py': [12739]}
        self.readings = dict.fromkeys(deploy.KEYS, 0)
        self.readings['Inverter'] = 1
        self.live_status = dict(controller_running=True, victron_connected=True,
                               error='', vebus_leds=self.readings, mode='BATTERY FIRST')
        self.patchers = [
            patch.object(deploy, 'ROOT', self.live),
            patch.object(deploy, 'HERE', self.payload),
            patch.object(deploy, 'CURRENT', self.current),
            patch.object(deploy, 'HISTORY', self.history),
            patch.object(deploy, 'processes', return_value=self.pids),
            patch.object(deploy, 'get', side_effect=self.api),
        ]
        for p in self.patchers:
            p.start()
        self.addCleanup(self.temp.cleanup)
        for p in reversed(self.patchers):
            self.addCleanup(p.stop)

    def api(self, path):
        if path == '/':
            return (self.current/deploy.HTML).read_bytes()
        if path == '/api/status':
            return json.dumps(self.live_status).encode()
        if path == '/api/history':
            return b'{"source":"pi-sqlite"}'
        raise AssertionError(path)

    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.main()

    def test_only_ui_changes_and_backup_is_complete(self):
        inode = self.history.stat().st_ino
        self.run_install()
        self.assertEqual(deploy.sha(self.current/deploy.HTML), self.manifest['html_sha256'])
        self.assertEqual(self.history.stat().st_ino, inode)
        for path, expected in self.core.items():
            self.assertEqual(deploy.sha(self.live/path), expected)
        backups = list((self.live/'backups').glob('*/rollback.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())['previous_target'], str(self.previous))
        self.assertEqual((backups[0].parent/deploy.HTML).read_text(), 'old verified UI')

    def test_repeat_install_is_read_only(self):
        self.run_install()
        target = self.current.resolve()
        self.run_install()
        self.assertEqual(self.current.resolve(), target)
        self.assertEqual(len(list((self.live/'backups').iterdir())), 1)

    def test_unexpected_html_is_rejected_before_writes(self):
        (self.previous/deploy.HTML).write_text('unexpected revision')
        with self.assertRaisesRegex(RuntimeError, 'unexpected live UI'):
            self.run_install()
        self.assertEqual(self.current.resolve(), self.previous)
        self.assertFalse((self.live/'backups').exists())

    def test_unexpected_policy_is_rejected_before_writes(self):
        (self.live/'src'/'maintenance_policy.py').write_text('newer policy')
        with self.assertRaisesRegex(RuntimeError, 'unexpected live source'):
            self.run_install()
        self.assertEqual(self.current.resolve(), self.previous)
        self.assertFalse((self.live/'backups').exists())

    def test_missing_led_channel_is_rejected_before_writes(self):
        del self.readings['Float']
        with self.assertRaisesRegex(RuntimeError, 'eight VE.Bus LED'):
            self.run_install()
        self.assertEqual(self.current.resolve(), self.previous)

    def test_boolean_led_does_not_become_fake_on_state(self):
        self.readings['Mains'] = True
        with self.assertRaisesRegex(RuntimeError, 'unexpected VE.Bus LED'):
            self.run_install()

    def test_all_unknown_leds_do_not_pass_live_preflight(self):
        self.live_status['vebus_leds'] = dict.fromkeys(deploy.KEYS)
        with self.assertRaisesRegex(RuntimeError, 'all VE.Bus LED'):
            self.run_install()

    def test_failed_readback_rolls_back_ui(self):
        with patch.object(deploy, 'verify', side_effect=RuntimeError('simulated readback failure')):
            with self.assertRaisesRegex(RuntimeError, 'simulated readback'):
                self.run_install()
        self.assertEqual(self.current.resolve(), self.previous)
        for path, expected in self.core.items():
            self.assertEqual(deploy.sha(self.live/path), expected)

    def test_process_restart_is_detected_and_ui_rolls_back(self):
        with patch.object(deploy, 'processes', side_effect=[
                self.pids, {'solar_fortress.py': [741], 'web_dashboard.py': [12739]}]):
            with self.assertRaisesRegex(RuntimeError, 'process changed'):
                self.run_install()
        self.assertEqual(self.current.resolve(), self.previous)

    def test_payload_checksum_is_checked_before_writes(self):
        (self.payload/deploy.HTML).write_text('corrupted upload')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            self.run_install()
        self.assertFalse((self.live/'backups').exists())


class PackageTests(unittest.TestCase):
    def test_manifest_matches_deployable_html_and_verifier(self):
        manifest = json.loads((HERE/'manifest.json').read_text())
        for filename, key in [('Solar_Fortress_Mission_Control_Live.html', 'html_sha256'),
                              ('verify_render.html', 'verifier_sha256')]:
            self.assertEqual(hashlib.sha256((HERE/filename).read_bytes()).hexdigest(), manifest[key])
        self.assertIn(manifest['html_sha256'], (HERE/'verify_render.html').read_text())


if __name__ == '__main__':
    unittest.main()
