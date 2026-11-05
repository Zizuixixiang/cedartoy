#!/usr/bin/env python3
"""Exact 2025 restock monitor with an opt-in public promotion-list monitor."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "deploy/racknerd-monitor.json"
RUNTIME = ROOT / "data/racknerd-monitor"
LOG = logging.getLogger("racknerd-monitor")
OFFICIAL = "https://my.racknerd.com/index.php?rp=/store/blackfriday2025/4-gb-kvm-vps-black-friday-2025"
CLUB = "https://racknerd.club/en/black-friday-2025"
SPECIALS = "https://www.racknerd.com/specials/"
SPECIALS_ROBOTS = "https://www.racknerd.com/robots.txt"
USER_AGENT = "RackNerdOfferMonitor/2.0"
NAME = "4 GB KVM VPS (Black Friday 2025)"
TARGET = {"name": NAME, "ram_mb": 4096, "cpu_cores": 3, "disk_gb": 65,
          "annual_cents": 2998, "official_url": OFFICIAL, "club_url": CLUB}
ANNUAL = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:USD\s*)?(?:/\s*(?:year|yr)\b|per\s+year\b|annually\b)", re.I)


class EvidenceError(ValueError):
    """A fixed diagnostic code, never arbitrary response content."""


def safe_error(error):
    if isinstance(error, HTTPError):
        return "http_" + str(error.code)
    if isinstance(error, EvidenceError):
        return str(error)
    return type(error).__name__


def allowed_url(url):
    u = urlsplit(url)
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        raise EvidenceError("unapproved_url")
    if url in (SPECIALS, SPECIALS_ROBOTS):
        return url
    if u.hostname == "racknerd.club" and u.path == "/en/black-friday-2025" and not u.query:
        return url
    if u.hostname == "my.racknerd.com":
        # No third-party redirects, logins, arbitrary crawls, or affiliate hops.
        route = parse_qs(u.query).get("rp", [u.path])[0].rstrip("/")
        if route == parse_qs(urlsplit(OFFICIAL).query)["rp"][0]:
            return url
        if u.path == "/cart.php" and parse_qs(u.query).get("a") == ["confproduct"]:
            return url
    raise EvidenceError("unapproved_url")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self, interval=2, timeout=25):
        self.interval, self.timeout, self.last = interval, timeout, 0
        self.opener = build_opener(ProxyHandler({}), SafeRedirect())

    def get(self, url):
        allowed_url(url)
        time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = Request(url, headers={
            "User-Agent": USER_AGENT + " (twice daily)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache, max-age=0",
        })
        # Deliberately no response cache: the confirmation must be a new request.
        with self.opener.open(req, timeout=self.timeout) as response:
            final = allowed_url(response.url)
            if urlsplit(final).hostname != urlsplit(url).hostname:
                raise EvidenceError("cross_source_redirect")
            if response.headers.get("cf-mitigated", "").lower() == "challenge":
                raise EvidenceError("access_challenge")
            if int(response.headers.get("Age", "0")) > 300:
                raise EvidenceError("stale_http_cache")
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise EvidenceError("page_too_large")
            return body.decode("utf-8", errors="replace"), final


class NoPromotionRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EvidenceError("promotion_redirect_refused")


class PromotionFetcher(Fetcher):
    def __init__(self):
        super().__init__()
        self.opener = build_opener(ProxyHandler({}), NoPromotionRedirect())

    def get(self, url):
        if url not in (SPECIALS, SPECIALS_ROBOTS):
            raise EvidenceError("unapproved_promotion_url")
        return super().get(url)


class Node:
    def __init__(self, tag="document", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    @property
    def text(self):
        if self.tag in ("script", "style", "noscript") or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
            return ""
        return " ".join(" ".join(c.text if isinstance(c, Node) else c for c in self.children).split())

    def nodes(self, tag=None):
        if self.tag in ("script", "style", "noscript") or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
            return
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes(tag)


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def disabled(node):
    a = node.attrs
    return "disabled" in a or a.get("aria-disabled") == "true" or "disabled" in a.get("class", "").split()


def annual_prices(text):
    return {int(Decimal(m[1]) * 100) for m in ANNUAL.finditer(text)}


def check_specs(text, club=False):
    patterns = [r"\b4\s*GB\s+(?:DDR\d*\s+)?RAM\b",
                r"\b65\s*G(?:B)?\s+(?:PURE\s+)?SSD(?:\s+RAID-10)?\s+Storage\b",
                r"\b3\s+vCore\s+CPU\b" if club else r"\b3\s*x?\s*vCPU\s+Cores?\b"]
    if not all(re.search(p, text, re.I) for p in patterns):
        raise EvidenceError("target_specs_mismatch")


def parse_official(html, url, now=None):
    allowed_url(url)
    root = Document(html).root
    if any(n.tag == "title" and "just a moment" in n.text.lower() for n in root.nodes()):
        raise EvidenceError("access_challenge")
    text = root.text
    exact_route = parse_qs(urlsplit(url).query).get("rp", [urlsplit(url).path])[0] == parse_qs(urlsplit(OFFICIAL).query)["rp"][0]
    if exact_route and re.search(r"the plan you selected is out of stock", text, re.I):
        return False
    if NAME.lower() not in text.lower():
        raise EvidenceError("target_identity_missing")
    check_specs(text)
    for form in root.nodes("form"):
        if disabled(form):
            continue
        hostname = any(n.attrs.get("name") == "hostname" and not disabled(n) for n in form.nodes("input"))
        for select in form.nodes("select"):
            if not hostname or select.attrs.get("name") != "billingcycle" or disabled(select):
                continue
            for option in select.nodes("option"):
                if option.attrs.get("value") == "annually" and not disabled(option) and annual_prices(option.text) == {2998}:
                    return True
    raise EvidenceError("checkout_not_confirmed")


def parse_club(html, url, now=None):
    if url != CLUB:
        raise EvidenceError("club_wrong_page")
    root = Document(html).root
    if "2025" not in root.text or "Black Friday" not in root.text:
        raise EvidenceError("target_identity_missing")
    cards = [n for n in root.nodes("div") if "single-pricing-pack" in n.attrs.get("class", "").split()
             and any(h.text == "4GB Package" for h in n.nodes("h3"))]
    if len(cards) != 1:
        raise EvidenceError("target_card_missing_or_ambiguous")
    card = cards[0]
    check_specs(card.text, club=True)
    if annual_prices(card.text) != {2998}:
        raise EvidenceError("target_price_mismatch")
    # If the publisher adds an update date, stale/unparseable dates cannot vote.
    updated = re.search(r"Last\s+Updated\s*:?\s*(\S+)", root.text, re.I)
    if updated:
        try:
            stamp = datetime.fromisoformat(updated[1].replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
        except ValueError:
            raise EvidenceError("unreadable_source_timestamp") from None
        if not -300 <= (now if now is not None else time.time()) - stamp <= 86400:
            raise EvidenceError("stale_source_timestamp")
    links = [n for n in card.nodes("a") if n.attrs.get("href") in ("/product/925", "https://racknerd.club/product/925")]
    if len(links) != 1:
        raise EvidenceError("target_link_mismatch")
    label = links[0].text.lower()
    if label in ("sold out", "out of stock"):
        return False
    if label in ("available", "in stock", "order now", "buy now") and not disabled(links[0]):
        return True  # A changed button is only auxiliary evidence, never official stock.
    raise EvidenceError("unrecognized_stock_label")


def validate_config(config):
    if config.get("target") != TARGET:
        raise EvidenceError("target_config_mismatch")
    if not 10 <= config["confirm_delay_seconds"] <= 60 or not 1 <= config["failure_threshold"] <= 10:
        raise EvidenceError("invalid_config")
    if not 43200 <= config["baseline_max_age_seconds"] <= 172800:
        raise EvidenceError("invalid_baseline_age")
    if not isinstance(config.get("promotions_enabled", False), bool):
        raise EvidenceError("invalid_promotions_config")


def scan(config, fetcher, state, wait=time.sleep):
    observations = {}
    for source, url, parse in (("official", OFFICIAL, parse_official), ("club", CLUB, parse_club)):
        try:
            html, final = fetcher.get(url)
            stock = parse(html, final, time.time())
            observations[source] = {"stock": stock, "error": None, "url": url,
                                    "checked_at": time.time(), "checks": 1,
                                    "evidence_hash": hashlib.sha256(html.encode()).hexdigest()}
        except Exception as error:
            observations[source] = {"stock": None, "error": safe_error(error), "url": url,
                                    "checked_at": time.time(), "checks": 1}
    # Confirm every positive and every changed negative, including first baseline.
    confirm = [s for s, o in observations.items() if o["stock"] is not None and
               (o["stock"] is True or state["sources"].get(s, {}).get("stock") is not o["stock"])]
    if confirm:
        wait(config["confirm_delay_seconds"])
    for source in confirm:
        item = observations[source]
        try:
            html, final = fetcher.get(item["url"])
            second = (parse_official if source == "official" else parse_club)(html, final, time.time())
            if second is not item["stock"]:
                raise EvidenceError("confirmation_conflict")
            item["checks"] = 2
            item["evidence_hash"] = hashlib.sha256(html.encode()).hexdigest()
        except Exception as error:
            item["stock"], item["error"] = None, safe_error(error)
        item["checked_at"] = time.time()
    return observations


def check_promotions_robots(fetcher):
    """Fail closed; only the reviewed public list is fetched, never its order links."""
    text, final = fetcher.get(SPECIALS_ROBOTS)
    if final != SPECIALS_ROBOTS or not re.search(r"(?im)^user-agent\s*:", text) or "<html" in text.lower():
        raise EvidenceError("robots_unreadable")
    robots = RobotFileParser()
    robots.parse(text.splitlines())
    if not robots.can_fetch(USER_AGENT, SPECIALS):
        raise EvidenceError("robots_disallowed")
    delay = robots.crawl_delay(USER_AGENT) or 0
    rate = robots.request_rate(USER_AGENT)
    delay = max(delay, rate.seconds / rate.requests if rate and rate.requests else 0)
    if delay > 60:
        raise EvidenceError("robots_rate_not_supported")
    fetcher.interval = max(fetcher.interval, delay)
    # RobotFileParser does not implement wildcard paths. Conservatively honor
    # every matching wildcard Disallow, including those for other agents.
    for line in text.splitlines():
        key, _, value = line.partition("#")[0].partition(":")
        value = value.strip()
        if key.strip().lower() == "disallow" and ("*" in value or "$" in value):
            pattern = re.escape(value.rstrip("$")).replace(r"\*", ".*")
            if re.match(pattern + ("$" if value.endswith("$") else ""), "/specials/"):
                raise EvidenceError("robots_disallowed")


def promotion_link(href):
    """Normalize identity without requesting the checkout or affiliate URL."""
    u = urlsplit(href)
    if u.scheme != "https" or u.netloc != "my.racknerd.com" or u.fragment:
        return None
    query = parse_qs(u.query)
    if u.path == "/cart.php" and query.get("a") == ["add"] and len(query.get("pid", [])) == 1:
        pid = query["pid"][0]
        if re.fullmatch(r"[1-9]\d{0,6}", pid):
            return "pid:" + pid, "https://my.racknerd.com/cart.php?a=add&pid=" + pid
    route = query.get("rp", [u.path])[0]
    if re.fullmatch(r"/store/[a-z0-9-]+/[a-z0-9-]+", route):
        return "route:" + route, "https://my.racknerd.com/index.php?" + urlencode({"rp": route})
    return None


def parse_promotions(html, url):
    if url != SPECIALS:
        raise EvidenceError("promotions_wrong_page")
    root = Document(html).root
    titles = [n for n in root.nodes("h3") if re.fullmatch(r"\d+(?:\.\d+)?\s*GB\s+KVM VPS", n.text, re.I)]
    if not titles:
        raise EvidenceError("promotion_cards_missing")
    offers = {}
    for title in titles:
        # Use the smallest container with a single plan heading and its order
        # link. Never combine prices/specs from adjacent cards or the demo UI.
        cards = [n for n in root.nodes() if n.tag in ("div", "article", "section", "li")
                 and title in list(n.nodes("h3")) and len(list(n.nodes("h3"))) == 1
                 and any(promotion_link(a.attrs.get("href", "")) for a in n.nodes("a"))
                 and annual_prices(n.text)]
        if not cards:
            raise EvidenceError("promotion_card_incomplete")
        card = cards[-1]
        ram = {int(Decimal(v) * 1024) for v in re.findall(r"\b(\d+(?:\.\d+)?)\s*GB\s+(?:DDR\d*\s+)?RAM\b", card.text, re.I)}
        prices = annual_prices(card.text)
        links = {p for a in card.nodes("a") if (p := promotion_link(a.attrs.get("href", "")))}
        if len(ram) != 1 or len(prices) != 1 or len(links) != 1:
            raise EvidenceError("promotion_card_ambiguous")
        memory, price = ram.pop(), prices.pop()
        if memory != int(Decimal(re.match(r"\d+(?:\.\d+)?", title.text)[0]) * 1024) or price <= 0:
            raise EvidenceError("promotion_specs_mismatch")
        key, order_url = links.pop()
        if key in offers:
            raise EvidenceError("promotion_duplicate_identity")
        labels = [a for a in card.nodes("a") if promotion_link(a.attrs.get("href", ""))]
        sold_out = bool(re.search(r"\b(?:sold out|out of stock|0 available)\b", card.text, re.I))
        order_link = any(a.text.lower() in ("order now", "buy now", "available", "in stock") and not disabled(a) for a in labels)
        offers[key] = {"name": title.text, "ram_mb": memory, "annual_cents": price,
                       "listing": "sold_out" if sold_out else "order_link" if order_link else "unknown",
                       "order_url": order_url}
    return offers


def scan_promotions(fetcher, wait=time.sleep):
    result = {"source": SPECIALS, "error": None, "offers": {}, "checks": 0}
    try:
        check_promotions_robots(fetcher)
        first = parse_promotions(*fetcher.get(SPECIALS))
        result["checks"] = 1
        wait(20)
        second = parse_promotions(*fetcher.get(SPECIALS))
        result["checks"] = 2
        if first != second:
            raise EvidenceError("promotion_confirmation_conflict")
        result["offers"] = second
    except Exception as error:
        result["error"] = safe_error(error)
    result["checked_at"] = time.time()
    return result


def observe_promotions(state, result, now=None):
    """A separate, silent first baseline. Missing pages never mean sold out."""
    now = time.time() if now is None else now
    promo = state.setdefault("promotions", {"offers": {}, "initialized": False})
    promo["last_read"] = {k: v for k, v in result.items() if k != "offers"}
    state["pending"].pop("promotions", None)  # Rebuild only from current evidence.
    if result["error"]:
        return []
    events = []
    for key, offer in result["offers"].items():
        # 925 remains exclusively owned by the original exact-plan monitor.
        if key in ("pid:925", promotion_link(OFFICIAL)[0]):
            continue
        previous = promo["offers"].get(key)
        record = promo["offers"].setdefault(key, {"reference_price": offer["annual_cents"]})
        old = copy.deepcopy(record)
        record.update({"offer": offer, "seen_at": now})
        eligible = offer["ram_mb"] >= 4096 and offer["annual_cents"] <= 3500 and offer["listing"] != "sold_out"
        reason = None
        if eligible and promo["initialized"]:
            if previous is None:
                reason = "新发现套餐（不代表刚上架）"
            elif (old["reference_price"] - offer["annual_cents"] >= max(200, old["reference_price"] * .1)
                  and offer["annual_cents"] < old["offer"]["annual_cents"]):
                reason = "列表价格明显下降"
            elif old.get("offer", {}).get("listing") == "sold_out" and offer["listing"] == "order_link" and 0 <= now - old.get("seen_at", 0) <= 172800:
                reason = "疑似补货（列表按钮变化）"
        pending = old.get("event")
        if pending and (pending["offer"] != offer or not 0 <= now - pending["created_at"] <= 172800):
            pending = None
        if reason and not pending:
            pending = {"reason": reason, "offer": copy.deepcopy(offer), "created_at": now}
        record["event"] = pending if eligible else None
        if record["event"] and ("notified_at" not in record or now - record["notified_at"] >= 86400):
            events.append((key, record["event"]))
    promo["initialized"] = True
    if not events:
        return []
    # One digest per scan, at most five plans; the remainder waits for revalidation.
    events = events[:5]
    lines = ["RackNerd 促销列表线索 · CedarStar/CedarClio 候选",
             "来源：官方公开促销列表 " + SPECIALS,
             "库存、结账价格、续费及机房未核实；Order Now 不等于有货。请手动核验。"]
    for key, event in events:
        offer = event["offer"]
        lines.append(f"{event['reason']}：{offer['name']} · ${offer['annual_cents'] / 100:.2f}/年\n{offer['order_url']}")
    state["pending"]["promotions"] = {"text": "\n".join(lines), "items": [key for key, _ in events], "created_at": now}
    return ["promotions"]


def empty_state():
    return {"version": 2, "target": OFFICIAL, "sources": {}, "pending": {},
            "failure_count": 0, "failure_alerted": False}


def read_state(path):
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text())
    if state.get("version") == 1:
        if any(not isinstance(state.get(k), dict) for k in ("offers", "failures", "pending")):
            raise EvidenceError("invalid_state_preserved")
        migrated = empty_state()
        migrated["legacy_v1"] = state  # Preserve old history; never deliver its broad-plan outbox.
        return migrated
    if state.get("version") != 2 or state.get("target") != OFFICIAL or any(not isinstance(state.get(k), dict) for k in ("sources", "pending")):
        raise EvidenceError("invalid_state_preserved")
    if "promotions" in state:
        promo = state["promotions"]
        if not isinstance(promo, dict) or not isinstance(promo.get("offers"), dict) or not isinstance(promo.get("initialized"), bool):
            raise EvidenceError("invalid_state_preserved")
    return state


def health(observations):
    if observations["official"]["stock"] is not None:
        return "official_confirmed"
    return "auxiliary_only" if observations["club"]["stock"] is not None else "unavailable"


def notification(kind, official_error):
    title = "官方订购页已确认可配置此套餐" if kind == "confirmed" else "疑似补货，官方站无法自动复核，请手动打开链接"
    text = f"RackNerd：{title}\nBlack Friday 2025 · 4GB / 3核 / 65GB SSD · $29.98/年\n{OFFICIAL}"
    if kind == "suspected":
        text += (f"\n辅助来源：{CLUB}\n此前 Sold Out，本次两次独立读取均显示可订购。"
                 f"页面未提供库存更新时间；这只是第三方页面变化，不是官方库存确认。\n官方复核：{official_error}")
    return text


def observe(state, observations, config, initialize=False, now=None):
    now = time.time() if now is None else now
    previous = copy.deepcopy(state["sources"])
    ready = []
    for source, current in observations.items():
        record = state["sources"].setdefault(source, {"stock": None, "notified": False})
        record["last_read"] = copy.deepcopy(current)
        if current["stock"] is not None:
            record["stock"] = current["stock"]
            record["last_definite_at"] = current["checked_at"]
            if current["stock"] is False:
                record["notified"] = False
                state["pending"].pop("confirmed" if source == "official" else "suspected", None)
    official, club = observations["official"]["stock"], observations["club"]["stock"]
    if official is not None:
        state["pending"].pop("suspected", None)  # Official stock/absence takes precedence.
    if official is True and not state["sources"]["official"].get("notified") and not initialize:
        state["pending"]["confirmed"] = {"text": notification("confirmed", None), "created_at": now}
        ready.append("confirmed")
    if official is None and club is True and not initialize:
        old = previous.get("club", {})
        recent_absence = old.get("stock") is False and 0 <= now - old.get("last_definite_at", 0) <= config["baseline_max_age_seconds"]
        if recent_absence and not state["sources"]["club"].get("notified"):
            state["pending"]["suspected"] = {"text": notification("suspected", observations["official"]["error"]), "created_at": now}
        pending = state["pending"].get("suspected")
        if pending:
            if 0 <= now - pending["created_at"] <= config["baseline_max_age_seconds"]:
                # Refresh the diagnostic but never relabel auxiliary evidence as confirmed.
                pending["text"] = notification("suspected", observations["official"]["error"])
                ready.append("suspected")
            else:
                state["pending"].pop("suspected")
    if initialize:
        # First available observation is not a third-party restock transition.
        for record in state["sources"].values():
            record["notified"] = record["stock"] is True
    if health(observations) == "unavailable":
        state["failure_count"] += 1
        if state["failure_count"] >= config["failure_threshold"] and not state["failure_alerted"] and not initialize:
            state["pending"]["failure"] = {"text": "RackNerd 目标套餐监控：所有信号已连续检查失败，无法判断库存；不会据此发补货通知。\n" + OFFICIAL}
            ready.append("failure")
    else:
        state["failure_count"], state["failure_alerted"] = 0, False
        state["pending"].pop("failure", None)
    state["last_check"] = now
    return ready


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
        pending = state["pending"].pop(key)
        if key == "failure":
            state["failure_alerted"] = True
        elif key == "promotions":
            for item in pending["items"]:
                record = state["promotions"]["offers"][item]
                record["reference_price"] = record["offer"]["annual_cents"]
                record["notified_at"] = pending["created_at"]
                record["event"] = None
        else:
            state["sources"]["official" if key == "confirmed" else "club"]["notified"] = True
        persist(state)
        sent += 1
    return sent, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME)
    parser.add_argument("--check-promotions", action="store_true", help="include the unverified public-list parser in --dry-run only")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="fetch and print; never send or change state")
    modes.add_argument("--initialize", action="store_true", help="silently seed target baseline, including migration from v1; refuses an existing target baseline")
    args = parser.parse_args(argv)
    if args.check_promotions and not args.dry_run:
        parser.error("--check-promotions requires --dry-run")
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
        validate_config(config)
        if (args.runtime_dir / "PAUSED").exists() and not args.dry_run:
            return 0
        with (args.runtime_dir / "monitor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("RackNerd monitor: another scan holds the lock")
                return 1 if args.dry_run or args.initialize else 0
            path = args.runtime_dir / "state.json"
            state = read_state(path)
            if args.initialize and state["sources"]:
                raise EvidenceError("baseline_already_exists")
            observations = scan(config, Fetcher(), state)
            mode = health(observations)
            if args.initialize and mode == "unavailable":
                raise EvidenceError("baseline_requires_usable_signal")
            ready = observe(state, observations, config, args.initialize)
            promotions = None
            if config.get("promotions_enabled", False) or args.check_promotions:
                promotions = scan_promotions(PromotionFetcher())
                ready += observe_promotions(state, promotions)
                LOG.info("promotions_scan error=%s plans=%d checks=%d", promotions["error"], len(promotions["offers"]), promotions["checks"])
            report = {"target": TARGET, "mode": mode, "observations": observations,
                      "promotions": promotions, "promotions_enabled": config.get("promotions_enabled", False),
                      "would_notify": [{"kind": k, "text": state["pending"][k]["text"]} for k in ready],
                      "limitation": "Club has no inventory update timestamp; changes are unverified auxiliary clues only."}
            if args.dry_run:
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 1 if mode == "unavailable" or (promotions and promotions["error"]) else 0
            save_state(path, state)
            if args.initialize:
                sent, ok = 0, True
                print("Target baseline saved silently; mode=" + mode)
            else:
                sent, ok = deliver(state, ready, lambda text: send_telegram(text, Path(config["telegram_env"])), lambda value: save_state(path, value))
            LOG.info("target_scan mode=%s official=%s official_error=%s club=%s club_error=%s sent=%d pending=%d",
                     mode, observations["official"]["stock"], observations["official"]["error"],
                     observations["club"]["stock"], observations["club"]["error"], sent, len(state["pending"]))
            return 0 if mode != "unavailable" and ok else 1
    except Exception as error:
        LOG.error("run_failed kind=%s", safe_error(error))
        print("RackNerd monitor failed: " + safe_error(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
