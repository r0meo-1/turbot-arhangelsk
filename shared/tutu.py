"""Tutu.ru MCP integration — real transport offers for the lead funnel.

The bot's dialog already collects everything a search needs (направление,
даты, люди, бюджет). This module turns that into a live search against
Tutu's public MCP server and renders the result for two very different
audiences: the client (an orientation price, no checkout) and the manager
(a price anchor plus ready checkout links).

Design notes
------------
* **No new dependencies.** The server is a stateless JSON-RPC endpoint over
  plain HTTP POST — no auth, no ``initialize`` handshake, no session id, no
  SSE. The official MCP SDK would pull in anyio/httpx/pydantic for nothing,
  which matters on a 512 MB free-tier instance. ``requests`` is enough.
* **Never on the critical path.** Callers must invoke this from the
  post-completion background thread. A lead is already durable in SQLite
  before anything here runs.
* **Fail soft, always.** Every public entry point returns ``None`` instead of
  raising, so the caller can silently fall back to the template blurb.
"""

from __future__ import annotations

import html
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from shared.dates import parse_russian_dates

logger = logging.getLogger("turbot.shared.tutu")

# Injectable transport so tests never touch the network (mirrors shared/mdt.py).
RequestFn = Callable[[str, Dict[str, Any]], Optional[Any]]

MCP_PROTOCOL_VERSION = "2024-11-05"

# Argument names accepted by search_avia, taken verbatim from the server's
# published inputSchema. The server rejects anything else with a validation
# error, and a mocked test cannot catch an invented name — so the allowlist is
# asserted against in tests/test_tutu.py instead.
SEARCH_ARGS = frozenset({
    "origin", "destination", "departure_date", "return_date",
    "adults", "children", "infants", "service_class",
    "page", "page_size", "sort", "price_max", "direct_only", "carriers", "view",
})


@dataclass
class TutuSettings:
    """Runtime Tutu configuration (read live from bot env globals)."""

    enabled: bool = False
    endpoint: str = "https://mcp.tutu.ru/mcp"
    timeout: int = 12
    default_origin: str = "Архангельск"
    max_offers: int = 3
    cache_ttl: int = 900          # seconds; in-memory only (Render disk is ephemeral)
    cache_max_entries: int = 128  # hard cap so a busy day cannot grow unbounded
    show_client: bool = True
    show_admin: bool = True


# ---------------------------------------------------------------------------
# Destination resolution
#
# The funnel offers countries ("Египет"), but Tutu searches cities. Left alone
# it resolves a country to its capital — Египет → Каир — which is the wrong
# city for a beach agency and materially more expensive (measured on the live
# server: Архангельск→Каир 36 152 ₽ vs Архангельск→Хургада 24 539 ₽ for the
# same date). Map the countries we actually sell to the resort the client
# means; anything unknown is passed through untouched for Tutu to resolve.

COUNTRY_TO_RESORT: Dict[str, str] = {
    "египет": "Хургада",
    "турция": "Анталья",
    "таиланд": "Пхукет",
    "мальдивы": "Мале",
    "оаэ": "Дубай",
    "объединенные арабские эмираты": "Дубай",
    "эмираты": "Дубай",
    "вьетнам": "Нячанг",
    "куба": "Варадеро",
    "доминикана": "Пунта-Кана",
    "шри-ланка": "Коломбо",
    "тунис": "Монастир",
}


def resolve_destination_city(destination: str) -> str:
    """Map a country name to the resort city clients actually mean."""
    key = (destination or "").strip().lower()
    return COUNTRY_TO_RESORT.get(key, (destination or "").strip())


# ---------------------------------------------------------------------------
# Date resolution
#
# parse_russian_dates() handles free text ("15-22 июня") and already returns
# ISO, but most users tap a preset, and presets store fuzzy values ("через
# месяц", "лето"). Those must be turned into a concrete future date or the
# search cannot run at all — which would leave the feature dark for the
# majority of the funnel.

_FUZZY_OFFSETS: List[Tuple[Tuple[str, ...], int]] = [
    (("ближайшие выходные", "выходные"), 0),      # special-cased to next Saturday
    (("в этом месяце",), 7),
    (("через 1-2 недели", "1-2 недели", "недели"), 10),
    (("следующий месяц", "через месяц", "месяц"), 30),
    (("даты гибкие", "гибкие"), 21),
]

_SEASON_MONTH: List[Tuple[Tuple[str, ...], int]] = [
    (("лето",), 7),
    (("зима",), 1),
    (("весна",), 4),
    (("осень",), 10),
]

# A search for tomorrow returns almost nothing useful; keep a sane floor.
MIN_LEAD_DAYS = 3


