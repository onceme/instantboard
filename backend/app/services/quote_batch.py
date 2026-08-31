"""Batch component-quote fetching for the fund-intraday estimator (§6.3 / §7.2-7).

Given the de-duplicated union of constituent stocks across all followed funds
(grouped by market), fetch the realtime change for each one through the
per-market upstream chain returned by the budget governor and merge the results.
Codes the preferred source does not return are back-filled incrementally from
the next source in the chain — a per-code fill, never a whole-batch retry.

Every HTTP request is gated by UpstreamBudgetGovernor.acquire() and its outcome
is folded back via report_result(); a code no source can supply is reported in
the returned missing set so the estimator can degrade it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.services.upstream_budget import (
    get_budget_governor,
    get_spec,
    to_em_secid,
    to_sina_symbol,
    to_tencent_symbol,
)

logger = logging.getLogger(__name__)

# Upstream fetch timeout (seconds). Batches can carry up to 500 codes, so a
# generous ceiling avoids false failures while still bounding a hung request.
QUOTE_FETCH_TIMEOUT_SECONDS = 10
# Concurrent in-flight HTTP batches per upstream fetch round (§7.3: concurrency
# capped at 4).
MAX_CONCURRENT_BATCHES = 4

# Sina A-share CSV field positions (§6.1: self-compute the change):
#   0 name | 1 open | 2 prev_close | 3 price
SINA_CN_FIELD_PREV_CLOSE = 2
SINA_CN_FIELD_PRICE = 3

# Tencent qt fields separated by "~" (§6.1 positions, verified 2026-08-31):
#   1 name | 2 code | 3 price | 4 prev_close | ... | 31 change | 32 change%
TENCENT_FIELD_NAME = 1
TENCENT_FIELD_PRICE = 3
TENCENT_FIELD_PREV_CLOSE = 4
TENCENT_FIELD_CHANGE_PERCENT = 32

UA_HEADER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 instantboard-quote/1.0"
)


@dataclass
class Constituent:
    """One de-duplicated constituent stock to quote."""

    key: str  # canonical identity: secid if known else "{market}:{stock_code}"
    stock_code: str
    market: str  # CN / HK / US
    secid: str | None = None


@dataclass
class QuoteTick:
    """Normalized realtime quote for one constituent."""

    price: float | None
    change_percent: float | None
    # realtime for A-shares; tencent/sina HK+US feeds are delayed (~15-25min,
    # §6.3) so those legs carry the delayed flag the UI surfaces.
    quote_status: str
    upstream: str
    name: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def _constituent_key(constituent: Constituent) -> str:
    if constituent.secid:
        return constituent.secid
    return f"{constituent.market.upper()}:{constituent.stock_code}"


def _quote_status_for_market(market: str) -> str:
    return "realtime" if market.upper() == "CN" else "delayed"


async def _governed_get(governor, upstream: str, url: str, headers: dict) -> httpx.Response | None:
    """One budget-gated HTTP GET. Returns the 200 response or None; the outcome
    is always reported back to the governor and the slot released."""
    if not await governor.acquire(upstream):
        logger.debug(f"quote_batch: {upstream} budget exhausted, batch deferred")
        return None
    try:
        async with httpx.AsyncClient(timeout=QUOTE_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
        ok = response.status_code == 200
        await governor.report_result(upstream, ok, response.status_code)
        return response if ok else None
    except httpx.HTTPError as exc:
        await governor.report_result(upstream, ok=False, status_code=None)
        logger.warning(f"quote_batch: {upstream} request failed: {exc}")
        return None
    finally:
        governor.release(upstream)


def _chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] if size > 0 else [items]


def _build_headers(spec_headers: dict) -> dict:
    headers = {"User-Agent": UA_HEADER}
    headers.update(spec_headers or {})
    return headers


# --- Tencent (qt.gtimg.cn) ---


async def _fetch_tencent(
    constituents: list[Constituent], governor
) -> dict[str, tuple[float | None, float | None, str | None]]:
    """Returns {canonical_key: (price, change_percent, name)} for resolved codes."""
    spec = get_spec("tencent_qt")
    sym_to_key: dict[str, str] = {}
    ordered: list[tuple[str, Constituent]] = []
    for c in constituents:
        sym = to_tencent_symbol(c.stock_code, c.market)
        if not sym or sym in sym_to_key:
            continue
        sym_to_key[sym] = _constituent_key(c)
        ordered.append((sym, c))
    if not ordered:
        return {}

    results: dict[str, tuple[float | None, float | None, str | None]] = {}

    async def fetch_batch(batch: list[tuple[str, Constituent]]) -> None:
        syms = [sym for sym, _ in batch]
        url = spec.endpoint.format(syms=",".join(syms))
        response = await _governed_get(governor, "tencent_qt", url, _build_headers(spec.required_headers))
        if response is None:
            return
        try:
            text = response.content.decode("gbk", errors="ignore")
        except (LookupError, UnicodeDecodeError):
            text = response.text
        # Each line: v_<sym>="f0~f1~...~fn";
        for line in text.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            _, _, payload = line.partition("=")
            payload = payload.strip().strip('"')
            if not payload:
                continue
            fields = payload.split("~")
            sym_part = line.split("=", 1)[0].strip()
            sym = sym_part[2:] if sym_part.startswith("v_") else sym_part
            key = sym_to_key.get(sym)
            if key is None:
                continue
            price = _to_float(fields[TENCENT_FIELD_PRICE]) if len(fields) > TENCENT_FIELD_PRICE else None
            change_percent = None
            if len(fields) > TENCENT_FIELD_CHANGE_PERCENT:
                change_percent = _to_float(fields[TENCENT_FIELD_CHANGE_PERCENT])
            if change_percent is None and len(fields) > TENCENT_FIELD_PREV_CLOSE:
                prev_close = _to_float(fields[TENCENT_FIELD_PREV_CLOSE])
                if price is not None and prev_close:
                    change_percent = (price - prev_close) / prev_close * 100
            name = fields[TENCENT_FIELD_NAME] if len(fields) > TENCENT_FIELD_NAME else None
            if price is None and change_percent is None:
                continue
            results[key] = (price, change_percent, name)

    await _run_batches(
        [ordered[i : i + spec.batch_limit] for i in range(0, len(ordered), spec.batch_limit)], fetch_batch
    )
    return results


# --- Sina (hq.sinajs.cn) ---


async def _fetch_sina(
    constituents: list[Constituent], governor
) -> dict[str, tuple[float | None, float | None, str | None]]:
    spec = get_spec("sina_hq")
    sym_to_key: dict[str, str] = {}
    ordered: list[tuple[str, Constituent]] = []
    for c in constituents:
        sym = to_sina_symbol(c.stock_code, c.market)
        if not sym or sym in sym_to_key:
            continue
        sym_to_key[sym] = _constituent_key(c)
        ordered.append((sym, c))
    if not ordered:
        return {}

    results: dict[str, tuple[float | None, float | None, str | None]] = {}

    async def fetch_batch(batch: list[tuple[str, Constituent]]) -> None:
        syms = [sym for sym, _ in batch]
        url = spec.endpoint.format(syms=",".join(syms))
        response = await _governed_get(governor, "sina_hq", url, _build_headers(spec.required_headers))
        if response is None:
            return
        try:
            text = response.content.decode("gbk", errors="ignore")
        except (LookupError, UnicodeDecodeError):
            text = response.text
        # Each line: var hq_str_<sym>="name,open,prev_close,price,...";
        for line in text.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            head, _, payload = line.partition("=")
            payload = payload.strip().strip('"')
            if not payload:
                continue
            sym_part = head.strip()
            sym = sym_part.replace("var hq_str_", "")
            key = sym_to_key.get(sym)
            if key is None:
                continue
            fields = payload.split(",")
            price = _to_float(fields[SINA_CN_FIELD_PRICE]) if len(fields) > SINA_CN_FIELD_PRICE else None
            prev_close = _to_float(fields[SINA_CN_FIELD_PREV_CLOSE]) if len(fields) > SINA_CN_FIELD_PREV_CLOSE else None
            change_percent = None
            if price is not None and prev_close:
                change_percent = (price - prev_close) / prev_close * 100
            name = fields[0] if fields else None
            if price is None and change_percent is None:
                continue
            results[key] = (price, change_percent, name)

    await _run_batches(
        [ordered[i : i + spec.batch_limit] for i in range(0, len(ordered), spec.batch_limit)], fetch_batch
    )
    return results


# --- EastMoney push2 (main / m1 mirror / delay) ---


async def _fetch_eastmoney(
    upstream: str, constituents: list[Constituent], governor
) -> dict[str, tuple[float | None, float | None, str | None]]:
    spec = get_spec(upstream)
    secid_to_key: dict[str, str] = {}
    ordered: list[tuple[str, Constituent]] = []
    for c in constituents:
        secid = c.secid or to_em_secid(c.stock_code, c.market)
        if not secid or secid in secid_to_key:
            continue
        secid_to_key[secid] = _constituent_key(c)
        ordered.append((secid, c))
    if not ordered:
        return {}

    results: dict[str, tuple[float | None, float | None, str | None]] = {}

    async def fetch_batch(batch: list[tuple[str, Constituent]]) -> None:
        secids = [secid for secid, _ in batch]
        url = spec.endpoint.format(syms=",".join(secids)) + "&fields=f2,f3,f12,f14&fltt=2&invt=2"
        response = await _governed_get(governor, upstream, url, _build_headers(spec.required_headers))
        if response is None:
            return
        try:
            payload = response.json()
        except (ValueError, TypeError):
            await governor.report_result(upstream, ok=False, status_code=response.status_code)
            return
        diff = (payload or {}).get("data", {}).get("diff")
        if not diff:
            return
        # diff is either a list of rows or a {"0": row, "1": row, ...} map.
        rows = list(diff.values()) if isinstance(diff, dict) else diff
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("f12") or "")
            # Match back by secid suffix (f12 is the bare code; rebuild secid
            # candidates is overkill — look the key up by any secid ending in the
            # code) .
            matched_key = None
            for secid, key in secid_to_key.items():
                if secid.split(".", 1)[-1] == code:
                    matched_key = key
                    break
            if matched_key is None:
                continue
            price = _to_float(row.get("f2"))
            change_percent = _to_float(row.get("f3"))
            name = row.get("f14")
            if price is None and change_percent is None:
                continue
            results[matched_key] = (price, change_percent, name)

    await _run_batches(
        [ordered[i : i + spec.batch_limit] for i in range(0, len(ordered), spec.batch_limit)], fetch_batch
    )
    return results


async def _run_batches(batches: list[list], fetch_batch) -> None:
    """Run batch fetch coroutines with a concurrency cap of MAX_CONCURRENT_BATCHES."""
    if not batches:
        return
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async def guarded(batch: list) -> None:
        async with semaphore:
            await fetch_batch(batch)

    await asyncio.gather(*[guarded(batch) for batch in batches], return_exceptions=True)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN guard for "-"-style placeholders cast by upstream
        return None
    return result


# Upstream name → fetcher. EastMoney mirrors share one fetcher keyed by name.
async def _dispatch_upstream(upstream: str, constituents: list[Constituent], governor):
    if upstream == "tencent_qt":
        return await _fetch_tencent(constituents, governor)
    if upstream == "sina_hq":
        return await _fetch_sina(constituents, governor)
    if upstream.startswith("em_push2"):
        return await _fetch_eastmoney(upstream, constituents, governor)
    logger.warning(f"quote_batch: unhandled upstream {upstream}")
    return {}


async def fetch_quotes(constituents: list[Constituent], governor=None) -> tuple[dict[str, QuoteTick], list[str]]:
    """Fetch quotes for all constituents along the per-market chains.

    Returns (quotes, missing): quotes maps the canonical constituent key to a
    QuoteTick; missing lists the keys no upstream could supply (the estimator
    then degrades those funds). Each market group walks its governor chain in
    order, back-filling only the not-yet-resolved codes from later sources.
    """
    governor = governor or get_budget_governor()
    quotes: dict[str, QuoteTick] = {}

    by_market: dict[str, list[Constituent]] = {}
    for c in constituents:
        by_market.setdefault((c.market or "").upper(), []).append(c)

    for market, group in by_market.items():
        if not market:
            continue
        chain = await governor.pick_chain(market)
        if not chain:
            logger.warning(f"quote_batch: no upstream chain for market {market}")
            continue
        pending = list(group)
        status = _quote_status_for_market(market)
        for upstream in chain:
            if not pending:
                break
            try:
                got = await _dispatch_upstream(upstream, pending, governor)
            except Exception as exc:  # noqa: BLE001 - one upstream's crash must not kill the group
                logger.warning(f"quote_batch: {upstream} failed for market {market}: {exc}")
                got = {}
            for key, (price, change_percent, name) in got.items():
                quotes[key] = QuoteTick(
                    price=price,
                    change_percent=change_percent,
                    quote_status=status,
                    upstream=upstream,
                    name=name,
                )
            pending = [c for c in pending if _constituent_key(c) not in quotes]

    missing = [_constituent_key(c) for c in constituents if _constituent_key(c) not in quotes]
    return quotes, missing
