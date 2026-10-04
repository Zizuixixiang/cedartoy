"""Isolated RackNerd parser/state tests; never contact Telegram or production DBs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import racknerd_monitor as monitor

URL = "https://my.racknerd.com/index.php?rp=/store/blackfriday2025&language=english"
ORDER = "https://my.racknerd.com/index.php?rp=/store/blackfriday2025/4-gb-kvm-vps-black-friday-2025"
CONFIG = {"ram_mb": 4096, "max_annual_cents": 3000, "failure_threshold": 3, "sources": [URL]}


def card(stock="0 Available", price="29.98", name="4 GB KVM VPS (Black Friday 2025)", order=ORDER):
    # Synthetic markup using the currently published official product fields.
    return f'''<div class="product"><header><span>{name}</span><span>{stock}</span></header>
    <div>3x vCPU Cores<br>65 GB PURE SSD RAID-10 Storage<br>4 GB RAM<br>
    Available in Multiple Locations<br>JUST $29.98/YEAR - WOW!!</div>
    <footer><div>Starting from <span>${price} USD</span><span>Annually</span></div>
    <a href="{order}">Order Now</a></footer></div>'''


def offer(stock=False, price=2998):
    return {"key": "test", "name": "4 GB KVM VPS", "ram_mb": 4096,
            "annual_cents": price, "stock": stock, "url": ORDER, "source": URL}


class ParserTests(unittest.TestCase):
    def test_zero_stock_beats_order_button(self):
        result = monitor.parse_offers(card(), URL)[0]
        self.assertEqual((result["ram_mb"], result["annual_cents"], result["stock"]), (4096, 2998, False))

    def test_real_billing_amount_beats_old_advertising_price(self):
        result = monitor.parse_offers(card("3 Available", "25.50"), URL)[0]
        self.assertEqual((result["annual_cents"], result["stock"]), (2550, True))

    def test_order_button_alone_is_unknown(self):
        self.assertIsNone(monitor.parse_offers(card(""), URL)[0]["stock"])

    def test_specials_markup_and_scripts(self):
        html = '<script>4 GB KVM VPS $1/year</script><article><h3>4 GB KVM VPS</h3><p><span>$</span>59.99/year</p><li>4 GB RAM</li><a href="https://my.racknerd.com/cart.php?a=add&amp;pid=954">Order now</a></article>'
        result = monitor.parse_offers(html, "https://www.racknerd.com/specials/")[0]
        self.assertEqual((result["annual_cents"], result["key"]), (5999, "pid:954"))

    def test_monthly_and_biennial_not_annual(self):
        for period in ["Monthly", "Biennially"]:
            html = card().replace("JUST $29.98/YEAR - WOW!!", "").replace("Annually", period)
            with self.assertRaises(ValueError):
                monitor.parse_offers(html, URL)

    def test_stock_does_not_leak_from_next_card(self):
        result = monitor.parse_offers(card("2 Available") + card("0 Available", order=ORDER + "-second"), URL)
        self.assertEqual([o["stock"] for o in result], [True, False])

    def test_megabytes_and_no_wrong_ram(self):
        self.assertEqual(monitor.parse_offers(card(name="4096 MB KVM VPS"), URL)[0]["ram_mb"], 4096)
        with self.assertRaises(ValueError):
            monitor.parse_offers(card().replace("4 GB RAM", "2 GB RAM"), URL)

    def test_checkout_requires_product_price_and_form(self):
        self.assertFalse(monitor.checkout_stock("<h1>Out of Stock</h1>", ORDER, offer()))
        self.assertIsNone(monitor.checkout_stock(card("3 Available"), ORDER, offer()))
        html = '<h1>4 GB KVM VPS</h1><input name="hostname"><select name="billingcycle"><option>$29.98 USD Annually</option></select>'
        self.assertTrue(monitor.checkout_stock(html, ORDER, offer()))
        self.assertIsNone(monitor.checkout_stock(html.replace("29.98", "59.99"), ORDER, offer()))

    def test_domain_restriction(self):
        for url in ["https://greencloudvps.com/", "https://racknerd.com.evil.test/", "http://www.racknerd.com/", "https://user:secret@racknerd.com/"]:
            with self.assertRaises(ValueError):
                monitor.official_url(url)

    def test_scan_confirms_links_and_deduplicates_redirected_product(self):
        second = "https://www.racknerd.com/specials/"
        pid_link = "https://my.racknerd.com/cart.php?a=add&pid=925"
        checkout = '<h1>4 GB KVM VPS (Black Friday 2025)</h1><input name="hostname"><select name="billingcycle"><option>$29.98 USD Annually</option></select>'
        pages = {URL: (card("3 Available"), URL), second: (card("", order=pid_link), second), ORDER: (checkout, ORDER), pid_link: (checkout, ORDER)}
        class FakeFetcher:
            def get(self, url):
                return pages[url]
        offers, failures, succeeded = monitor.scan(dict(CONFIG, sources=[URL, second]), FakeFetcher())
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0]["stock"])
        self.assertFalse(failures)
        self.assertEqual(len(succeeded), 2)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self.state = monitor.empty_state()
        self.messages = []

    def tearDown(self):
        self.tmp.cleanup()

    def persist(self, state):
        monitor.save_state(self.path, state)

    def cycle(self, offers, failures=None, send=None, initialize=False):
        ready = monitor.observe(self.state, offers, failures or {}, [] if failures else [URL], CONFIG, initialize)
        self.persist(self.state)
        result = monitor.deliver(self.state, ready, send or self.messages.append, self.persist)
        self.state = monitor.read_state(self.path)
        return result

    def test_new_restock_drop_and_unchanged_across_restart(self):
        self.cycle([offer()])
        for _ in range(3):
            self.cycle([offer()])
        self.assertEqual(len(self.messages), 1)
        self.cycle([offer(True)])
        self.cycle([offer(True)])
        self.cycle([offer(True, 2500)])
        self.cycle([offer(True, 2500)])
        self.assertEqual(len(self.messages), 3)
        self.assertIn("补货", self.messages[1])
        self.assertIn("降价", self.messages[2])
        self.cycle([offer(False, 2500)])
        self.cycle([offer(True, 2500)])
        self.assertEqual(len(self.messages), 4)

    def test_new_cheaper_product_and_threshold_crossing(self):
        self.cycle([offer(True, 4000)])
        self.assertFalse(self.messages)
        self.cycle([offer(True, 3000)])
        cheap = offer(True, 2000)
        cheap["key"] = "new-cheaper-plan"
        self.cycle([cheap])
        self.cycle([cheap])
        self.assertEqual(len(self.messages), 2)

    def test_send_failure_retained_until_revalidated(self):
        def fail(_):
            raise OSError("must not log this sensitive payload")
        with self.assertLogs(monitor.LOG, "WARNING") as log:
            self.cycle([offer(True)], send=fail)
        self.assertNotIn("sensitive", str(log.output))
        self.assertTrue(self.state["pending"])
        self.cycle([], {URL: "network"})
        self.assertFalse(self.messages)
        self.cycle([offer(True)])
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(self.state["pending"])

    def test_outdated_restock_not_sent(self):
        self.cycle([offer()], initialize=True)
        with patch.object(monitor.LOG, "warning"):
            self.cycle([offer(True)], send=lambda _: (_ for _ in ()).throw(OSError()))
        self.cycle([offer(False)])
        self.assertFalse(self.messages)
        self.assertFalse(self.state["pending"])

    def test_unknown_or_missing_never_implies_stock_change(self):
        self.cycle([offer()], initialize=True)
        self.cycle([offer(None)])
        self.cycle([])
        self.assertFalse(self.messages)
        self.cycle([offer(True)])
        self.assertIn("补货", self.messages[0])

    def test_failure_alert_aggregated_deduplicated_and_rearmed(self):
        failures = {URL: "network", URL + "2": "network"}
        for _ in range(6):
            self.cycle([], failures)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("2 个来源", self.messages[0])
        self.cycle([])
        for _ in range(3):
            self.cycle([], {URL: "network"})
        self.assertEqual(len(self.messages), 2)

    def test_cross_process_persistent_dedup(self):
        self.cycle([offer(True)])
        code = 'import json,sys; from pathlib import Path; from scripts import racknerd_monitor as m; s=m.read_state(Path(sys.argv[1])); print(json.dumps(m.observe(s,[json.loads(sys.argv[2])],{},[sys.argv[3]],json.loads(sys.argv[4]))))'
        result = subprocess.run([sys.executable, "-c", code, str(self.path), json.dumps(offer(True)), URL, json.dumps(CONFIG)], cwd=monitor.ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_state_preserved(self):
        self.path.write_text("not json")
        with self.assertRaises(ValueError):
            monitor.read_state(self.path)
        self.assertEqual(self.path.read_text(), "not json")

    def test_dry_run_never_sends_or_changes_state(self):
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps(dict(CONFIG, telegram_env="/unused")))
        self.persist(self.state)
        before = self.path.read_bytes()
        with patch.object(monitor, "scan", return_value=([offer()], {}, [URL])), patch.object(monitor, "send_telegram") as send, patch("builtins.print"):
            self.assertEqual(monitor.main(["--config", str(config_path), "--runtime-dir", self.tmp.name, "--dry-run"]), 0)
            send.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