def _next_saturday(today: date) -> date:
    ahead = (5 - today.weekday()) % 7 or 7
    return today + timedelta(days=ahead)


def _season_date(today: date, month: int) -> date:
    year = today.year
    candidate = date(year, month, 15)
    if candidate <= today + timedelta(days=MIN_LEAD_DAYS):
        candidate = date(year + 1, month, 15)
    return candidate


def resolve_dates(raw: str, today: Optional[date] = None) -> Tuple[Optional[str], Optional[str]]:
    """Turn whatever the dialog stored into (depart, return) ISO dates.

    Free text goes through the existing MDT parser; presets are mapped to a
    concrete future date. Returns (None, None) only when nothing usable can
    be derived, in which case the caller should skip the search.
    """
    today = today or date.today()
    text = (raw or "").strip().lower()
    if not text:
        return None, None

    # 1. Exact free-text range — reuse the parser already trusted by MDT.
    start, end = parse_russian_dates(raw)
    if start:
        try:
            if datetime.strptime(start, "%Y-%m-%d").date() >= today:
                return start, end
        except ValueError:
            pass  # fall through to fuzzy handling

    # 2. Seasons.
    for needles, month in _SEASON_MONTH:
        if any(n in text for n in needles):
            return _season_date(today, month).isoformat(), None

    # 3. Relative presets.
    for needles, offset in _FUZZY_OFFSETS:
        if any(n in text for n in needles):
            if offset == 0:
                return _next_saturday(today).isoformat(), None
            return (today + timedelta(days=offset)).isoformat(), None

    return None, None


def parse_people(raw: Any) -> int:
    """'5+' / '2' / 3 → int, clamped to what a search can express."""
    if isinstance(raw, int):
        return max(1, min(raw, 9))
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return 1
    return max(1, min(int(digits), 9))


# ---------------------------------------------------------------------------
# Tiny TTL cache (in-memory: the Render disk is ephemeral, so nothing durable)

class _TTLCache:
    """Bounded, thread-safe cache. Two clients asking the same route on the
    same day should cost one upstream call, not two."""

    def __init__(self, ttl: int, max_entries: int) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            expires_at, value = hit
            if expires_at < now:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Drop the entry closest to expiry — cheap and good enough.
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache: Optional[_TTLCache] = None
_cache_lock = threading.Lock()


def get_cache(settings: TutuSettings) -> _TTLCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _TTLCache(settings.cache_ttl, settings.cache_max_entries)
        return _cache


# ---------------------------------------------------------------------------
# MCP transport

def mcp_call(
    settings: TutuSettings,
    session: requests.Session,
    tool: str,
    arguments: Dict[str, Any],
    log: Optional[logging.Logger] = None,
    use_cache: bool = True,
) -> Optional[Any]:
    """Call one MCP tool. Returns the decoded tool payload, or None on failure.

    The server is stateless: no initialize handshake and no session header are
    required (verified against tutu-mcp-server 0.26.0).
    """
    log = log or logger
    cache = get_cache(settings)
    cache_key = ""
    if use_cache:
        cache_key = f"{tool}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
        cached = cache.get(cache_key)
        if cached is not None:
            log.info("Tutu cache hit for %s", tool)
            return cached

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    try:
        resp = session.post(
            settings.endpoint,
            json=payload,
            timeout=settings.timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        log.error("Tutu MCP call %s failed: %s", tool, exc)
        return None

    if "error" in body:
        log.error("Tutu MCP returned error for %s: %s", tool, str(body["error"])[:300])
        return None

    result = body.get("result") or {}
    if result.get("isError"):
        log.warning("Tutu tool %s reported isError", tool)
        return None

    content = result.get("content") or []
    if not content:
        log.warning("Tutu tool %s returned empty content", tool)
        return None

    text = content[0].get("text", "")
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = text  # some tools return prose (get_*_instructions)

    if use_cache and cache_key:
        cache.set(cache_key, parsed)
    return parsed


# ---------------------------------------------------------------------------
# Search

@dataclass
class Offer:
    price: float
    currency: str
    carriers: List[str]
    duration_min: int
    departure_at: str
    transport: str
    checkout_url: str = ""
    search_results_url: str = ""


@dataclass
class SearchResult:
    offers: List[Offer] = field(default_factory=list)
    from_city: str = ""
    to_city: str = ""
    search_url: str = ""
    depart_date: str = ""
    # True when nothing matched the client's budget and we widened the search.
    over_budget: bool = False

    @property
    def cheapest(self) -> Optional[Offer]:
        return self.offers[0] if self.offers else None


def _fmt_duration(minutes: Any) -> str:
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return ""
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours} ч {mins} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def _fmt_price(amount: float, currency: str = "RUB") -> str:
    symbol = "₽" if currency in ("RUB", "RUR", "") else currency
    # Render the amount as returned — Tutu's grounding rules forbid rounding.
    whole = int(amount) if float(amount).is_integer() else amount
    return f"{whole:,}".replace(",", " ") + f" {symbol}"


