"""Attribution and preparation safety checks; no download/install/browser needed."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from links import AUTHORS
from scripts import prepare_nowhere_env as prep
from vendor_cmd_adapter.guides import GUIDES

ROOT=Path(__file__).resolve().parents[1]


class AttributionTests(unittest.TestCase):
    def test_all_three_platform_displays_use_current_author(self):
        name='青少年小鼠狂饮乙醇（小红书号 94326164228）'
        home=(ROOT/'index.html').read_text()
        for game,repo in (('ciyuwu','ci-yu-wu'),('market','shangzhuochifan'),('nowhere','nowhere')):
            self.assertEqual(AUTHORS[game]['name'],name)
            self.assertEqual(AUTHORS[game]['project'],'https://github.com/yuyixuanfu/'+repo)
            card=home.split(f'id: "{game}",',1)[1].split('ranks:',1)[0]
            self.assertIn(name,card)
            self.assertNotIn('与一旋复',card)
        self.assertIn(name,GUIDES['market'])
        for file in ('turtle-soup/backend/guides/ciyuwu.md','nowhere_adapter/guide.md','docs/NOWHERE.md'):
            self.assertIn(name,(ROOT/file).read_text())
        from nowhere_adapter.web import render_page
        self.assertNotIn(name,render_page().decode())
        self.assertNotIn('platform-credit',render_page().decode())
        import server
        catalog = server._tool_list_games()
        self.assertNotIn(name, catalog)
        self.assertEqual(catalog.count('·青少年小鼠狂饮乙醇'), 3)
        for game in ('ciyuwu','market','nowhere'):
            self.assertIn(name,server._tool_get_guide({'game':game}))


class PreparationTests(unittest.TestCase):
    def test_disk_gate_fails_before_download(self):
        with patch.object(prep.shutil,'disk_usage',return_value=type('Disk',(),{'free':10})()):
            with self.assertRaisesRegex(RuntimeError,'磁盘不足'): prep.require_disk()

    def test_untrusted_archive_paths_and_links_are_rejected(self):
        for name,link in (('../escape',''),('python/link','../../escape')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'bad.tgz';target=root/'out';target.mkdir()
                with tarfile.open(archive,'w:gz') as tar:
                    item=tarfile.TarInfo(name)
                    if link: item.type=tarfile.SYMTYPE;item.linkname=link
                    tar.addfile(item,io.BytesIO())
                with self.assertRaisesRegex(RuntimeError,'不安全'): prep.unpack(archive,target)
                self.assertEqual(list(target.iterdir()),[])

    def test_download_checksum_mismatch_stops_before_unpack(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(prep.urllib.request,'urlopen',return_value=io.BytesIO(b'bad artifact')), patch.object(prep,'require_disk'):
            with self.assertRaisesRegex(RuntimeError,'SHA-256'):
                prep.download(Path(temp)/'artifact.tgz')

    def test_installer_timeout_terminates_its_own_process_group(self):
        from unittest.mock import Mock
        proc=Mock();proc.pid=12345;proc.poll.side_effect=[None,None]
        with tempfile.TemporaryDirectory() as temp, patch.object(prep.subprocess,'Popen',return_value=proc), patch.object(prep,'require_disk'), patch.object(prep.time,'monotonic',side_effect=[0,2]), patch.object(prep.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'超时'):
                prep.run(['unused'],{'TMPDIR':temp},timeout=1)
            kill.assert_called_once_with(12345,prep.signal.SIGTERM)
            proc.wait.assert_called_once_with(timeout=5)

    def test_no_shared_pip_configuration_or_python_path_in_install(self):
        with patch.dict(os.environ,{'PIP_INDEX_URL':'https://secret.invalid','PYTHONPATH':'/shared','UV_INDEX':'secret'}):
            env=prep.clean_env(Path('/temporary'))
        for key in ('PIP_INDEX_URL','PYTHONPATH','UV_INDEX'): self.assertNotIn(key,env)
        self.assertEqual(env['PIP_CONFIG_FILE'],os.devnull)

    def test_owned_complete_environment_is_idempotent_without_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);runtime=root/'runtime';venv=root/'venv'
            (runtime/'python/bin').mkdir(parents=True)
            (runtime/'python/bin/python3.11').touch()
            manifest={**prep.metadata(),'freeze':'fixture==1\n'}
            (runtime/'manifest.json').write_text(json.dumps(manifest))
            with patch.object(prep,'RUNTIME',runtime),patch.object(prep,'VENV',venv),patch.object(prep,'preflight'), \
                 patch.object(prep,'run',side_effect=['3.11.13\n','fixture==1\n','ok']),patch.object(prep,'download') as download:
                prep.prepare();download.assert_not_called()
                self.assertFalse(venv.exists())

    def test_complete_environment_drift_fails_without_reinstall(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'python/bin').mkdir(parents=True);(root/'python/bin/python3.11').touch()
            (root/'manifest.json').write_text(json.dumps({**prep.metadata(),'freeze':'fixture==1\n'}))
            with patch.object(prep,'RUNTIME',root),patch.object(prep,'preflight'), \
                 patch.object(prep,'run',side_effect=['3.11.13\n','fixture==2\n']),patch.object(prep,'download') as download:
                with self.assertRaisesRegex(RuntimeError,'漂移'): prep.prepare()
                download.assert_not_called()

    def test_live_fixture_uses_actual_authentication_and_binding_without_engine(self):
        from tests_toy.nowhere_live_acceptance import LiveAcceptanceTests
        import server
        case=LiveAcceptanceTests('test_live_engine_auth_private_world_and_browser')
        case.setUp()
        try:
            self.assertEqual(server._current_account(case.tokens[101])['id'],101)
            human=server._current_account(case.tokens[1])
            self.assertEqual(server._bound_ai_slot_target_for_user(human,'101:2')['player'],'101:2')
            self.assertIsNone(server._bound_ai_slot_target_for_user(human,'202'))
            with self.assertRaises(server._McpError): server._current_account('invalid')
            self.assertIn('open_door',case.play(101,2,'schema'))
        finally:
            case.tearDown();case.doCleanups()


if __name__=='__main__': unittest.main()
