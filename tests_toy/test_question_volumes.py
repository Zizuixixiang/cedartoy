"""Offline question measurements and the public discovery/Guide boundary."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
import research_scales
from deeptalk import catalog
from cedar_backend.question_volume import metadata, guide_volume
from scripts.update_question_volumes import build_metadata, LEGACY_MODES, GUIDES

ROOT = Path(__file__).resolve().parents[1]


class QuestionVolumeTests(unittest.TestCase):
    def test_precomputed_values_match_every_actual_question_and_static_guide(self):
        data = build_metadata()
        self.assertEqual(metadata(), data)
        self.assertEqual(len(data['games']), 34)
        self.assertNotIn('aidep', data['games'])
        examples = {'dt_relations': (13,516,593), 'dt_romance100': (100,1294,1667),
                    'dt_dilemmas30': (30,1902,2353), 'ipip_adhd': (12,191,217)}
        for game, expected in examples.items():
            v = data['games'][game]['variants'][0]
            self.assertEqual((v['count'],v['chars'],v['tokens']), expected)
        for game in (*LEGACY_MODES, 'bdsmtest'):
            text = (GUIDES / (game + '.md')).read_text()
            self.assertEqual([line for line in text.splitlines() if line.startswith('整套题库文本：')], [guide_volume(game)])
        self.assertEqual([v['count'] for v in data['games']['mbti']['variants']], [16,93])
        self.assertEqual([v['count'] for v in data['games']['enneagram']['variants']], [36,180])

    def test_three_sections_unique_entries_and_maintenance(self):
        listing = server._tool_list_games()
        self.assertEqual(re.findall(r'^(深聊问答|测试|游戏):', listing, re.M), ['深聊问答','测试','游戏'])
        self.assertNotRegex(listing, r'开放量表:|小游戏:|题文体积|Token|cl100k_base')
        deep, tests, games = re.split(r'\n(?:测试|游戏): ', listing)
        ids = lambda s: re.findall(r'(?:^|\n| \| )([a-z][a-z0-9_]*)(?:（[^）]*）)?·', s)
        self.assertEqual(ids(deep), list(catalog.ALIASES))
        expected = ['mbti','enneagram','dnd','love','ecr','humanity','sins_virtues','bdsmtest']
        self.assertEqual(ids(tests), expected + sorted(research_scales.MCP_GAMES))
        all_ids = ids(deep) + ids(tests) + ids(games)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertNotIn('deeptalk', all_ids)
        self.assertNotIn('aidep', all_ids)
        with patch.object(server, '_game_maintenance', return_value={'label':'维护中'}):
            marked = server._tool_list_games()
        self.assertEqual(marked, listing.replace('camping_plaza·', 'camping_plaza（维护中）·'))

    def test_all_guides_measured_once_and_no_human_only_mcp(self):
        import tiktoken
        encoder = tiktoken.get_encoding('cl100k_base')
        for game in (*catalog.ALIASES, *sorted(research_scales.MCP_GAMES), *LEGACY_MODES, 'bdsmtest'):
            with self.subTest(game=game):
                result = server._handle_root_mcp({'jsonrpc':'2.0','id':1,'method':'tools/call',
                    'params':{'name':'get_guide','arguments':{'game':game}}})['result']
                self.assertFalse(result['isError'], result)
                guide = json.loads(result['content'][0]['text'])['guide']
                self.assertEqual(guide.count('整套题库文本：'), 1)
                self.assertIn(guide_volume(game), guide)
                self.assertGreater(len(encoder.encode(guide)), 0)
                if game in research_scales.MCP_GAMES:
                    self.assertEqual(guide.count('不具有人类量表效度'), 1)
                    self.assertEqual(guide.count('不作诊断'), 1)
                    self.assertIn('未验证翻译', guide)
                    self.assertIn('选项（整数）', guide)
                    self.assertIn(research_scales.SCALES[game].attribution, guide)
        with self.assertRaises(server._McpError): server._tool_get_guide({'game':'aidep'})
        with self.assertRaises(server._McpError): server._tool_play({'game':'aidep','action':'start'})
        adhd = json.loads(server._tool_get_guide({'game':'ipip_adhd'}))['guide']
        self.assertIn('无临床阈值或常模', adhd)
        self.assertIn('非ASRS，不能筛查或确定ADHD', adhd)

    def test_runtime_guides_do_not_import_tokenizer(self):
        code = '''
import sys
sys.modules['tiktoken'] = None
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server, research_scales
from deeptalk.catalog import ALIASES
for game in [*ALIASES, *research_scales.MCP_GAMES]:
    assert '整套题库文本' in server._tool_get_guide({'game': game})
'''
        subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True,capture_output=True,text=True)
