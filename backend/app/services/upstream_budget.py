"""Upstream budget governance for fund-intraday quote/holdings fetches.

Implements fund-intraday-nav.md §6: every quote/holdings upstream has a
registered request budget (max_rpm / burst / required headers) and the
governor enforces it BEFORE each request — budget check, pre-emptive chain
switching when the preferred source nears its limit, and a circuit breaker
with exponential cooldown after repeated denials. State (budget counters +
breakers) lives in Redis so the worker tasks and api on-demand paths share ONE
source of truth across processes.

Failure tolerance follows §12: Redis unavailability fails OPEN (a budget read
failure lets the request through, logged) — an occasional extra request is
acceptable, a Redis outage must not take the whole feature down; the breaker
remains the real protection once Redis recovers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.config import settings
from app.core.redis import RedisKeys, get_redis_client

logger = logging.getLogger(__name__)

# --- Circuit-breaker policy (§6.2) ---
# Two consecutive denials start the cooldown ladder: 60s × 2^(n-2), capped.
BREAKER_DENIAL_THRESHOLD = 2
BREAKER_BASE_COOLDOWN_SECONDS = 60
BREAKER_MAX_COOLDOWN_SECONDS = 1800
# Status codes that count as an upstream denial (escalate the breaker).
# 4xx like 404 on a single fund code are per-item failures, NOT upstream
# denials, and must not trip the breaker (fund-intraday-nav.md §5.4).
DENIAL_STATUS_CODES = frozenset({403, 429})

# Pre-emptive switch threshold (§6.2 pick_chain): when the preferred source
# has less than this fraction of its minute budget left, move to the backup
# with the most budget remaining BEFORE getting denied.
PRE_SWITCH_REMAINING_FRACTION = 0.2

# Market → failover group (§6.3).
QUOTE_GROUP_BY_MARKET = {"CN": "cn_quote", "HK": "hk_quote", "US": "us_quote"}
QUOTE_CHAINS: dict[str, tuple[str, ...]] = {
    "cn_quote": ("tencent_qt", "sina_hq"),
    "hk_quote": ("tencent_qt", "sina_hq", "em_push2_delay"),
    "us_quote": ("tencent_qt", "sina_hq"),
    "fund_holdings": ("em_f10_holdings",),
}


@dataclass(frozen=True)
class UpstreamSpec:
    name: str
    endpoint: str
    batch_limit: int
    max_rpm: int
    burst: int
    # Headers the request must carry (the endpoint 404/403s without them).
    required_headers: dict[str, str] = field(default_factory=dict)
    # Documentation-only fields mirroring the §6.1 registry table.
    timeliness: str = ""
    known_limit: str = ""


# §6.1 registry (document mirror; env overrides resolve through get_spec()).
# All limits come from the 2026-08-31 measurement round.
UPSTREAM_REGISTRY: dict[str, UpstreamSpec] = {
    "tencent_qt": UpstreamSpec(
        name="tencent_qt",
        endpoint="https://qt.gtimg.cn/q={syms}",
        batch_limit=500,
        max_rpm=120,
        burst=6,
        timeliness="A-share realtime; HK delayed ~15min; US delayed ~15min",
        known_limit="200 codes x 10 rounds @3s, no throttling observed",
    ),
    "sina_hq": UpstreamSpec(
        name="sina_hq",
        endpoint="https://hq.sinajs.cn/list={syms}",
        batch_limit=400,
        max_rpm=60,
        burst=4,
        required_headers={"Referer": "https://finance.sina.com.cn"},
        timeliness="A-share realtime; HK delayed 25min+",
        known_limit="no failures observed",
    ),
    "em_push2": UpstreamSpec(
        name="em_push2",
        endpoint="https://push2.eastmoney.com/api/qt/ulist.np/get?secids={syms}",
        batch_limit=200,
        max_rpm=6,
        burst=1,
        timeliness="A-share <=3s; the only near-realtime HK source",
        known_limit="main domain bans clients after ~8 requests; <=10 req/min advised",
    ),
    "em_push2_m1": UpstreamSpec(
        name="em_push2_m1",
        endpoint="https://1.push2.eastmoney.com/api/qt/ulist.np/get?secids={syms}",
        batch_limit=200,
        max_rpm=15,
        burst=2,
        timeliness="same as main domain",
        known_limit="mirror usable",
    ),
    "em_push2_delay": UpstreamSpec(
        name="em_push2_delay",
        endpoint="https://push2delay.eastmoney.com/api/qt/ulist.np/get?secids={syms}",
        batch_limit=200,
        max_rpm=40,
        burst=4,
        timeliness="delayed quotes",
        known_limit="200 codes x 10 rounds @3s, no throttling observed",
    ),
    "em_f10_holdings": UpstreamSpec(
        name="em_f10_holdings",
        endpoint="https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=10",
        batch_limit=1,
        max_rpm=8,
        burst=1,
        required_headers={"Referer": "https://fundf10.eastmoney.com/"},
        timeliness="disclosure data",
        known_limit="<=10 req/min advised",
    ),
}

# Env overrides for registry budgets (§10): settings field name per upstream.
_RPM_SETTING_BY_NAME = {
    "tencent_qt": "quote_tencent_max_rpm",
    "sina_hq": "quote_sina_max_rpm",
    "em_push2_delay": "quote_em_delay_max_rpm",
    "em_f10_holdings": "quote_em_f10_max_rpm",
}


def get_spec(name: str) -> UpstreamSpec:
    """Registry entry with env budget overrides applied (§10)."""
    spec = UPSTREAM_REGISTRY[name]
    setting_name = _RPM_SETTING_BY_NAME.get(name)
    if setting_name is None:
        return spec
    override = getattr(settings, setting_name, spec.max_rpm)
    if override == spec.max_rpm:
        return spec
    return replace(spec, max_rpm=override)


def effective_max_rpm(name: str) -> int:
    """Budget actually enforced: registry/env rpm × global safety factor."""
    return int(get_spec(name).max_rpm * settings.quote_upstream_safety_factor)


def quote_chain(market: str) -> list[str]:
    """Failover chain for one constituent market (§6.3).

    HK adds the em_push2 mirrors behind the delayed sources; the MAIN domain
    only joins when QUOTE_EASTMONEY_MAIN_ENABLED=true (default off — the IP
    red line, §6.3).
    """
    group = QUOTE_GROUP_BY_MARKET.get((market or "").upper(), "")
    names = list(QUOTE_CHAINS.get(group, ()))
    if (market or "").upper() == "HK":
        names.append("em_push2_m1")
        if settings.quote_eastmoney_main_enabled:
            names.append("em_push2")
    return names


def is_denial(ok: bool, status_code: int | None) -> bool:
    """True when a result counts as an upstream denial (breaker escalation).

    Denials are 403/429, any 5xx, and connection-level failures (status None).
    Per-item misses like 404 are NOT denials (§5.4: log and move on).
    """
    if ok:
        return False
    if status_code is None:
        return True
    if status_code in DENIAL_STATUS_CODES:
        return True
    return status_code >= 500


def _minute_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M")


class UpstreamBudgetGovernor:
    """Redis-backed budget + circuit-breaker state for the upstream registry.

    State is shared across processes (worker jobs + api on-demand paths
    consume the same upstreams). Per-process in-flight counting is local; it
    only needs to be exact inside one process because the burst limit guards
    concurrency, not global volume.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, int] = {}

    # --- budget / breaker primitives ---

    async def _breaker_state(self, name: str) -> dict | None:
        try:
            client = await get_redis_client()
            raw = await client.get(RedisKeys.quote_breaker_key(name))
        except Exception as exc:
            logger.warning(f"upstream_budget: breaker read failed for {name} (fail-open): {exc}")
            return None
        if not raw:
            return None
        try:
            state = json.loads(raw)
            if isinstance(state, dict) and isinstance(state.get("cooldown_until"), (int, float)):
                return state
        except (TypeError, ValueError):
            logger.debug(f"upstream_budget: corrupt breaker entry for {name}, ignoring")
        return None

    def _breaker_active(self, state: dict | None) -> bool:
        if not state:
            return False
        return datetime.now(UTC).timestamp() < float(state.get("cooldown_until", 0))

    async def acquire(self, name: str) -> bool:
        """Admit one request or refuse it (§6.2 request flow).

        Order: breaker → minute budget → in-flight burst. Refusal reasons are
        logged at debug; Redis outage degrades to fail-open admission.
        """
        spec = get_spec(name)

        if self._breaker_active(await self._breaker_state(name)):
            logger.debug(f"upstream_budget: {name} refused (breaker cooldown)")
            return False

        budget_key = RedisKeys.quote_budget_key(name, _minute_stamp())
        try:
            client = await get_redis_client()
            count = await client.incr(budget_key)
            await client.expire(budget_key, RedisKeys.QUOTE_BUDGET_TTL)
            if count > effective_max_rpm(name):
                await client.decr(budget_key)
                logger.debug(f"upstream_budget: {name} refused (minute budget exhausted, count={count})")
                return False
        except Exception as exc:
            # Fail-open (§12): a Redis blip must not switch the feature off;
            # the breaker is the real protection once Redis recovers.
            logger.warning(f"upstream_budget: budget check failed for {name} (fail-open): {exc}")

        if self._in_flight.get(name, 0) >= spec.burst:
            logger.debug(f"upstream_budget: {name} refused (in-flight burst {spec.burst})")
            return False

        self._in_flight[name] = self._in_flight.get(name, 0) + 1
        return True

    def release(self, name: str) -> None:
        """Return the in-flight slot taken by acquire(); never goes negative."""
        self._in_flight[name] = max(0, self._in_flight.get(name, 0) - 1)

    async def slot(self, name: str) -> bool:
        """Async context helper: `if await gov.slot(name): try ... finally release`.

        Kept separate from acquire() so callers needing custom control flow
        (batch loops) can also use the primitives directly.
        """
        return await self.acquire(name)

    # --- budget-left introspection (pick_chain pre-switching) ---

    async def _budget_left(self, name: str) -> int:
        """Requests still admissible this minute; large on Redis errors
        (fail-open: the source stays eligible instead of being demoted)."""
        try:
            client = await get_redis_client()
            raw = await client.get(RedisKeys.quote_budget_key(name, _minute_stamp()))
            used = int(raw) if raw else 0
        except Exception as exc:
            logger.debug(f"upstream_budget: budget read failed for {name} (treating as open): {exc}")
            return effective_max_rpm(name)
        if self._breaker_active(await self._breaker_state(name)):
            return 0
        return max(0, effective_max_rpm(name) - used)

    async def pick_chain(self, market: str) -> list[str]:
        """Ordered source list for a market group, pre-switching included.

        Normal case returns the static chain. When the preferred source keeps
        less than PRE_SWITCH_REMAINING_FRACTION of its minute budget, the
        ADMISSIBLE backup with the most budget left goes first — switching
        happens BEFORE a denial, so the denied path is the exception (§6.2).
        """
        chain = quote_chain(market)
        if not chain:
            return chain
        preferred = chain[0]
        budget = effective_max_rpm(preferred)
        left = await self._budget_left(preferred)
        if budget <= 0 or left >= PRE_SWITCH_REMAINING_FRACTION * budget:
            return chain

        alternatives = []
        for name in chain[1:]:
            remaining = await self._budget_left(name)
            if remaining > 0:
                alternatives.append((remaining, name))
        if not alternatives:
            return chain  # no admissible backup: keep the preferred anyway

        alternatives.sort(key=lambda entry: (-entry[0], chain.index(entry[1])))
        best = alternatives[0][1]
        # Structured observability (fund-intraday-nav.md §13 M2): a pre-switch
        # means the preferred source is nearing its budget ceiling — worth an
        # INFO trail so a burst of switches is visible without scraping debug.
        logger.info(
            f"upstream_budget: pre-switch {market} chain {preferred}->{best} (preferred budget left {left}/{budget})"
        )
        return [best, *([n for n in chain if n != best])]

    # --- result reporting ---

    async def report_result(self, name: str, ok: bool, status_code: int | None = None) -> None:
        """Fold one request outcome into the breaker (§6.2).

        Success zeroes the failure streak and halves a running cooldown
        (SourceHealth-style stepped recovery). A denial escalates the cooldown
        ladder 60s × 2^(n-2) capped at 1800s. Non-denial failures (404, empty
        payload, parse errors) leave the breaker untouched. Redis failures are
        logged, never raised.
        """
        breaker_key = RedisKeys.quote_breaker_key(name)
        try:
            client = await get_redis_client()
            if ok:
                state = await self._breaker_state(name)
                if state is None:
                    # Streak may still exist without a cooldown: clear it.
                    await client.delete(breaker_key)
                    return
                remaining = float(state.get("cooldown_until", 0)) - datetime.now(UTC).timestamp()
                if remaining > 0:
                    new_state = {
                        "cooldown_until": datetime.now(UTC).timestamp() + remaining / 2,
                        "consecutive_fails": 0,
                    }
                    await client.set(breaker_key, json.dumps(new_state), ex=int(remaining / 2) + 1)
                else:
                    await client.delete(breaker_key)
                return

            if not is_denial(ok, status_code):
                return

            state = await self._breaker_state(name) or {"cooldown_until": 0, "consecutive_fails": 0}
            fails = int(state.get("consecutive_fails", 0)) + 1
            cooldown = 0.0
            if fails >= BREAKER_DENIAL_THRESHOLD:
                cooldown = min(
                    BREAKER_BASE_COOLDOWN_SECONDS * (2 ** (fails - BREAKER_DENIAL_THRESHOLD)),
                    BREAKER_MAX_COOLDOWN_SECONDS,
                )
            new_state = {
                "cooldown_until": datetime.now(UTC).timestamp() + cooldown,
                "consecutive_fails": fails,
            }
            # EX = full cooldown so an expired breaker key self-cleans.
            await client.set(breaker_key, json.dumps(new_state), ex=max(int(cooldown), 1))
            logger.info(
                f"upstream_budget: {name} denial recorded (fails={fails}, "
                f"status={status_code}, cooldown={cooldown:.0f}s)"
            )
        except Exception as exc:
            logger.warning(f"upstream_budget: report_result failed for {name}: {exc}")


