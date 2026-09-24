"""Local and upstream market pending state must survive JSON cold reloads."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "shangzhuochifan"))
import market_engine
from vendor_cmd_adapter import base, market


class MarketUpstreamPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.save = Path(self.tmp.name) / "market_save.json"
        p = patch.object(market_engine, "SAVE_FILE", str(self.save))
        p.start()
        self.addCleanup(p.stop)

    def test_local_and_upstream_pending_fields_survive_json(self):
        game = market_engine.MarketGame()
        expected = {
            "_pending_chain_step": {"id": "legacy"},
            "_pending_chain_steps": [{"id": "new"}],
            "_pending_interaction": {"id": "interaction"},
            "_pending_help": {"id": "help"},
            "_pending_rare": {"id": "rare"},
            "_help_cooldown": {"stall": 3},
            "_stall_discounts": {"stall": 0.05},
            "_rare_found_test": True,
            "_per_stall_ms_triggered": {("stall", 20)},
            "_stall_item_cache": {"stall": ["test"]},
            "_season_stall_items": {"spring": ["test"]},
        }
        for key, value in expected.items():
            setattr(game, key, value)
        serialized = json.loads(json.dumps(game.to_dict(), ensure_ascii=False))
        restored = market_engine.MarketGame()
        self.assertTrue(restored.from_dict(serialized))
        for key, value in expected.items():
            self.assertEqual(getattr(restored, key), value, key)

    def test_old_save_without_new_fields_still_loads(self):
        data = market_engine.MarketGame().to_dict()
        for key in ("_pending_chain_steps", "_per_stall_ms_triggered", "_stall_item_cache", "_season_stall_items"):
            data.pop(key, None)
        game = market_engine.MarketGame()
        self.assertTrue(game.from_dict(json.loads(json.dumps(data))))
        self.assertEqual(game._pending_chain_steps, [])
        self.assertEqual(game._per_stall_ms_triggered, set())

    def test_stateless_adapter_cold_reload_and_player_isolation(self):
        with patch.object(base, "SAVE_ROOT", Path(self.tmp.name) / "saves"):
            market.GAME.run("testA", "状态", reset=True, extra={"seed": 42})
            path_a = base.SAVE_ROOT / "market" / "testA" / "market_save.json"
            data = json.loads(path_a.read_text())
            data["_pending_chain_steps"] = [{"id": "pending"}]
            data["_per_stall_ms_triggered"] = [["stall", 20]]
            data["_stall_discounts"] = {"stall": 0.05}
            data["_rare_found_flags"] = {"_rare_found_test": True}
            path_a.write_text(json.dumps(data))
            market.GAME.run("testA", "状态")
            after = json.loads(path_a.read_text())
            self.assertEqual(after["_per_stall_ms_triggered"], [["stall", 20]])
            self.assertEqual(after["_stall_discounts"], {"stall": 0.05})
            self.assertEqual(after["_pending_chain_steps"], [{"id": "pending"}])
            self.assertTrue(after["_rare_found_flags"]["_rare_found_test"])
            market.GAME.run("testB", "状态", reset=True, extra={"seed": 42})
            path_b = base.SAVE_ROOT / "market" / "testB" / "market_save.json"
            other = json.loads(path_b.read_text())
            self.assertEqual(other["_per_stall_ms_triggered"], [])
            self.assertEqual(other["_stall_discounts"], {})
            self.assertNotIn("_rare_found_test", other.get("_rare_found_flags", {}))


if __name__ == "__main__":
    unittest.main()
