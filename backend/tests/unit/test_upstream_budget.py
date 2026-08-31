"""Unit tests for the upstream budget governor (fund-intraday-nav.md §6).

Covers the §6.1 registry + symbol conversion, minute-window budget gating,
the <20% pre-emptive chain switch, the circuit-breaker cooldown ladder
(60s × 2^(n-2) capped at 1800s) with halved recovery on success, and the
fail-open behavior when Redis is unavailable (§12).
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.services.upstream_budget import (
    UPSTREAM_REGISTRY,
    UpstreamBudgetGovernor,
    effective_max_rpm,
    get_spec,
    infer_stock_market,
    is_denial,
    quote_chain,
    secid_market,
    to_em_secid,
    to_sina_symbol,
    to_tencent_symbol,
)


def _mock_redis_client(**overrides):
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.incr = AsyncMock(return_value=1)
    client.decr = AsyncMock(return_value=0)
    client.expire = AsyncMock(return_value=True)
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


def _patched_governor(client=None):
    """Governor with get_redis_client patched to return `client`."""
    governor = UpstreamBudgetGovernor()
    patcher = patch(
        "app.services.upstream_budget.get_redis_client",
        new_callable=AsyncMock,
        return_value=client or _mock_redis_client(),
    )
    return governor, patcher


class TestRegistryAndChains:
    def test_registry_defaults_match_design_table(self):
        assert UPSTREAM_REGISTRY["tencent_qt"].batch_limit == 500
        assert UPSTREAM_REGISTRY["tencent_qt"].max_rpm == 120
        assert UPSTREAM_REGISTRY["tencent_qt"].burst == 6
        assert UPSTREAM_REGISTRY["sina_hq"].batch_limit == 400
        assert UPSTREAM_REGISTRY["sina_hq"].required_headers == {"Referer": "https://finance.sina.com.cn"}
        assert UPSTREAM_REGISTRY["em_push2"].max_rpm == 6
        assert UPSTREAM_REGISTRY["em_push2"].burst == 1
        assert UPSTREAM_REGISTRY["em_push2_delay"].max_rpm == 40
        assert UPSTREAM_REGISTRY["em_f10_holdings"].batch_limit == 1
        assert UPSTREAM_REGISTRY["em_f10_holdings"].max_rpm == 8

    def test_effective_rpm_applies_safety_factor(self):
        # settings default safety factor 0.8 → 120 → 96.
        assert effective_max_rpm("tencent_qt") == 96
        assert effective_max_rpm("em_f10_holdings") == 6

    def test_env_override_changes_effective_rpm(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "quote_tencent_max_rpm", 60)
        assert get_spec("tencent_qt").max_rpm == 60
        assert effective_max_rpm("tencent_qt") == 48
        monkeypatch.setattr(settings, "quote_tencent_max_rpm", 120)

    def test_quote_chains(self):
        assert quote_chain("CN") == ["tencent_qt", "sina_hq"]
        assert quote_chain("US") == ["tencent_qt", "sina_hq"]
        # HK: main domain stays OUT by default (IP red line, §6.3).
        assert quote_chain("HK") == ["tencent_qt", "sina_hq", "em_push2_delay", "em_push2_m1"]

    def test_hk_main_domain_only_when_switch_flipped(self, monkeypatch):
        from app.config import settings

        assert settings.quote_eastmoney_main_enabled is False
        monkeypatch.setattr(settings, "quote_eastmoney_main_enabled", True)
        assert "em_push2" in quote_chain("HK")
        monkeypatch.setattr(settings, "quote_eastmoney_main_enabled", False)

    def test_is_denial_classification(self):
        assert is_denial(False, 403) is True
        assert is_denial(False, 429) is True
        assert is_denial(False, 500) is True
        assert is_denial(False, None) is True  # connection-level failure
        assert is_denial(False, 404) is False  # per-item miss, not upstream denial
        assert is_denial(True, None) is False


class TestSymbolConversion:
    def test_tencent_symbols(self):
        assert to_tencent_symbol("600519", "CN") == "sh600519"
        assert to_tencent_symbol("000858", "CN") == "sz000858"
        assert to_tencent_symbol("300750", "CN") == "sz300750"
        assert to_tencent_symbol("00700", "HK") == "hk00700"
        assert to_tencent_symbol("AAPL", "US") == "usAAPL"

    def test_sina_symbols(self):
        assert to_sina_symbol("600519", "CN") == "sh600519"
        assert to_sina_symbol("00700", "HK") == "hk00700"
        assert to_sina_symbol("AAPL", "US") == "gb_aapl"
        # Beijing exchange has no reliable sina equivalent.
        assert to_sina_symbol("430047", "CN") is None

    def test_em_secid(self):
        assert to_em_secid("600519", "CN") == "1.600519"
        assert to_em_secid("000858", "CN") == "0.000858"
        assert to_em_secid("00700", "HK") == "116.00700"
        assert to_em_secid("AAPL", "US") == "105.AAPL"

    def test_secid_market(self):
        assert secid_market("1.600519") == "CN"
        assert secid_market("0.000858") == "CN"
        assert secid_market("116.00700") == "HK"
        assert secid_market("105.AAPL") == "US"
        assert secid_market("106.BABA") == "US"
        assert secid_market("bogus") is None

    def test_infer_market(self):
        assert infer_stock_market("600519") == "CN"
        assert infer_stock_market("00700") == "HK"
        assert infer_stock_market("AAPL") == "US"


class TestBudgetGating:
    async def test_acquire_increments_minute_budget(self):
        client = _mock_redis_client()
        governor, patcher = _patched_governor(client)
        with patcher:
            assert await governor.acquire("tencent_qt") is True
        governor.release("tencent_qt")
        budget_key = client.incr.call_args.args[0]
        assert budget_key.startswith("quote_budget:tencent_qt:")
        # yyyymmddHHMM minute window suffix
        assert len(budget_key.split(":")[-1]) == 12
        client.expire.assert_awaited()

    async def test_acquire_refuses_when_budget_exhausted(self):
        # effective max for tencent = 96; incr returning 97 trips the check.
        client = _mock_redis_client(incr=AsyncMock(return_value=97))
        governor, patcher = _patched_governor(client)
        with patcher:
            assert await governor.acquire("tencent_qt") is False
        client.decr.assert_awaited_once()  # the increment is rolled back
        assert governor._in_flight.get("tencent_qt", 0) == 0

    async def test_acquire_refuses_when_breaker_active(self):
        future = datetime.now(UTC).timestamp() + 300
        client = _mock_redis_client(
            get=AsyncMock(return_value=json.dumps({"cooldown_until": future, "consecutive_fails": 3}))
        )
        governor, patcher = _patched_governor(client)
        with patcher:
            assert await governor.acquire("tencent_qt") is False
        client.incr.assert_not_awaited()  # budget is not even touched

    async def test_acquire_breaker_expired_is_admitted(self):
        past = datetime.now(UTC).timestamp() - 5
        client = _mock_redis_client(
            get=AsyncMock(return_value=json.dumps({"cooldown_until": past, "consecutive_fails": 2}))
        )
        governor, patcher = _patched_governor(client)
        with patcher:
            assert await governor.acquire("tencent_qt") is True
        governor.release("tencent_qt")

    async def test_burst_limit_blocks_concurrent_requests(self):
        client = _mock_redis_client()
        governor, patcher = _patched_governor(client)
        with patcher:
            for _ in range(6):  # tencent burst = 6
                assert await governor.acquire("tencent_qt") is True
            assert await governor.acquire("tencent_qt") is False
            governor.release("tencent_qt")
            assert await governor.acquire("tencent_qt") is True
        for _ in range(7):
            governor.release("tencent_qt")

    async def test_fail_open_when_redis_down(self):
        client = _mock_redis_client()
        client.get = AsyncMock(side_effect=RuntimeError("redis down"))
        client.incr = AsyncMock(side_effect=RuntimeError("redis down"))
        governor, patcher = _patched_governor(client)
        with patcher:
            # Redis outage must admit the request (§12 fail-open), not block it.
            assert await governor.acquire("tencent_qt") is True
        governor.release("tencent_qt")


class TestPreSwitching:
    async def test_pre_switch_when_preferred_budget_low(self):
        async def fake_get(key):
            if key.startswith("quote_budget:tencent_qt:"):
                return "90"  # 6 left of effective 96 (<20%) → switch
            if key.startswith("quote_budget:sina_hq:"):
                return "10"
            return None

        client = _mock_redis_client(get=AsyncMock(side_effect=fake_get))
        governor, patcher = _patched_governor(client)
        with patcher:
            chain = await governor.pick_chain("CN")
        assert chain == ["sina_hq", "tencent_qt"]

    async def test_no_switch_when_budget_healthy(self):
        client = _mock_redis_client(get=AsyncMock(return_value="5"))
        governor, patcher = _patched_governor(client)
        with patcher:
            chain = await governor.pick_chain("CN")
        assert chain == ["tencent_qt", "sina_hq"]

    async def test_no_switch_keeps_chain_when_backup_cooled_down(self):
        async def fake_get(key):
            if key.startswith("quote_budget:tencent_qt:"):
                return "95"  # nearly exhausted
            if key == "quote_breaker:sina_hq":
                return json.dumps({"cooldown_until": datetime.now(UTC).timestamp() + 500, "consecutive_fails": 4})
            return None

        client = _mock_redis_client(get=AsyncMock(side_effect=fake_get))
        governor, patcher = _patched_governor(client)
        with patcher:
            chain = await governor.pick_chain("CN")
        assert chain == ["tencent_qt", "sina_hq"]

    async def test_pre_switch_fails_open_on_redis_error(self):
        client = _mock_redis_client(get=AsyncMock(side_effect=RuntimeError("down")))
        governor, patcher = _patched_governor(client)
        with patcher:
            chain = await governor.pick_chain("CN")
        assert chain == ["tencent_qt", "sina_hq"]


class TestBreakerLadder:
    async def _report_denial(self, existing_fails: int) -> dict:
        state = {"cooldown_until": 0, "consecutive_fails": existing_fails}
        client = _mock_redis_client(get=AsyncMock(return_value=json.dumps(state)))
        governor, patcher = _patched_governor(client)
        with patcher:
            await governor.report_result("tencent_qt", ok=False, status_code=403)
        assert client.set.await_count == 1
        args, kwargs = client.set.call_args
        return {"key": args[0], "payload": json.loads(args[1]), "ex": kwargs.get("ex")}

    async def test_denial_ladder_60_120_240_capped_1800(self):
        first = await self._report_denial(1)  # fails → 2: first cooldown
        assert first["payload"]["consecutive_fails"] == 2
        assert first["ex"] == 60

        second = await self._report_denial(2)  # fails → 3
        assert second["ex"] == 120

        third = await self._report_denial(3)  # fails → 4
        assert third["ex"] == 240

        capped = await self._report_denial(11)  # 60×2^9 = 30720 → capped
        assert capped["ex"] == 1800

    async def test_first_denial_no_cooldown(self):
        first = await self._report_denial(0)  # fails → 1: below threshold
        assert first["payload"]["consecutive_fails"] == 1
        assert first["ex"] == 1  # marker only, cooldown_until ≈ now

    async def test_non_denial_failure_does_not_escalate(self):
        client = _mock_redis_client()
        governor, patcher = _patched_governor(client)
        with patcher:
            await governor.report_result("em_f10_holdings", ok=False, status_code=404)
        client.set.assert_not_awaited()

    async def test_success_zeroes_streak_without_cooldown(self):
        state = {"cooldown_until": 0, "consecutive_fails": 5}
        client = _mock_redis_client(get=AsyncMock(return_value=json.dumps(state)))
        governor, patcher = _patched_governor(client)
        with patcher:
            await governor.report_result("tencent_qt", ok=True)
        client.delete.assert_awaited_once()
        client.set.assert_not_awaited()

    async def test_success_halves_running_cooldown(self):
        future = datetime.now(UTC).timestamp() + 100
        state = {"cooldown_until": future, "consecutive_fails": 3}
        client = _mock_redis_client(get=AsyncMock(return_value=json.dumps(state)))
        governor, patcher = _patched_governor(client)
        with patcher:
            await governor.report_result("tencent_qt", ok=True)
        args, kwargs = client.set.call_args
        payload = json.loads(args[1])
        assert payload["consecutive_fails"] == 0
        # remaining ~100s halved → ~50s cooldown left
        remaining = payload["cooldown_until"] - datetime.now(UTC).timestamp()
        assert 40 < remaining < 60
        assert 45 <= kwargs.get("ex") <= 55

    async def test_success_with_no_state_is_noop(self):
        client = _mock_redis_client()
        governor, patcher = _patched_governor(client)
        with patcher:
            await governor.report_result("tencent_qt", ok=True)
        client.set.assert_not_awaited()
        client.delete.assert_awaited_once()  # idempotent clear