budget_governor = UpstreamBudgetGovernor()


def get_budget_governor() -> UpstreamBudgetGovernor:
    return budget_governor


# --- Symbol conversion (pure, §6.1 tail) ---
# Internal {stock_code, market} ↔ upstream-specific addressings. Snapshots
# carry an EastMoney secid when the holdings page link provided one; these
# helpers are the code-rule fallback and the Tencent/Sina mappings.

CN_SH_LEADING = frozenset("69")
CN_SZ_LEADING = frozenset("03")
CN_BJ_LEADING = frozenset("48")


def infer_stock_market(stock_code: str) -> str:
    """Heuristic market from a raw constituent code.

    Tickers with letters are US; <=5 digits are HK (EastMoney pads to 5 at
    display time); exact 6-digit codes are CN A-shares.
    """
    code = (stock_code or "").strip()
    if not code:
        return ""
    if any(ch.isalpha() for ch in code):
        return "US"
    if len(code) <= 5:
        return "HK"
    return "CN"


def _cn_exchange_prefix(stock_code: str) -> str:
    leading = (stock_code or "")[:1]
    if leading in CN_SH_LEADING:
        return "sh"
    if leading in CN_SZ_LEADING:
        return "sz"
    if leading in CN_BJ_LEADING:
        return "bj"
    return "sz"