def _fmt_departure(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return ""
    months = ("янв", "фев", "мар", "апр", "мая", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек")
    return f"{dt.day} {months[dt.month - 1]}, {dt.hour:02d}:{dt.minute:02d}"


def _normalise_offers(payload: Dict[str, Any], limit: int) -> List[Offer]:
    """Normalise and de-duplicate offers for display.

    Tutu returns one entry per fare family, so the same physical flight can
    appear several times with an identical price. Three identical lines in a
    row reads as a broken bot, so collapse anything a human would see as the
    same option: price + carriers + departure + duration.
    """
    offers: List[Offer] = []
    seen: set = set()
    for raw in (payload.get("offers") or []):
        if len(offers) >= limit:
            break
        price = (raw.get("price") or {})
        try:
            amount = float(price.get("amount"))
        except (TypeError, ValueError):
            continue
        signature = (
            amount,
            tuple(raw.get("carriers") or []),
            raw.get("departure_at") or "",
            raw.get("duration_min") or 0,
        )
        if signature in seen:
            continue
        seen.add(signature)
        offers.append(Offer(
            price=amount,
            currency=price.get("currency", "RUB"),
            carriers=[c for c in (raw.get("carriers") or []) if c],
            duration_min=raw.get("duration_min") or 0,
            departure_at=raw.get("departure_at") or "",
            transport=raw.get("transport") or "",
            checkout_url=raw.get("checkout_url") or "",
            search_results_url=raw.get("search_results_url") or "",
        ))
    return offers


def search_offers(
    settings: TutuSettings,
    session: requests.Session,
    *,
    destination: str,
    dates_raw: str,
    origin: str = "",
    people: Any = 1,
    kids: Any = 0,
    infants: Any = 0,
    budget: Any = None,
    log: Optional[logging.Logger] = None,
    request_fn: Optional[RequestFn] = None,
) -> Optional[SearchResult]:
    """Search real offers for a completed lead. Returns None when unavailable.

    ``request_fn`` lets tests inject a transport without patching requests.
    """
    log = log or logger
    if not settings.enabled:
        return None

    city = resolve_destination_city(destination)
    if not city:
        return None

    depart, ret = resolve_dates(dates_raw)
    if not depart:
        log.info("Tutu search skipped: could not resolve dates from %r", dates_raw)
        return None

    # Parameter names are fixed by the server's schema — `adults`, not
    # `passengers`. An invented name is rejected with a validation error,
    # so keep this list in sync with SEARCH_ARGS below.
    arguments: Dict[str, Any] = {
        "origin": (origin or settings.default_origin).strip(),
        "destination": city,
        "departure_date": depart,
        # Over-fetch: fare families collapse during de-duplication, so asking
        # for exactly max_offers would often render fewer than intended.
        "page_size": min(max(settings.max_offers, 1) * 4, 30),
        "sort": "price_asc",
    }
    # A tour is a round trip whenever the client gave an end date.
    if ret and ret != depart:
        arguments["return_date"] = ret
    adults = parse_people(people)
    if adults > 1:
        arguments["adults"] = adults
    # Age bands are priced separately by every airline. Sending a family of
    # four as four adults overstates the fare, which is what the funnel did
    # before it asked about children at all.
    children = parse_people(kids) if kids else 0
    babies = parse_people(infants) if infants else 0
    if children:
        arguments["children"] = children
    if babies:
        arguments["infants"] = babies
    # The funnel asks for a budget PER PERSON; Tutu caps the price of the whole
    # offer. Comparing them directly would filter far too aggressively.
    try:
        if budget:
            # Budget is per person; children and infants occupy the same trip,
            # so the cap tracks everyone travelling, not just the adults.
            arguments["price_max"] = int(budget) * max(adults + children + babies, 1)
    except (TypeError, ValueError):
        pass

    caller = request_fn or (lambda tool, args: mcp_call(settings, session, tool, args, log=log))
    payload = caller("search_avia", arguments)

    # A budget cap can legitimately empty the result. Retry unfiltered so the
    # manager still learns what the market costs — but remember that we did,
    # because the client must not be shown offers they never asked for.
    over_budget = False
    if isinstance(payload, dict) and not payload.get("offers") and "price_max" in arguments:
        log.info("Tutu: no offers under budget, retrying without price_max")
        relaxed = {k: v for k, v in arguments.items() if k != "price_max"}
        payload = caller("search_avia", relaxed)
        over_budget = True

    if not isinstance(payload, dict):
        return None

    offers = _normalise_offers(payload, settings.max_offers)
    if not offers:
        return None

    meta = payload.get("meta") or {}
    return SearchResult(
        offers=offers,
        from_city=(meta.get("from") or {}).get("name", arguments["origin"]),
        to_city=(meta.get("to") or {}).get("name", city),
        search_url=offers[0].search_results_url,
        depart_date=depart,
        over_budget=over_budget,
    )


# ---------------------------------------------------------------------------
# Rendering
#
# Two audiences, deliberately different payloads:
#   client  → orientation + a link to browse. No checkout: handing the client
#             a "buy now" button routes the sale around the agency.
#   manager → the same numbers plus checkout links, because closing the deal
#             is their job.

def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _markup(kind: str):
    """Return (escape, bold, link) helpers for the target platform.

    Telegram renders HTML; VK has no markup at all and would show the tags
    verbatim, so the same offer has to be renderable both ways.
    """
    if kind == "plain":
        return (lambda v: str(v or ""),
                lambda t: str(t),
                lambda url, text: f"{text}: {url}")
    return (_esc,
            lambda t: f"<b>{t}</b>",
            lambda url, text: f'<a href="{_esc(url)}">{text}</a>')


def format_client_message(result: SearchResult, *, max_len: int = 3500,
                          markup: str = "html") -> str:
    """Orientation pricing for the client. No checkout links, by design."""
    if not result or not result.offers:
        return ""

    # Nothing fit the stated budget. Dumping a price several times higher than
    # the client asked for reads as tone-deaf; say so plainly instead, and let
    # the manager work the alternatives.
    esc, bold, link = _markup(markup)

    if result.over_budget:
        cheapest = result.offers[0]
        return (
            "✈️ " + bold("По вашему бюджету билетов на эти даты не нашлось") + "\n"
            f"{esc(result.from_city)} → {esc(result.to_city)}: "
            f"рынок начинается от {esc(_fmt_price(cheapest.price, cheapest.currency))} "
            "за перелёт.\n\n"
            "Это нормально: на такие направления выгодные места ловятся "
            "на соседних датах и в чартерах, которых нет в обычном поиске. "
            "Менеджер посмотрит варианты и вернётся с предложением."
        )

    lines = [
        "✈️ " + bold("Ориентир по перелёту"),
        f"{esc(result.from_city)} → {esc(result.to_city)}",
        "",
    ]
    for offer in result.offers:
        bits = [bold(esc(_fmt_price(offer.price, offer.currency)))]
        carriers = ", ".join(offer.carriers)
        if carriers:
            bits.append(esc(carriers))
        when = _fmt_departure(offer.departure_at)
        if when:
            bits.append(esc(when))
        duration = _fmt_duration(offer.duration_min)
        if duration:
            bits.append(esc(duration))
        lines.append("• " + " · ".join(bits))

    lines.append("")
    lines.append(
        "Это цены на билеты по данным Tutu.ru — ориентир, чтобы вы понимали порядок сумм."
    )
    lines.append(
        "Менеджер посчитает пакет с отелем и трансфером — обычно выходит выгоднее, "
        "чем собирать по частям."
    )
    if result.search_url:
        lines.append("\n🔗 " + link(result.search_url, "Посмотреть все варианты"))

    text = "\n".join(lines)
    return text[:max_len]


def format_admin_block(result: SearchResult) -> str:
    """Compact price anchor + checkout links for the manager."""
    if not result or not result.offers:
        return ""

    cheapest = result.offers[0]
    carriers = ", ".join(cheapest.carriers)
    head = (
        f"\n\n📊 <b>Рынок сейчас:</b> от {_esc(_fmt_price(cheapest.price, cheapest.currency))}"
        f" · {_esc(result.from_city)} → {_esc(result.to_city)}"
        f" · {_esc(result.depart_date)}"
    )
    if carriers:
        head += f" · {_esc(carriers)}"
    if result.over_budget:
        head += "\n⚠️ <b>Бюджет клиента не покрывает перелёт</b> — ищите чартер/соседние даты."

    links: List[str] = []
    if cheapest.checkout_url:
        links.append(f'<a href="{_esc(cheapest.checkout_url)}">Оформить</a>')
    if result.search_url:
        links.append(f'<a href="{_esc(result.search_url)}">Все варианты</a>')
    if links:
        head += "\n🔗 " + " · ".join(links)
    return head
