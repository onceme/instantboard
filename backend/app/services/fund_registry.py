"""Fund registry cache (fund-intraday-nav.md §9.3).

EastMoney's public ``fundcode_search.js`` is the full catalog of registered CN
funds (~28k entries: code, Chinese name, EastMoney type, full/abbreviated
pinyin). Caching it lets symbol search + auto-registration cover ANY registered
fund code — including the 01/02 new series and the OTC 11x families the
code-prefix heuristic deliberately skips — and adds name/pinyin search for OTC
funds that no external index (Yahoo) knows about.

Layers (all degradation-safe; a registry failure never blocks search — callers
fall back to the existing heuristic path):

- ``parse_registry_payload`` — pure parser with size/shape guards.
- Redis cache: ``fund_registry:ptr`` → ``{version: sha1-digest, ...}`` plus a
  versioned data key ``fund_registry:{version}`` holding the compact
  ``{code: [name, type, pinyin_full, pinyin_abbr]}`` JSON (24h TTL).
- In-process snapshot (~10min) so per-search requests never re-parse ~3MB JSON.
- Upstream fetch governed by the ``em_fund_registry`` budget spec plus a 300s
  negative cache so search keystrokes cannot re-hammer a failing upstream.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.redis import RedisKeys, get_redis_client
from app.services.upstream_budget import UPSTREAM_REGISTRY, get_budget_governor

logger = logging.getLogger(__name__)

UPSTREAM_NAME = "em_fund_registry"
FUND_REGISTRY_URL = UPSTREAM_REGISTRY[UPSTREAM_NAME].endpoint

# Payload guards: the measured catalog is ~3.15MB / ~27.7k rows (2026-09-01).
# The caps leave growth headroom while rejecting a runaway or replaced payload.
MAX_REGISTRY_BYTES = 8 * 1024 * 1024
MAX_REGISTRY_ENTRIES = 60_000
# A real catalog is megabytes; anything below this is an error page, not data.
MIN_PAYLOAD_BYTES = 10_000

# Candidate cap for text/pinyin search: keeps the result list displayable.
MAX_SEARCH_RESULTS = 20

# In-process snapshot lifetime; Redis-backed snapshots re-check the shared cache
# after this long, unpersisted ones (Redis store failed) are served for the full
# registry TTL so a Redis outage cannot cascade into upstream re-fetches.
REGISTRY_PROCESS_CACHE_TTL = 600
FETCH_TIMEOUT_SECONDS = 20.0
UPSTREAM_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class FundRegistryEntry:
    code: str
    name: str
    fund_type: str
    pinyin_full: str
    pinyin_abbr: str


def fund_placeholder_name(code: str) -> str:
    """Legacy auto-registration placeholder, kept for detection/healing."""
    return f"基金 {code}"


def is_placeholder_name(name: str | None, code: str) -> bool:
    return (name or "") == fund_placeholder_name(code)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" for ch in text)


def _cjk_weak_match(query: str, name: str) -> bool:
    """Tolerant CJK match for abbreviated catalog names.

    EastMoney's catalog shortens official titles (019118 is officially
    「景顺长城纳斯达克科技市值加权ETF联接(QDII)」but the catalog row reads
    「景顺长城纳斯达克科技ETF联接(QDII)E人民币」— 「市值加权」appears in neither).
    Users type the official name, so match greedily in slices: walk the query,
    consuming the longest slice present in the name, skipping foreign tokens
    (the 「市值加权」 part above), and accept when ≥60% of the query matched.
    The leading-4-chars gate keeps the per-search cost at one cheap substring
    check for the vast majority of the ~28k rows.
    """
    n = len(query)
    if n < 8 or query[:4] not in name:
        return False
    matched = 0
    i = 0
    while i < n:
        consumed = 0
        for length in range(min(n - i, 12), 1, -1):
            if query[i : i + length] in name:
                consumed = length
                break
        if consumed == 0:
            i += 1
        else:
            matched += consumed
            i += consumed
    return matched * 5 >= n * 3


class FundRegistry:
    """Immutable parsed snapshot with query methods."""

    __slots__ = ("_by_code", "version", "count", "fetched_at", "persisted")

    def __init__(self, entries: dict[str, FundRegistryEntry], version: str, fetched_at: str):
        self._by_code = entries
        self.version = version
        self.count = len(entries)
        self.fetched_at = fetched_at
        # False when the Redis store failed: the process cache then serves this
        # snapshot for the full registry TTL (see module docstring).
        self.persisted = True

    def lookup_code(self, code: str) -> FundRegistryEntry | None:
        return self._by_code.get((code or "").strip())

    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> list[FundRegistryEntry]:
        """Name/pinyin/code candidates in catalog order (code ascending).

        CJK input matches the Chinese name by substring (strong hits first, then
        prefix-tolerant weak hits for abbreviated catalog names); ASCII input
        matches the full-pinyin prefix, the abbreviated-pinyin prefix, an ASCII
        name substring, or the code prefix (partial code entry).
        """
        q = (query or "").strip()
        if not q or limit <= 0:
            return []
        hits: list[FundRegistryEntry] = []
        if _contains_cjk(q):
            weak: list[FundRegistryEntry] = []
            for entry in self._by_code.values():
                if q in entry.name:
                    hits.append(entry)
                    if len(hits) >= limit:
                        break
                elif len(weak) < limit and _cjk_weak_match(q, entry.name):
                    weak.append(entry)
            hits.extend(weak)
            hits = hits[:limit]
        else:
            qu = q.upper()
            for entry in self._by_code.values():
                if (
                    entry.pinyin_full.startswith(qu)
                    or entry.pinyin_abbr.startswith(qu)
                    or qu in entry.name.upper()
                    or entry.code.startswith(qu)
                ):
                    hits.append(entry)
                    if len(hits) >= limit:
                        break
        return hits


def parse_registry_payload(raw: bytes) -> dict[str, FundRegistryEntry]:
    """Parse the fundcode_search.js body into a code → entry map.

    Raises ValueError on any size/shape violation; individual malformed rows are
    skipped (the catalog is upstream data, tolerance beats rejection). Row shape:
    ``[code, pinyin_abbr, name, fund_type, pinyin_full]`` — the type and pinyin
    fields are empty for a handful of entries and stay empty.
    """
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError(f"registry payload too large: {len(raw)} bytes (cap {MAX_REGISTRY_BYTES})")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"registry payload not UTF-8: {exc}") from exc
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("registry payload: no array body found")
    try:
        rows = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"registry payload: invalid JSON body: {exc}") from exc
    if not isinstance(rows, list) or not rows:
        raise ValueError("registry payload: empty or non-array body")
    if len(rows) > MAX_REGISTRY_ENTRIES:
        raise ValueError(f"registry payload: {len(rows)} rows exceed cap {MAX_REGISTRY_ENTRIES}")

    entries: dict[str, FundRegistryEntry] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        code = str(row[0]).strip()
        name = str(row[2]).strip()
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        fund_type = str(row[3]).strip() if len(row) > 3 else ""
        pinyin_full = str(row[4]).strip().upper() if len(row) > 4 else ""
        pinyin_abbr = str(row[1]).strip().upper() if row[1] else ""
        entries.setdefault(code, FundRegistryEntry(code, name, fund_type, pinyin_full, pinyin_abbr))
    if not entries:
        raise ValueError("registry payload: zero valid rows")
    return entries


# --- process-level snapshot cache ---

_process_cache: FundRegistry | None = None
_process_cache_at: float = 0.0
_upstream_lock = asyncio.Lock()


def _cache_fresh(registry: FundRegistry, now: float) -> bool:
    ttl = REGISTRY_PROCESS_CACHE_TTL if registry.persisted else RedisKeys.FUND_REGISTRY_TTL
    return now - _process_cache_at < ttl


def _set_process_cache(registry: FundRegistry) -> None:
    global _process_cache, _process_cache_at
    _process_cache = registry
    _process_cache_at = time.monotonic()


def _reset_process_cache() -> None:  # test seam
    global _process_cache, _process_cache_at
    _process_cache = None
    _process_cache_at = 0.0


# --- Redis layer ---


def _serialize_data(entries: dict[str, FundRegistryEntry], version: str, fetched_at: str) -> str:
    return json.dumps(
        {
            "version": version,
            "fetched_at": fetched_at,
            "entries": {code: [e.name, e.fund_type, e.pinyin_full, e.pinyin_abbr] for code, e in entries.items()},
        },
        ensure_ascii=False,
    )


def _deserialize_data(payload: str) -> FundRegistry | None:
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return None
    version = str(data.get("version", ""))
    entries: dict[str, FundRegistryEntry] = {}
    for code, row in data["entries"].items():
        if not isinstance(row, list) or len(row) < 4 or len(code) != 6 or not code.isdigit():
            continue
        entries[code] = FundRegistryEntry(code, str(row[0]), str(row[1]), str(row[2]), str(row[3]))
    if not entries:
        return None
    return FundRegistry(entries, version, str(data.get("fetched_at", "")))


async def _load_from_redis() -> FundRegistry | None:
    try:
        client = await get_redis_client()
        raw_ptr = await client.get(RedisKeys.fund_registry_ptr_key())
        if not raw_ptr:
            return None
        version = str(json.loads(raw_ptr).get("version", ""))
        if not version:
            return None
        raw_data = await client.get(RedisKeys.fund_registry_data_key(version))
        if not raw_data:
            return None
        return _deserialize_data(raw_data)
    except Exception as exc:
        logger.warning(f"fund_registry: Redis load failed (will rebuild from upstream): {exc}")
        return None


async def _store_to_redis(version: str, data_payload: str, ptr_payload: str) -> bool:
    try:
        client = await get_redis_client()
        data_ttl = RedisKeys.FUND_REGISTRY_TTL + RedisKeys.FUND_REGISTRY_DATA_EXTRA_TTL
        await client.set(RedisKeys.fund_registry_data_key(version), data_payload, ex=data_ttl)
        await client.set(RedisKeys.fund_registry_ptr_key(), ptr_payload, ex=RedisKeys.FUND_REGISTRY_TTL)
        return True
    except Exception as exc:
        logger.warning(f"fund_registry: Redis store failed (serving in-process only): {exc}")
        return False


async def _mark_failure() -> None:
    try:
        client = await get_redis_client()
        await client.set(RedisKeys.fund_registry_fail_key(), "1", ex=RedisKeys.FUND_REGISTRY_FAIL_TTL)
    except Exception:
        pass  # Redis down: upstream still answered the fetch attempt; move on


async def _failure_marked() -> bool:
    try:
        client = await get_redis_client()
        return bool(await client.get(RedisKeys.fund_registry_fail_key()))
    except Exception:
        return False


# --- upstream layer ---


async def _fetch_payload() -> bytes | None:
    governor = get_budget_governor()
    spec = UPSTREAM_REGISTRY[UPSTREAM_NAME]
    if not await governor.acquire(UPSTREAM_NAME):
        logger.info("fund_registry: upstream budget/breaker refused the fetch")
        return None
    try:
        headers = {"User-Agent": UPSTREAM_USER_AGENT, **spec.required_headers}
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(FUND_REGISTRY_URL, headers=headers)
        if response.status_code != 200 or len(response.content) < MIN_PAYLOAD_BYTES:
            await governor.report_result(UPSTREAM_NAME, False, response.status_code)
            logger.warning(
                f"fund_registry: upstream rejected (HTTP {response.status_code}, {len(response.content)} bytes)"
            )
            return None
        await governor.report_result(UPSTREAM_NAME, True, response.status_code)
        return response.content
    except Exception as exc:
        await governor.report_result(UPSTREAM_NAME, False, None)
        logger.warning(f"fund_registry: upstream fetch failed: {exc}")
        return None
    finally:
        governor.release(UPSTREAM_NAME)


async def _load_from_upstream() -> FundRegistry | None:
    async with _upstream_lock:
        # Another waiter may have filled Redis while this coroutine queued.
        fresh = await _load_from_redis()
        if fresh is not None:
            return fresh
        if await _failure_marked():
            return None
        raw = await _fetch_payload()
        if raw is None:
            await _mark_failure()
            return None
        try:
            entries = parse_registry_payload(raw)
        except ValueError as exc:
            logger.error(f"fund_registry: payload rejected: {exc}")
            await _mark_failure()
            return None
        version = hashlib.sha1(raw).hexdigest()[:16]
        fetched_at = datetime.now(UTC).isoformat()
        registry = FundRegistry(entries, version, fetched_at)
        persisted = await _store_to_redis(
            version,
            _serialize_data(entries, version, fetched_at),
            json.dumps({"version": version, "count": registry.count}),
        )
        registry.persisted = persisted
        logger.info(f"fund_registry: loaded {registry.count} funds (version {version}, persisted={persisted})")
        return registry


# --- public entry points ---


async def get_registry() -> FundRegistry | None:
    """Lazy load: process cache → Redis → upstream. Never raises; None means
    "unavailable" and callers degrade to the heuristic search path."""
    if _process_cache is not None and _cache_fresh(_process_cache, time.monotonic()):
        return _process_cache
    registry = await _load_from_redis()
    if registry is None:
        registry = await _load_from_upstream()
    if registry is not None:
        _set_process_cache(registry)
    return registry


async def refresh_registry(force: bool = False) -> bool:
    """Proactive refresh (nightly holdings cron); force=False is a no-op when a
    cached registry exists. Returns True when a registry is available after."""
    cached = _process_cache if _process_cache is not None and _cache_fresh(_process_cache, time.monotonic()) else None
    if cached is None:
        cached = await _load_from_redis()
    if cached is not None and not force:
        _set_process_cache(cached)
        return True
    registry = await _load_from_upstream()
    if registry is not None:
        _set_process_cache(registry)
    return registry is not None