def to_tencent_symbol(stock_code: str, market: str) -> str | None:
    """Tencent qt.gtimg.cn addressing: sh600519 / sz000858 / hk00700 / usAAPL."""
    code = (stock_code or "").strip()
    if not code:
        return None
    market = (market or "").upper()
    if market == "HK":
        return f"hk{code.zfill(5)}"
    if market == "US":
        return f"us{code.upper()}"
    return f"{_cn_exchange_prefix(code)}{code}"


def to_sina_symbol(stock_code: str, market: str) -> str | None:
    """Sina hq.sinajs.cn addressing: sh600519 / hk00700 / gb_aapl (lowercase).

    Beijing-exchange codes have no reliable sina equivalent → None (the chain
    simply skips this source for them).
    """
    code = (stock_code or "").strip()
    if not code:
        return None
    market = (market or "").upper()
    if market == "HK":
        return f"hk{code.zfill(5)}"
    if market == "US":
        return f"gb_{code.lower()}"
    if code[:1] in CN_BJ_LEADING:
        return None
    return f"{_cn_exchange_prefix(code)}{code}"


def to_em_secid(stock_code: str, market: str) -> str | None:
    """EastMoney secid from code rules (snapshots prefer the parsed secid).

    1. Shanghai / 0. Shenzhen / 116. HK / 105. US (NASDAQ default; NYSE 106
    misses fall through the chain, acceptable for HK-only M1 usage).
    """
    code = (stock_code or "").strip()
    if not code:
        return None
    market = (market or "").upper()
    if market == "HK":
        return f"116.{code.zfill(5)}"
    if market == "US":
        return f"105.{code.upper()}"
    prefix = "1" if code[:1] in CN_SH_LEADING else "0"
    return f"{prefix}.{code}"


def secid_market(secid: str) -> str | None:
    """Market from a parsed EastMoney secid ('1.600519' → CN, ...)."""
    if not secid or "." not in secid:
        return None
    prefix = secid.split(".", 1)[0]
    if prefix in ("1", "0"):
        return "CN"
    if prefix == "116":
        return "HK"
    if prefix in ("105", "106", "107"):
        return "US"
    return None
