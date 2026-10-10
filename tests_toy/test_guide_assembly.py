"""Guide-only regressions: no account, save or game engine access."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class GuideAssemblyTests(unittest.TestCase):
    def test_account_guide_covers_public_parameters_and_their_missing_uses(self):
        guide = (Path(__file__).resolve().parents[1] / "turtle-soup/backend/guides/account.md").read_text()
        source = next(t for t in server._PLATFORM_TOOLS if t["name"] == "account")
        for parameter in source["inputSchema"]["properties"]:
            with self.subTest(parameter=parameter):
                self.assertRegex(guide, r"(?<![A-Za-z0-9_])" + parameter + r"(?![A-Za-z0-9_])")
        deletion = next(line for line in guide.splitlines() if line.startswith("delete_account（"))
        self.assertIn("人类账号还必须传current_password验证当前密码", deletion)
        self.assertEqual(guide.count("current_password"), 1)
        reset = next(line for line in guide.splitlines() if line.startswith("reset_machine_password（"))
        self.assertIn("人类账号需token", reset)
        self.assertIn("ai_user_id+new_password", reset)

    def test_account_guide_preserves_public_save_contract_without_storage_details(self):
        guide = (Path(__file__).resolve().parents[1] / "turtle-soup/backend/guides/account.md").read_text()
        self.assertNotRegex(guide, r"账号id:slot|81:2|target_player_id|tombstone|\.db\b|data/vendor_saves|test_sessions|test_results|命名空间")
        for contract in (
            "身份由服务端根据token确定", "自报player_id被忽略", "互相隔离",
            "slot=1-5选择存档槽，默认1", "游客忽略slot", "永久停用",
            "guest_claim_code：", "guest:abc", 'claim_code="你的认领码", slot=2',
            "其他槽已有档不影响认领", "不覆盖、不删档", "认领码也不会消耗",
            "play的params中传相同slot续档", "my_saves（需token）", "human:true",
            "username指定", "delete_save（需token）", "confirm:true", "海龟汤不可删",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, guide)

    # Verified against per-slot identity routing and account_saves_for_user.
    slot_games = set((
        "mbti enneagram dnd love ecr humanity sins_virtues bdsmtest eco ciyuwu "
        "ai_life bar leek delve travel nowhere arcade burger crucible_echoes "
        "fishing forest moonlit imitator_td memoria white_room market workkk "
        "garden_cat camping_plaza detroit"
    ).split())
    non_slot_guides = {"account", "duel", "tarot", "turtle_soup", "puzzle_box"}

    def test_only_slot_games_get_the_short_note_and_no_guide_has_announcements(self):
        note = server.SAVE_SLOT_GUIDE_NOTE
        self.assertEqual(note, '\n\n[存档槽] 登录后在 play 的 params 里传 slot=1-5（默认1）；'
                              'export/import 作用于该 slot；查看各槽：account(action="my_saves")。')
        self.assertNotRegex(note, r"player_id|:2|:5|跨槽|复制")
        with patch.object(server.sqlite3, "connect", side_effect=AssertionError("no guide database access")):
            for game in sorted(self.slot_games | self.non_slot_guides):
                with self.subTest(game=game):
                    response = json.loads(server._tool_get_guide({"game": game}))
                    serialized = json.dumps(response, ensure_ascii=False)
                    self.assertNotRegex(serialized, r"platform_announcements|\bannouncements?\b|announcement_id|\bvote\b|平台公告|投票")
                    guide = response.get("guide", "")
                    self.assertEqual(guide.count(note), int(game in self.slot_games))
                    if game in self.slot_games:
                        self.assertTrue(guide.endswith(note))

    def test_nowhere_preserves_complete_body_without_duplicate_attribution(self):
        original = server.nowhere_adapter.guide()
        delivered = json.loads(server._tool_get_guide({"game": "nowhere"}))
        self.assertEqual(set(delivered), {"game", "guide"})
        self.assertEqual(delivered["guide"], original + server.SAVE_SLOT_GUIDE_NOTE)
        for text in (server.AUTHORS["nowhere"]["name"], "https://github.com/yuyixuanfu/nowhere",
                     "CC BY-NC 4.0", "GeoNames", "WorldClim", "Met Museum", "iNaturalist"):
            self.assertIn(text, delivered["guide"])
