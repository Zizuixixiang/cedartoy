"""Target-specific fixtures and state-machine checks. Never contact Telegram."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from scripts import racknerd_monitor as m

CONFIG = json.loads(m.DEFAULT_CONFIG.read_text())
CLUB_HTML = (Path(__file__).parent / 'fixtures/racknerd_club_4gb.html').read_text()
OFFICIAL_HTML = '''<h1>4 GB KVM VPS (Black Friday 2025)</h1>
<p>3x vCPU Cores<br>65 GB PURE SSD RAID-10 Storage<br>4 GB RAM</p>
<form><input name="hostname"><select name="billingcycle">
<option value="annually">$29.98 USD Annually</option></select></form>'''
SOLD_OUT = '<h1>Out of Stock</h1><p>The plan you selected is out of stock and not available for ordering at this time.</p>'


def club(label='Sold Out'):
    return CLUB_HTML.replace('Sold Out', label)


def observations(official=None, auxiliary=False, now=1000):
    return {
        'official': {'stock': official, 'checked_at': now, 'checks': 2,
                     'error': 'http_403' if official is None else None, 'url': m.OFFICIAL},
        'club': {'stock': auxiliary, 'checked_at': now, 'checks': 2,
                 'error': 'parse_failed' if auxiliary is None else None, 'url': m.CLUB},
    }


class ParserTests(unittest.TestCase):
    def test_config_only_targets_exact_plan(self):
        m.validate_config(CONFIG)
        self.assertEqual(CONFIG['target'], m.TARGET)
        self.assertNotIn('sources', CONFIG)
        for field, value in [('cpu_cores', 2), ('annual_cents', 3000), ('disk_gb', 60)]:
            config = copy.deepcopy(CONFIG)
            config['target'][field] = value
            with self.assertRaises(m.EvidenceError):
                m.validate_config(config)

    def test_live_club_card_and_explicit_positive_labels(self):
        self.assertFalse(m.parse_club(CLUB_HTML, m.CLUB))
        for label in ['Available', 'In Stock', 'Order Now', 'Buy Now']:
            self.assertTrue(m.parse_club(club(label), m.CLUB))

    def test_club_rejects_other_plan_price_or_ambiguous_status(self):
        for old, new in [('4GB Package', '4.5GB Package'), ('>3<', '>2<'),
                         ('65 G SSD', '60 G SSD'), ('$29.98', '$30.00'),
                         ('/product/925', '/product/954'), ('Sold Out', 'Learn More'),
                         ('Sold Out', 'Sold Out Available')]:
            with self.subTest(change=(old, new)), self.assertRaises(m.EvidenceError):
                m.parse_club(CLUB_HTML.replace(old, new), m.CLUB)
        with self.assertRaises(m.EvidenceError):
            m.parse_club(CLUB_HTML + CLUB_HTML, m.CLUB)

    def test_missing_status_disabled_button_and_hidden_card_are_unknown(self):
        for html in [club(''), club('Available').replace('class="btn ', 'disabled class="btn '),
                     CLUB_HTML.replace('single-pricing-pack"', 'single-pricing-pack" hidden'),
                     '<script>' + CLUB_HTML + '</script>']:
            with self.assertRaises(m.EvidenceError):
                m.parse_club(html, m.CLUB)

    def test_stock_is_scoped_to_exact_card(self):
        other = club('Available').replace('4GB Package', '8GB Package').replace('/product/925', '/product/927')
        self.assertFalse(m.parse_club(other + CLUB_HTML + other, m.CLUB))
        other = CLUB_HTML.replace('4GB Package', '8GB Package')
        self.assertTrue(m.parse_club(other + club('Available'), m.CLUB))

    def test_explicit_stale_or_future_timestamp_cannot_vote(self):
        now = 1792321200  # 2026-10-18 UTC
        for timestamp in ['2026-09-13', '2030-01-01', 'unknown']:
            with self.assertRaises(m.EvidenceError):
                m.parse_club(CLUB_HTML + '<p>Last Updated: ' + timestamp + '</p>', m.CLUB, now)
        self.assertFalse(m.parse_club(CLUB_HTML + '<p>Last Updated: 2026-10-18</p>', m.CLUB, now))

    def test_official_requires_exact_specs_and_enabled_annual_checkout_option(self):
        self.assertTrue(m.parse_official(OFFICIAL_HTML, m.OFFICIAL))
        for old, new in [('2025', '2024'), ('4 GB RAM', '4.5 GB RAM'), ('3x vCPU', '2x vCPU'),
                         ('65 GB PURE', '60 GB PURE'), ('29.98', '39.98'), ('annually', 'biennially'),
                         ('<select ', '<select disabled '), ('<option ', '<option disabled '),
                         ('<form>', '<div>'), ('name="hostname"', 'name="other"')]:
            with self.subTest(change=(old, new)), self.assertRaises(m.EvidenceError):
                m.parse_official(OFFICIAL_HTML.replace(old, new), m.OFFICIAL)

    def test_marketing_price_is_not_billing_price(self):
        with self.assertRaises(m.EvidenceError):
            m.parse_official('<p>$29.98/year</p>' + OFFICIAL_HTML.replace('29.98', '59.99'), m.OFFICIAL)

    def test_official_sold_out_and_challenge_are_distinct(self):
        self.assertFalse(m.parse_official(SOLD_OUT, m.OFFICIAL))
        for html in ['<title>Just a moment...</title>', '<h1>Shared Hosting</h1>', club('Available')]:
            with self.assertRaises(m.EvidenceError):
                m.parse_official(html, m.OFFICIAL)

    def test_source_and_redirect_allowlist(self):
        for url in [m.OFFICIAL, m.CLUB, m.OFFICIAL.replace('/store/', '%2Fstore%2F')]:
            m.allowed_url(url)
        for url in ['http://racknerd.club/en/black-friday-2025', 'https://racknerd.club/product/925',
                    'https://racknerd.club.evil.test/en/black-friday-2025',
                    'https://my.racknerd.com/index.php?rp=/store/shared-hosting',
                    'https://user:secret@racknerd.club/en/black-friday-2025',
                    'https://racknerdtracker.com/old/']:
            with self.assertRaises(m.EvidenceError):
                m.allowed_url(url)
            with self.assertRaises(m.EvidenceError):
                m.SafeRedirect().redirect_request(None, None, 302, '', {}, url)


class FetchAndScanTests(unittest.TestCase):
    def test_403_is_not_retried_or_parsed(self):
        fetcher = m.Fetcher(interval=0)
        error = HTTPError(m.OFFICIAL, 403, 'Forbidden', {}, None)
        with patch.object(fetcher.opener, 'open', side_effect=error) as request:
            with self.assertRaises(HTTPError):
                fetcher.get(m.OFFICIAL)
        request.assert_called_once()
        self.assertEqual(m.safe_error(error), 'http_403')

    def test_challenge_and_stale_cache_rejected(self):
        for headers in [{'cf-mitigated': 'challenge'}, {'Age': '301'}]:
            fetcher = m.Fetcher(interval=0)
            response = MagicMock()
            response.url, response.headers = m.CLUB, headers
            response.__enter__.return_value = response
            with patch.object(fetcher.opener, 'open', return_value=response), self.assertRaises(m.EvidenceError):
                fetcher.get(m.CLUB)
            response.read.assert_not_called()

    def test_second_read_is_network_request_with_revalidation(self):
        fetcher = m.Fetcher(interval=0)
        response = MagicMock()
        response.url, response.headers = m.CLUB, {}
        response.read.return_value = CLUB_HTML.encode()
        response.__enter__.return_value = response
        with patch.object(fetcher.opener, 'open', return_value=response) as request:
            fetcher.get(m.CLUB)
            fetcher.get(m.CLUB)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0].get_header('Cache-control'), 'no-cache, max-age=0')

    def run_scan(self, first, second=None, state=None):
        fetcher = MagicMock()
        def get(url):
            if url == m.OFFICIAL:
                raise HTTPError(url, 403, 'Forbidden', {}, None)
            value = queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return value, m.CLUB
        queue = [first] + ([] if second is None else [second])
        fetcher.get.side_effect = get
        wait = MagicMock()
        result = m.scan(CONFIG, fetcher, state or m.empty_state(), wait=wait)
        return result, fetcher, wait

    def test_initial_baseline_is_double_checked_and_official_error_retained(self):
        result, fetcher, wait = self.run_scan(CLUB_HTML, CLUB_HTML)
        self.assertFalse(result['club']['stock'])
        self.assertEqual(result['club']['checks'], 2)
        self.assertEqual(result['official']['error'], 'http_403')
        self.assertEqual(fetcher.get.call_count, 3)
        wait.assert_called_once_with(20)
        self.assertEqual(m.health(result), 'auxiliary_only')

    def test_unchanged_negative_needs_one_read_but_rearming_needs_two(self):
        state = m.empty_state()
        m.observe(state, observations(), CONFIG, now=1000)
        result, fetcher, wait = self.run_scan(CLUB_HTML, state=state)
        self.assertFalse(result['club']['stock'])
        self.assertEqual(fetcher.get.call_count, 2)
        wait.assert_not_called()
        state['sources']['club']['stock'] = True
        result, _, wait = self.run_scan(CLUB_HTML, CLUB_HTML, state)
        self.assertEqual(result['club']['checks'], 2)
        wait.assert_called_once()

    def test_disagreeing_failed_or_unparseable_second_read_is_unknown(self):
        for second in [CLUB_HTML, '<p>broken</p>', HTTPError(m.CLUB, 503, '', {}, None)]:
            result, _, _ = self.run_scan(club('Available'), second)
            self.assertIsNone(result['club']['stock'])
            self.assertTrue(result['club']['error'])
            self.assertEqual(m.health(result), 'unavailable')

    def test_official_positive_is_rechecked(self):
        fetcher = MagicMock()
        fetcher.get.side_effect = lambda url: (OFFICIAL_HTML if url == m.OFFICIAL else CLUB_HTML, url)
        result = m.scan(CONFIG, fetcher, m.empty_state(), wait=MagicMock())
        self.assertTrue(result['official']['stock'])
        self.assertEqual(result['official']['checks'], 2)
        self.assertEqual(m.health(result), 'official_confirmed')


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.json'
        self.state = m.empty_state()
        self.messages = []
        self.now = 1000

    def tearDown(self):
        self.tmp.cleanup()

    def persist(self, state):
        m.save_state(self.path, state)

    def cycle(self, official=None, auxiliary=False, send=None, initialize=False, advance=10):
        self.now += advance
        ready = m.observe(self.state, observations(official, auxiliary, self.now), CONFIG, initialize, self.now)
        self.persist(self.state)
        result = m.deliver(self.state, ready, send or self.messages.append, self.persist)
        self.state = m.read_state(self.path)
        return result

    def test_baseline_and_first_available_never_imply_third_party_restock(self):
        self.cycle(auxiliary=True)
        self.cycle(auxiliary=True)
        self.assertFalse(self.messages)
        self.cycle(auxiliary=False)
        self.cycle(auxiliary=True)
        self.cycle(auxiliary=True)
        self.assertEqual(len(self.messages), 1)
        self.assertIn('疑似补货，官方站无法自动复核，请手动打开链接', self.messages[0])
        self.assertIn('http_403', self.messages[0])
        self.assertIn(m.OFFICIAL, self.messages[0])
        self.assertIn(m.CLUB, self.messages[0])
        self.assertNotIn('已确认可配置', self.messages[0])

    def test_unknown_does_not_create_false_transition_or_rearm(self):
        self.cycle()
        self.cycle(auxiliary=True)
        self.cycle(auxiliary=None)
        self.cycle(auxiliary=True)
        self.assertEqual(len(self.messages), 1)
        self.cycle(auxiliary=False)
        self.cycle(auxiliary=None)
        self.cycle(auxiliary=True)
        self.assertEqual(len(self.messages), 2)

    def test_single_parse_failure_with_no_baseline_never_alerts(self):
        self.cycle(auxiliary=None)
        self.cycle(auxiliary=True)
        self.assertFalse(self.messages)

    def test_old_absence_is_not_a_fresh_restock(self):
        self.cycle()
        self.cycle(auxiliary=True, advance=CONFIG['baseline_max_age_seconds'] + 1)
        self.assertFalse(self.messages)

    def test_official_absence_vetoes_suspected_and_official_positive_can_upgrade(self):
        self.cycle()
        self.cycle(official=False, auxiliary=True)
        self.assertFalse(self.messages)
        self.cycle(auxiliary=False)
        self.cycle(auxiliary=True)
        self.cycle(official=True, auxiliary=True)
        self.cycle(official=True, auxiliary=True)
        self.assertEqual(len(self.messages), 2)
        self.assertIn('疑似补货', self.messages[0])
        self.assertIn('官方订购页已确认', self.messages[1])

    def test_confirmed_stock_dedup_and_rearm(self):
        self.cycle(official=True)
        self.cycle(official=None)
        self.cycle(official=True)
        self.assertEqual(len(self.messages), 1)
        self.cycle(official=False)
        self.cycle(official=True)
        self.assertEqual(len(self.messages), 2)

    def fail(self, text):
        raise OSError('sensitive content must never be logged')

    def test_send_failure_survives_restart_and_requires_revalidation(self):
        self.cycle()
        with self.assertLogs(m.LOG, 'WARNING') as logs:
            self.cycle(auxiliary=True, send=self.fail)
        self.assertNotIn('sensitive', str(logs.output))
        self.assertIn('suspected', self.state['pending'])
        self.cycle(auxiliary=None)
        self.assertFalse(self.messages)
        self.cycle(auxiliary=True)
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(self.state['pending'])

    def test_obsolete_pending_is_cancelled_by_sold_out_or_official_verdict(self):
        for official, auxiliary in [(None, False), (False, True), (True, True)]:
            self.state = m.empty_state()
            self.cycle()
            with patch.object(m.LOG, 'warning'):
                self.cycle(auxiliary=True, send=self.fail)
            self.cycle(official=official, auxiliary=auxiliary)
            self.assertNotIn('suspected', self.state['pending'])

    def test_expired_pending_does_not_resend(self):
        self.cycle()
        with patch.object(m.LOG, 'warning'):
            self.cycle(auxiliary=True, send=self.fail)
        self.cycle(auxiliary=True, advance=CONFIG['baseline_max_age_seconds'] + 1)
        self.assertFalse(self.messages)
        self.assertFalse(self.state['pending'])

    def test_both_unavailable_alert_once_and_recover(self):
        for _ in range(5):
            self.cycle(auxiliary=None)
        self.assertEqual(len(self.messages), 1)
        self.assertIn('所有信号', self.messages[0])
        for _ in range(5):
            self.cycle()  # Expected official 403 alone must not cause noise.
        self.assertEqual(len(self.messages), 1)
        for _ in range(3):
            self.cycle(auxiliary=None)
        self.assertEqual(len(self.messages), 2)

    def test_old_state_migrates_without_sending_old_events(self):
        old = {'version': 1, 'offers': {'unrelated': {}}, 'failures': {},
               'pending': {'unrelated': {'text': 'old promotion'}}}
        self.path.write_text(json.dumps(old))
        self.state = m.read_state(self.path)
        self.assertEqual(self.state['legacy_v1'], old)
        self.assertFalse(self.state['pending'])
        self.cycle(initialize=True)
        self.assertFalse(self.messages)
        self.assertEqual(self.state['version'], 2)
        self.assertEqual(self.state['legacy_v1'], old)

    def test_corrupt_state_is_preserved(self):
        for value in ['not json', '{"version": 2}', '{"version": 1}']:
            self.path.write_text(value)
            with self.assertRaises(ValueError):
                m.read_state(self.path)
            self.assertEqual(self.path.read_text(), value)

    def test_cross_process_notification_dedup(self):
        self.cycle()
        self.cycle(auxiliary=True)
        code = 'import json,sys; from pathlib import Path; from scripts import racknerd_monitor as m; s=m.read_state(Path(sys.argv[1])); print(json.dumps(m.observe(s,json.loads(sys.argv[2]),json.loads(sys.argv[3]),now=1030)))'
        result = subprocess.run([sys.executable, '-c', code, str(self.path), json.dumps(observations(auxiliary=True, now=1030)), json.dumps(CONFIG)], cwd=m.ROOT, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_dry_run_does_not_persist_or_send(self):
        self.cycle()
        before = self.path.read_bytes()
        with patch.object(m, 'scan', return_value=observations(auxiliary=True, now=self.now + 1)), patch.object(m, 'send_telegram') as send, patch('sys.stdout', new_callable=io.StringIO) as output, patch.object(m.time, 'time', return_value=self.now + 1):
            self.assertEqual(m.main(['--runtime-dir', self.tmp.name, '--dry-run']), 0)
            send.assert_not_called()
            report = json.loads(output.getvalue())
        self.assertEqual(report['mode'], 'auxiliary_only')
        self.assertEqual(report['would_notify'][0]['kind'], 'suspected')
        self.assertEqual(self.path.read_bytes(), before)

    def test_initialize_migrates_silently_but_never_resets_existing_baseline(self):
        self.path.write_text(json.dumps({'version': 1, 'offers': {}, 'failures': {}, 'pending': {}}))
        with patch.object(m, 'scan', return_value=observations()), patch.object(m, 'send_telegram') as send, patch('builtins.print'):
            self.assertEqual(m.main(['--runtime-dir', self.tmp.name, '--initialize']), 0)
            before = self.path.read_bytes()
            self.assertEqual(m.main(['--runtime-dir', self.tmp.name, '--initialize']), 1)
            send.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)


class PromotionTests(unittest.TestCase):
    # Synthetic markup for safety/state regression, NOT a captured live page.
    def card(self, pid=1001, ram='4', price='29.99', label='Order Now'):
        return (f'<article><div><h3>{ram} GB KVM VPS</h3></div>'
                f'<div>${price}<span>/year</span></div><ul><li>{ram} GB RAM</li></ul>'
                f'<a href="https://my.racknerd.com/cart.php?a=add&amp;pid={pid}">{label}</a></article>')

    def result(self, html=None, error=None):
        return {'source': m.SPECIALS, 'error': error, 'checks': 2, 'checked_at': 1000,
                'offers': {} if error else m.parse_promotions(html or self.card(), m.SPECIALS)}

    def test_scoped_specs_and_prices_reject_ambiguous_incomplete_markup(self):
        offers = m.parse_promotions(self.card() + self.card(1002, '2', '15.99'), m.SPECIALS)
        self.assertEqual(offers['pid:1001']['annual_cents'], 2999)
        self.assertEqual(offers['pid:1002']['ram_mb'], 2048)
        for html in [self.card() * 2, self.card().replace('4 GB RAM', '8 GB RAM'),
                     self.card().replace('/year', '/month'), '<h3>4 GB KVM VPS</h3>',
                     self.card().replace('</ul>', '</ul><p>$39.99/year</p>')]:
            with self.subTest(html=html), self.assertRaises(m.EvidenceError):
                m.parse_promotions(html, m.SPECIALS)

    def test_button_is_never_official_stock_confirmation(self):
        offer = self.result()['offers']['pid:1001']
        self.assertNotIn('stock', offer)
        self.assertEqual(offer['listing'], 'order_link')
        html = self.card().replace('</ul>', '</ul><p>0 Available</p>')
        self.assertEqual(self.result(html)['offers']['pid:1001']['listing'], 'sold_out')

    def test_robots_denial_unreadable_and_failure_never_fetch_list(self):
        for content in ['User-agent: *\nDisallow: /specials/',
                        'User-agent: *\nDisallow: /*specials*', '<html>challenge</html>']:
            fetch = MagicMock(interval=2)
            fetch.get.return_value = content, m.SPECIALS_ROBOTS
            result = m.scan_promotions(fetch, wait=MagicMock())
            self.assertTrue(result['error'])
            fetch.get.assert_called_once_with(m.SPECIALS_ROBOTS)
        fetch.get.side_effect = HTTPError(m.SPECIALS_ROBOTS, 403, '', {}, None)
        self.assertEqual(m.scan_promotions(fetch)['error'], 'http_403')

    def test_two_read_confirmation_and_redirect_boundaries(self):
        fetch = MagicMock(interval=2)
        fetch.get.side_effect = [('User-agent: *\nDisallow: /cgi-bin/\nCrawl-delay: 5', m.SPECIALS_ROBOTS),
                                 (self.card(), m.SPECIALS), (self.card(price='30.99'), m.SPECIALS)]
        wait = MagicMock()
        self.assertEqual(m.scan_promotions(fetch, wait)['error'], 'promotion_confirmation_conflict')
        self.assertEqual(fetch.interval, 5)
        wait.assert_called_once_with(20)
        with self.assertRaises(m.EvidenceError):
            m.NoPromotionRedirect().redirect_request(None, None, 302, '', {}, m.OFFICIAL)
        with self.assertRaises(m.EvidenceError):
            m.PromotionFetcher().get('https://racknerd.club/product/925')

    def test_silent_baseline_filters_new_digest_and_delivery_dedup(self):
        state = m.empty_state()
        self.assertEqual(m.observe_promotions(state, self.result(), now=1000), [])
        html = self.card() + self.card(1002) + self.card(1003, '3.5') + self.card(1004, price='35.01') + self.card(925)
        ready = m.observe_promotions(state, self.result(html), now=2000)
        self.assertEqual(ready, ['promotions'])
        self.assertEqual(state['pending']['promotions']['items'], ['pid:1002'])
        messages = []
        m.deliver(state, ready, messages.append, lambda value: None)
        self.assertIn('库存、结账价格', messages[0])
        self.assertIn('官方公开促销列表', messages[0])
        self.assertEqual(m.observe_promotions(state, self.result(html), now=3000), [])
        self.assertEqual(state['sources'], {})

    def test_price_threshold_and_no_repeat_on_bounce(self):
        state = m.empty_state()
        m.observe_promotions(state, self.result(self.card(price='39.99')), now=1000)
        ready = m.observe_promotions(state, self.result(self.card(price='34.99')), now=2000)
        self.assertEqual(ready, ['promotions'])
        m.deliver(state, ready, lambda text: None, lambda value: None)
        m.observe_promotions(state, self.result(self.card(price='39.99')), now=3000)
        self.assertEqual(m.observe_promotions(state, self.result(self.card(price='34.99')), now=100000), [])
        self.assertEqual(m.observe_promotions(state, self.result(self.card(price='33.99')), now=100001), [])

    def test_restock_requires_recent_explicit_absence_not_missing_page(self):
        state = m.empty_state()
        m.observe_promotions(state, self.result(), now=1000)
        m.observe_promotions(state, self.result(error='http_404'), now=2000)
        self.assertEqual(m.observe_promotions(state, self.result(), now=3000), [])
        m.observe_promotions(state, self.result(self.card(label='Sold Out')), now=4000)
        self.assertEqual(m.observe_promotions(state, self.result(), now=5000), ['promotions'])
        m.deliver(state, ['promotions'], lambda text: None, lambda value: None)
        m.observe_promotions(state, self.result(self.card(label='Sold Out')), now=6000)
        self.assertEqual(m.observe_promotions(state, self.result(), now=7000), [])  # cooldown
        m.observe_promotions(state, self.result(self.card(label='Sold Out')), now=8000)
        self.assertEqual(m.observe_promotions(state, self.result(), now=200000), [])  # stale absence

    def test_failed_send_revalidated_cancelled_and_old_v2_preserved(self):
        state = m.empty_state()
        state['sources'] = {'club': {'stock': False}}
        m.observe_promotions(state, self.result(), now=1000)
        html = self.card() + self.card(1002)
        ready = m.observe_promotions(state, self.result(html), now=2000)
        with patch.object(m.LOG, 'warning'):
            m.deliver(state, ready, MagicMock(side_effect=OSError('secret')), lambda value: None)
        self.assertTrue(state['pending'])
        self.assertEqual(m.observe_promotions(state, self.result(error='http_403'), now=3000), [])
        self.assertEqual(m.observe_promotions(state, self.result(html), now=4000), ['promotions'])
        self.assertEqual(m.observe_promotions(state, self.result(self.card() + self.card(1002, label='Sold Out')), now=5000), [])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            m.save_state(path, state)
            self.assertEqual(m.read_state(path), state)
        self.assertEqual(state['sources'], {'club': {'stock': False}})

    def test_disabled_by_default_and_existing_dry_run_never_probes_new_source(self):
        self.assertFalse(CONFIG['promotions_enabled'])
        with tempfile.TemporaryDirectory() as temp, patch.object(m, 'scan', return_value=observations()), \
                patch.object(m, 'scan_promotions') as scan, patch.object(m, 'send_telegram') as send, patch('builtins.print'):
            self.assertEqual(m.main(['--runtime-dir', temp, '--dry-run']), 0)
            scan.assert_not_called()
            send.assert_not_called()

    def test_expired_price_event_does_not_restart_its_retry_window(self):
        state = m.empty_state()
        m.observe_promotions(state, self.result(self.card(price='39.99')), now=1000)
        self.assertEqual(m.observe_promotions(state, self.result(), now=2000), ['promotions'])
        self.assertEqual(m.observe_promotions(state, self.result(), now=200000), [])
        self.assertIsNone(state['promotions']['offers']['pid:1001']['event'])

    def test_opt_in_dry_run_does_not_send_read_credentials_or_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            state = m.empty_state()
            m.observe_promotions(state, self.result(), now=1000)
            m.save_state(path, state)
            before = path.read_bytes()
            result = self.result(self.card() + self.card(1002, price='35.00'))
            with patch.object(m, 'scan', return_value=observations()), \
                    patch.object(m, 'scan_promotions', return_value=result), \
                    patch.object(m, 'send_telegram') as send, patch.object(m, 'telegram_settings') as settings, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(m.main(['--runtime-dir', temp, '--dry-run', '--check-promotions']), 0)
                self.assertEqual(json.loads(output.getvalue())['would_notify'][0]['kind'], 'promotions')
                send.assert_not_called()
                settings.assert_not_called()
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
