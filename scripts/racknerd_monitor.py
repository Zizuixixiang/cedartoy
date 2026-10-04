#!/usr/bin/env python3
"""Twice-daily RackNerd offer monitor. No application imports or third-party deps."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
from decimal import Decimal
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "deploy/racknerd-monitor.json"
RUNTIME = ROOT / "data/racknerd-monitor"
LOG = logging.getLogger("racknerd-monitor")
HOSTS = {"racknerd.com", "www.racknerd.com", "my.racknerd.com"}
TITLE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(GB|MB)\s+(?:KVM\s+)?VPS\b(?:\s*\([^)]{1,90}\)|\s+Special\b)?", re.I)
ANNUAL = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:USD\s*)?(?:/\s*(?:year|yr)\b|per\s+year\b|annually\b)", re.I)
SOLD_OUT = re.compile(r"\bout\s+of\s+stock\b|\bsold\s*out\b|\bunavailable\b|\b0\s+available\b", re.I)


def official_url(url):
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in HOSTS or u.username or u.password or u.port not in (None, 443):
        raise ValueError("non_official_url")
    return url


def product_key(url):
    u = urlsplit(official_url(url))
    q = parse_qs(u.query)
    if q.get("pid", [""])[0].isdigit():
        return "pid:" + q["pid"][0]
    route = q.get("rp", [u.path])[0].rstrip("/")
    if route.startswith("/store/") and len(route.split("/")) >= 4:
        return "store:" + route.lower()
    return None


class Page(HTMLParser):
    """Flatten visible markup, retaining order-link offsets; ignore scripts/styles."""
    def __init__(self, html, url):
        super().__init__(convert_charrefs=True)
        self.url, self.parts, self.links, self.inputs = url, [], [], {}
        self.skip = 0
        self.anchor = None
        self.size = 0
        self.feed(html)
        self.text = "".join(self.parts)

    def append(self, text):
        self.parts.append(text)
        self.size += len(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if self.skip:
            return
        self.append(" ")
        if tag == "a":
            self.anchor = (self.size, urljoin(self.url, attrs.get("href", "")), attrs)
        if tag in ("input", "select") and attrs.get("name"):
            self.inputs[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "a" and self.anchor:
            start, url, attrs = self.anchor
            self.links.append((start, self.size, url, attrs))
            self.anchor = None
        self.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.append(re.sub(r"\s+", " ", data))


def cents(value):
    return int(Decimal(value) * 100)


def parse_offers(html, url):
    page = Page(html, url)
    titles = list(TITLE.finditer(page.text))
    offers = {}
    for i, title in enumerate(titles):
        end = titles[i + 1].start() if i + 1 < len(titles) else len(page.text)
        links = []
        for start, stop, href, attrs in page.links:
            if title.end() <= start < end:
                try:
                    key = product_key(href)
                except ValueError:
                    continue
                if key:
                    links.append((start, stop, href, attrs, key))
        if not links:
            continue
        start, stop, href, attrs, key = links[0]
        block = page.text[title.end():stop]
        prices = list(ANNUAL.finditer(block))
        if not prices:
            continue  # Monthly/two-year prices must never masquerade as annual.
        price = cents(prices[-1][1])
        ram = int(Decimal(title[1]) * (1024 if title[2].upper() == "GB" else 1))
        ram_spec = re.search(r"\b(\d+(?:\.\d+)?)\s*(GB|MB)\s+(?:DDR\d*\s+)?RAM\b", block, re.I)
        if ram_spec:
            actual = int(Decimal(ram_spec[1]) * (1024 if ram_spec[2].upper() == "GB" else 1))
            if actual != ram:
                raise ValueError("ram_mismatch")
        count = re.search(r"\b(\d+)\s+Available\b", block, re.I)
        disabled = "disabled" in attrs or attrs.get("aria-disabled") == "true" or "disabled" in attrs.get("class", "").split()
        stock = False if SOLD_OUT.search(block) or disabled else (int(count[1]) > 0 if count else None)
        offers[key] = {
            "key": key, "name": " ".join(title[0].split()), "ram_mb": ram,
            "annual_cents": price, "stock": stock, "url": href, "source": url,
        }
    if not offers:
        raise ValueError("no_annual_vps_parsed")
    return list(offers.values())


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self, interval=2, timeout=25):
        self.interval, self.timeout, self.last = interval, timeout, 0
        self.cache = {}
        self.opener = build_opener(ProxyHandler({}), OfficialRedirect())

    def get(self, url):
        official_url(url)
        if url not in self.cache:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            req = Request(url, headers={"User-Agent": "RackNerdOfferMonitor/1.0 (twice daily)", "Accept-Language": "en-US,en;q=0.9"})
            with self.opener.open(req, timeout=self.timeout) as response:
                final = official_url(response.url)
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError("page_too_large")
                result = (body.decode("utf-8", errors="replace"), final)
            self.cache[url] = result
            self.cache[final] = result
        return self.cache[url]


def checkout_stock(html, url, offer):
    page = Page(html, url)
    if SOLD_OUT.search(page.text):
        return False
    # A stale order link can redirect to another catalogue. Demand a real server
    # configuration form, the selected product and the expected annual amount.
    prices = {cents(m[1]) for m in ANNUAL.finditer(page.text)}
    same_product = offer["name"].lower() in " ".join(page.text.split()).lower()
    if "hostname" in page.inputs and "billingcycle" in page.inputs and same_product and offer["annual_cents"] in prices:
        return True
    return None


def eligible(offer, config):
    return offer["ram_mb"] == config["ram_mb"] and offer["annual_cents"] <= config["max_annual_cents"]


def scan(config, fetcher):
    offers, failures, succeeded = {}, {}, []
    for source in config["sources"]:
        try:
            html, final = fetcher.get(source)
            parsed = parse_offers(html, final)
            uncertain = False
            for offer in parsed:
                # Only affordable target plans need additional stock requests.
                if eligible(offer, config) and offer["stock"] is not False:
                    try:
                        body, checkout_url = fetcher.get(offer["url"])
                        offer["stock"] = checkout_stock(body, checkout_url, offer)
                        canonical = product_key(checkout_url)
                        if canonical:
                            offer["key"] = canonical
                    except Exception:
                        offer["stock"] = None
                    uncertain |= offer["stock"] is None
                old = offers.get(offer["key"])
                if old and (old["annual_cents"] != offer["annual_cents"] or old["stock"] != offer["stock"]):
                    # Conflicting pages cannot establish a reliable purchase state.
                    offer["stock"] = None
                    uncertain = True
                offers[offer["key"]] = offer
            if uncertain:
                failures[source] = "stock_unknown"
            else:
                succeeded.append(source)
        except Exception as error:
            failures[source] = safe_error(error)
    return list(offers.values()), failures, succeeded


def safe_error(error):
    # Exception messages/tracebacks may embed Telegram URLs and credentials.
    return "http_" + str(error.code) if isinstance(error, HTTPError) else type(error).__name__


def empty_state():
    return {"version": 1, "offers": {}, "failures": {}, "pending": {}}


def read_state(path):
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text())
    if state.get("version") != 1 or any(not isinstance(state.get(k), dict) for k in ("offers", "failures", "pending")):
        raise ValueError("invalid_state_preserved")
    return state


def save_state(path, state):
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(state, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def signature(offer):
    return [offer["ram_mb"], offer["annual_cents"], offer["stock"]]


def observe(state, offers, failures, succeeded, config, initialize=False):
    """Persist observations separately from the notification outbox."""
    ready = []
    for offer in offers:
        key = offer["key"]
        old = state["offers"].get(key)
        pending = state["pending"].get(key)
        if offer["stock"] is None:
            # No evidence of stock change: preserve last definitive observation.
            continue
        if pending and pending["signature"] != signature(offer):
            state["pending"].pop(key)
        reasons = []
        if eligible(offer, config):
            if old is None or not eligible(old, config):
                reasons.append("新符合条件套餐")
            else:
                if old["stock"] is False and offer["stock"] is True:
                    reasons.append("补货，可购买")
                if offer["annual_cents"] < old["annual_cents"]:
                    reasons.append("年付降价")
        if reasons and not initialize:
            price = offer["annual_cents"] / 100
            status = "有货，可购买" if offer["stock"] else "无货，继续监控"
            text = f"RackNerd：{' / '.join(reasons)}\n{offer['name']}\n{offer['ram_mb'] / 1024:g}GB RAM · ${price:.2f}/年\n{status}\n{offer['url']}"
            state["pending"][key] = {"signature": signature(offer), "text": text}
        state["offers"][key] = copy.deepcopy(offer)
        if key in state["pending"]:
            ready.append(key)
    for source in succeeded:
        state["failures"].pop(source, None)
    for source, error in failures.items():
        item = state["failures"].setdefault(source, {"count": 0})
        item["count"] += 1
        item["error"] = error
    # One aggregated incident, even if DNS/network failure affects every source.
    if not failures:
        state["failure_alerted"] = False
        state["pending"].pop("failure", None)
    failing = [url for url in failures if state["failures"][url]["count"] >= config["failure_threshold"]]
    if failing and not state.get("failure_alerted") and not initialize:
        state["pending"]["failure"] = {"text": f"RackNerd 监控：{len(failing)} 个来源已连续失败至少 {config['failure_threshold']} 次。\n" + "\n".join(failing) + "\n恢复前不重复告警；下一次按每天两次的计划检查。"}
        ready.append("failure")
    state["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return ready


def telegram_settings(path):
    """Read just the existing notification keys, without importing either app."""
    wanted = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_MAIN_USER_CHAT_ID", "TELEGRAM_PROXY"}
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        key = key.strip()
        if sep and key in wanted:
            parts = shlex.split(value, comments=True)
            values[key] = " ".join(parts)
    if not values.get("TELEGRAM_BOT_TOKEN") or not re.fullmatch(r"-?\d+", values.get("TELEGRAM_MAIN_USER_CHAT_ID", "")):
        raise ValueError("telegram_not_configured")
    return values


def send_telegram(text, env_path):
    settings = telegram_settings(env_path)
    proxy = settings.get("TELEGRAM_PROXY")
    if proxy and urlsplit(proxy).scheme not in ("http", "https"):
        raise ValueError("unsupported_proxy_scheme")
    opener = build_opener(ProxyHandler({"https": proxy} if proxy else {}))
    data = urlencode({"chat_id": settings["TELEGRAM_MAIN_USER_CHAT_ID"], "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    url = "https://api.telegram.org/bot" + settings["TELEGRAM_BOT_TOKEN"] + "/sendMessage"
    # No automatic retries: an ambiguous timeout may already have delivered.
    with opener.open(Request(url, data=data), timeout=30) as response:
        result = json.loads(response.read(100_000))
    if result.get("ok") is not True or not result.get("result", {}).get("message_id"):
        raise ValueError("telegram_rejected")


def deliver(state, ready, send, persist):
    sent = 0
    for key in ready:
        try:
            send(state["pending"][key]["text"])
        except Exception as error:
            LOG.warning("telegram_failed kind=%s pending=%d", safe_error(error), len(ready) - sent)
            return sent, False
        state["pending"].pop(key)
        if key == "failure":
            state["failure_alerted"] = True
        persist(state)
        sent += 1
    return sent, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="fetch and print; never send or change state")
    modes.add_argument("--initialize", action="store_true", help="seed first baseline silently; refuses existing state")
    modes.add_argument("--test-telegram", action="store_true", help="send one explicit test; do not change monitoring state")
    args = parser.parse_args(argv)
    os.umask(0o077)
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.runtime_dir / "monitor.log", maxBytes=262144, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    for previous in LOG.handlers[:]:
        LOG.removeHandler(previous)
        previous.close()
    LOG.addHandler(handler)
    try:
        config = json.loads(args.config.read_text())
        if not config["sources"] or config["failure_threshold"] < 1:
            raise ValueError("invalid_config")
        for url in config["sources"]:
            official_url(url)
        env_path = Path(config["telegram_env"])
        if args.test_telegram:
            send_telegram("RackNerd 监控测试通知：Telegram 通道正常。\n计划北京时间每天 09:00、21:00 检查 4GB / ≤ $30 年付优惠与补货；实际启用状态以部署验收为准。", env_path)
            LOG.info("telegram_test success")
            print("Telegram test: success")
            return 0
        if (args.runtime_dir / "PAUSED").exists() and not args.dry_run:
            return 0
        with (args.runtime_dir / "monitor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            path = args.runtime_dir / "state.json"
            state = read_state(path)  # Corrupt state is preserved; never reset it.
            if args.initialize and path.exists():
                raise ValueError("baseline_already_exists")
            offers, failures, succeeded = scan(config, Fetcher())
            if args.initialize and failures:
                raise ValueError("baseline_requires_successful_scan")
            ready = observe(state, offers, failures, succeeded, config, args.initialize)
            if args.dry_run:
                print(json.dumps({"offers": offers, "failures": failures, "would_notify": len(ready)}, ensure_ascii=False, indent=2))
                return 1 if failures else 0
            save_state(path, state)
            sent, ok = deliver(state, ready, lambda text: send_telegram(text, env_path), lambda value: save_state(path, value))
            LOG.info("scan offers=%d matched=%d failed_sources=%d sent=%d pending=%d", len(offers), sum(eligible(o, config) for o in offers), len(failures), sent, len(state["pending"]))
            return 0 if not failures and ok else 1
    except Exception as error:
        LOG.error("run_failed kind=%s", safe_error(error))
        print("RackNerd monitor failed: " + safe_error(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
