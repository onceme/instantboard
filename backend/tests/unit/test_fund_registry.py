"""Unit tests for the fund registry cache (fund-intraday-nav.md §9.3).

Fixture rows are REAL catalog lines from
https://fund.eastmoney.com/js/fundcode_search.js (fetched 2026-09-01,
27,718 entries total) covering the three code families — new OTC series
(019118), legacy OTC (110011, 000001-collision) and listed ETF (510300) —
plus synthetic malformed rows.

NOTE: tests/conftest.py autouse-patches fund_registry's three entry seams
(get_registry / refresh_registry / _fetch_payload) so the suite never touches
the upstream catalog; the wiring tests below restore the real functions
(captured at import time) and patch the lower layers instead.
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import SymbolNotFound
from app.models.finance import FinanceSymbol
from app.services import fund_registry
from app.services.finance import FinanceService

# Captured before any fixture patching replaces the module attributes.
_ORIG_GET_REGISTRY = fund_registry.get_registry
_ORIG_REFRESH_REGISTRY = fund_registry.refresh_registry

# Real catalog rows (2026-09-01) + malformed rows exercising the tolerance
# rules: empty name, short row, non-list row, non-digit code, duplicate code,
# empty type/pinyin (which must be KEPT).
SAMPLE_PAYLOAD = (
    "\ufeffvar r = ["
    '["000001","HXCZHH","华夏成长混合","混合型-灵活","HUAXIACHENGZHANGHUNHE"],'
    '["019118","JSCCNSDKKJETFLJQDIIERMB","景顺长城纳斯达克科技ETF联接(QDII)E人民币","指数型-海外股票",'
    '"JINGSHUNCHANGCHENGNASIDAKEKEJIETFLIANJIEQDIIERENMINBI"],'
    '["110011","YFDYZJXHHQDII","易方达优质精选混合(QDII)","QDII-混合偏股","YIFANGDAYOUZHIJINGXUANHUNHEQDII"],'
    '["270042","GFNSDK100ETFLJRMBQDIIA","广发纳斯达克100ETF联接人民币(QDII)A","指数型-海外股票",'
    '"GUANGFANASIDAKE100ETFLIANJIERENMINBIQDIIA"],'
    '["510300","HS300ETFHTBR","沪深300ETF华泰柏瑞","指数型-股票","HUSHEN300ETFHUATAIBAIRUI"],'
    '["999001","BAD","","",""],'
    '["999002"],'
    '"not-a-row",'
    '["ABCDE","X","非数字代码","",""],'
    '["000001","DUPLICATE","重复代码条目","",""],'
    '["999003","NOPINYIN","无拼音基金","",""]'
    "];"
)
SAMPLE_BYTES = SAMPLE_PAYLOAD.encode("utf-8")
SAMPLE_COUNT = 6  # 000001, 019118, 110011, 270042, 510300, 999003


def _parse_sample() -> dict[str, fund_registry.FundRegistryEntry]:
    return fund_registry.parse_registry_payload(SAMPLE_BYTES)


def _registry() -> fund_registry.FundRegistry:
    return fund_registry.FundRegistry(_parse_sample(), "testversion", "2026-09-01T00:00:00+00:00")


class TestParsePayload:
    def test_parses_real_rows_with_bom_and_js_prefix(self):
        entries = _parse_sample()
        assert len(entries) == SAMPLE_COUNT
        entry = entries["019118"]
        assert entry.name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        assert entry.fund_type == "指数型-海外股票"
        assert entry.pinyin_full.startswith("JINGSHUNCHANGCHENG")
        assert entry.pinyin_abbr == "JSCCNSDKKJETFLJQDIIERMB"

    def test_all_three_code_families_present(self):
        entries = _parse_sample()
        for code in ("000001", "019118", "110011", "270042", "510300"):
            assert code in entries, code

    def test_malformed_rows_skipped(self):
        entries = _parse_sample()
        for bad in ("999001", "999002", "ABCDE"):
            assert bad not in entries

    def test_empty_type_and_pinyin_tolerated(self):
        entry = _parse_sample()["999003"]
        assert entry.name == "无拼音基金"
        assert entry.fund_type == ""
        assert entry.pinyin_full == ""
        assert entry.pinyin_abbr == "NOPINYIN"

    def test_duplicate_code_first_wins(self):
        assert _parse_sample()["000001"].name == "华夏成长混合"

    def test_rejects_oversize_payload(self, monkeypatch):
        monkeypatch.setattr(fund_registry, "MAX_REGISTRY_BYTES", 100)
        with pytest.raises(ValueError, match="too large"):
            fund_registry.parse_registry_payload(SAMPLE_BYTES)

    def test_rejects_too_many_rows(self, monkeypatch):
        monkeypatch.setattr(fund_registry, "MAX_REGISTRY_ENTRIES", 3)
        with pytest.raises(ValueError, match="exceed cap"):
            fund_registry.parse_registry_payload(SAMPLE_BYTES)

    def test_rejects_no_array_body(self):
        with pytest.raises(ValueError, match="no array body"):
            fund_registry.parse_registry_payload(b"var r = undefined;")

    def test_rejects_invalid_json(self):
        with pytest.raises(ValueError, match="invalid JSON"):
            fund_registry.parse_registry_payload(b"\xef\xbb\xbfvar r = [oops];")

    def test_rejects_empty_array(self):
        with pytest.raises(ValueError, match="no array body|empty"):
            fund_registry.parse_registry_payload("\ufeffvar r = [];".encode())

    def test_rejects_all_bad_rows(self):
        with pytest.raises(ValueError, match="zero valid rows"):
            fund_registry.parse_registry_payload('\ufeffvar r = [["x"], "y"];'.encode())

    def test_rejects_non_utf8(self):
        with pytest.raises(ValueError, match="UTF-8"):
            fund_registry.parse_registry_payload(b"\xff\xfe\x81var r = [];")


class TestFundRegistrySearch:
    def test_code_lookup_hit_and_miss(self):
        reg = _registry()
        assert reg.lookup_code("019118").name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        assert reg.lookup_code("999999") is None
        assert reg.lookup_code(None) is None

    def test_cjk_substring_exact(self):
        reg = _registry()
        assert [e.code for e in reg.search("易方达优质精选")] == ["110011"]
        assert [e.code for e in reg.search("纳斯达克")] == ["019118", "270042"]

    def test_cjk_weak_match_official_name_with_missing_token(self):
        # 「市值加权」appears in the official name but NOT in the abbreviated
        # catalog row; the weak matcher must still hit 019118 (the case that
        # motivated this feature).
        reg = _registry()
        assert [e.code for e in reg.search("景顺长城纳斯达克科技市值加权")] == ["019118"]
        assert [e.code for e in reg.search("景顺长城纳斯达克科技市值加权ETF联接")] == ["019118"]

    def test_cjk_weak_match_refuses_unrelated(self):
        reg = _registry()
        assert reg.search("某某银行市值加权产品") == []

    def test_short_cjk_never_weak_matches(self):
        # <8 chars → full-substring only (weak path disabled).
        reg = _registry()
        assert reg.search("加权") == []

    def test_pinyin_full_prefix_case_insensitive(self):
        reg = _registry()
        assert [e.code for e in reg.search("jingshunc")] == ["019118"]
        assert [e.code for e in reg.search("YIFANGDAYOUZHI")] == ["110011"]

    def test_pinyin_abbreviation_prefix(self):
        reg = _registry()
        assert [e.code for e in reg.search("yfdyzjxhh")] == ["110011"]
        assert [e.code for e in reg.search("HXCZHH")] == ["000001"]

    def test_ascii_name_substring(self):
        reg = _registry()
        assert [e.code for e in reg.search("ETF")] == ["019118", "270042", "510300"]

    def test_code_prefix(self):
        reg = _registry()
        assert [e.code for e in reg.search("0191")] == ["019118"]
        assert [e.code for e in reg.search("5103")] == ["510300"]

    def test_limit_respected(self):
        reg = _registry()
        assert len(reg.search("ETF", limit=2)) == 2

    def test_empty_and_blank_query(self):
        reg = _registry()
        assert reg.search("") == []
        assert reg.search("   ") == []
        assert reg.search("ETF", limit=0) == []


def _synthetic_registry(seed: int = 7, size: int = 600) -> fund_registry.FundRegistry:
    """Random-but-deterministic catalog exercising every ASCII index leg:
    pinyin full/abbreviated prefixes, digit-bearing pinyin, ASCII name tokens,
    code prefixes and CJK-only names."""
    import random

    rng = random.Random(seed)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    tokens = ["", "ETF", "QDII", "LOF", "100", "300ETF", "A", "(QDII)A", "ESG300", "C", "E人民币"]
    entries: dict[str, fund_registry.FundRegistryEntry] = {}
    code_num = 0
    for _ in range(size):
        code = f"{code_num:06d}"
        code_num += rng.randint(1, 97)
        body = "".join(rng.choice(letters) for _ in range(rng.randint(4, 28)))
        if rng.random() < 0.3:
            body = body[:3] + str(rng.randint(0, 999)) + body[3:]
        pinyin_full = body
        pinyin_abbr = body[:: rng.randint(2, 4)] if rng.random() < 0.9 else ""
        cjk = "示例基金名称" + "".join(
            rng.choice("甲乙丙丁戊己庚辛壬癸混合债券指数增强") for _ in range(rng.randint(0, 6))
        )
        name = cjk + rng.choice(tokens)
        entries[code] = fund_registry.FundRegistryEntry(code, name, "混合型-灵活", pinyin_full, pinyin_abbr)
    return fund_registry.FundRegistry(entries, "synthetic", "2026-09-01T00:00:00+00:00")


class TestAsciiIndexConsistency:
    """The indexed ASCII path must reproduce the legacy full-catalog scan
    exactly (same entries, same code-ascending order, same limit cutoff)."""

    @pytest.mark.parametrize("qu", ["A", "AB", "E", "ETF", "QDI", "300", "0", "00", "Z", "100E", "QDIIA", "0001"])
    def test_targeted_queries_match_legacy_scan(self, qu):
        reg = _synthetic_registry()
        assert reg._search_ascii_indexed(qu, 20) == reg._search_ascii_scan(qu, 20)

    def test_random_probes_match_legacy_scan(self):
        import random

        reg = _synthetic_registry()
        rng = random.Random(42)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        for _ in range(300):
            qu = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 5)))
            assert reg._search_ascii_indexed(qu, 20) == reg._search_ascii_scan(qu, 20), qu

    def test_search_dispatch_matches_legacy_scan(self):
        reg = _synthetic_registry()
        for q in ("ETF", "a", "300etf", "100", "0000"):
            assert reg.search(q, 20) == reg._search_ascii_scan(q.upper(), 20), q

    def test_real_sample_pinyin_prefixes_match_legacy_scan(self):
        reg = _registry()
        for q in ("HXCZHH", "jingshunc", "yfd", "G", "GUANGFANASIDAKE", "5103", "etf"):
            assert reg.search(q, 20) == reg._search_ascii_scan(q.upper(), 20), q

    def test_single_letter_uses_pinyin_bucket(self):
        reg = _registry()
        assert [e.code for e in reg.search("h")] == ["000001", "510300"]
        assert [e.code for e in reg.search("J")] == ["019118"]


class TestPlaceholderHelpers:
    def test_placeholder_roundtrip(self):
        assert fund_registry.fund_placeholder_name("019118") == "基金 019118"
        assert fund_registry.is_placeholder_name("基金 019118", "019118") is True
        assert fund_registry.is_placeholder_name("真实名", "019118") is False
        assert fund_registry.is_placeholder_name(None, "019118") is False


class TestSerializationRoundtrip:
    def test_serialize_deserialize(self):
        entries = _parse_sample()
        payload = fund_registry._serialize_data(entries, "v123", "2026-09-01T00:00:00+00:00")
        reg = fund_registry._deserialize_data(payload)
        assert reg is not None
        assert reg.version == "v123"
        assert reg.count == SAMPLE_COUNT
        assert reg.lookup_code("019118").name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"

    def test_deserialize_rejects_garbage(self):
        with pytest.raises(json.JSONDecodeError):
            fund_registry._deserialize_data("not json")
        assert fund_registry._deserialize_data(json.dumps({"entries": {}})) is None
        assert fund_registry._deserialize_data(json.dumps({"version": "v", "entries": {"12": []}})) is None


class TestLazyLoadWiring:
    """get_registry/refresh_registry layering with the lower seams patched.

    The real entry points are restored over the conftest autouse patch.
    """

    async def test_process_cache_served_without_redis(self):
        reg = _registry()
        fund_registry._set_process_cache(reg)
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock) as redis_load,
        ):
            assert await fund_registry.get_registry() is reg
        redis_load.assert_not_awaited()

    async def test_redis_hit_populates_process_cache(self):
        reg = _registry()
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=reg),
            patch.object(fund_registry, "_load_from_upstream", new_callable=AsyncMock) as upstream,
        ):
            assert await fund_registry.get_registry() is reg
        upstream.assert_not_awaited()

    async def test_cold_miss_fetches_stores_and_caches(self):
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_fetch_payload", new_callable=AsyncMock, return_value=SAMPLE_BYTES),
            patch.object(fund_registry, "_store_to_redis", new_callable=AsyncMock, return_value=True) as store,
        ):
            reg = await fund_registry.get_registry()
        assert reg is not None
        assert reg.count == SAMPLE_COUNT
        assert reg.version == hashlib.sha1(SAMPLE_BYTES).hexdigest()[:16]
        assert reg.persisted is True
        store.assert_awaited_once()

    async def test_fetch_failure_returns_none_and_marks(self):
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_fetch_payload", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_mark_failure", new_callable=AsyncMock) as mark,
        ):
            assert await fund_registry.get_registry() is None
        mark.assert_awaited_once()

    async def test_rejected_payload_returns_none_and_marks(self):
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_fetch_payload", new_callable=AsyncMock, return_value=b"var r = [];"),
            patch.object(fund_registry, "_mark_failure", new_callable=AsyncMock) as mark,
        ):
            assert await fund_registry.get_registry() is None
        mark.assert_awaited_once()

    async def test_failure_marker_short_circuits_fetch(self):
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_failure_marked", new_callable=AsyncMock, return_value=True),
            patch.object(fund_registry, "_fetch_payload", new_callable=AsyncMock) as fetch,
        ):
            assert await fund_registry.get_registry() is None
        fetch.assert_not_awaited()

    async def test_redis_store_failure_keeps_registry_unpersisted(self):
        with (
            patch.object(fund_registry, "get_registry", new=_ORIG_GET_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_fetch_payload", new_callable=AsyncMock, return_value=SAMPLE_BYTES),
            patch.object(fund_registry, "_store_to_redis", new_callable=AsyncMock, return_value=False),
        ):
            reg = await fund_registry.get_registry()
        assert reg is not None and reg.persisted is False

    async def test_load_from_redis_client_roundtrip(self):
        reg = _registry()
        data = fund_registry._serialize_data(reg._by_code, reg.version, reg.fetched_at)
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[json.dumps({"version": reg.version}), data])
        with patch.object(fund_registry, "get_redis_client", new_callable=AsyncMock, return_value=client):
            got = await fund_registry._load_from_redis()
        assert got is not None and got.count == SAMPLE_COUNT

    async def test_load_from_redis_corrupt_data_returns_none(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[json.dumps({"version": "v1"}), "not-json"])
        with patch.object(fund_registry, "get_redis_client", new_callable=AsyncMock, return_value=client):
            assert await fund_registry._load_from_redis() is None

    async def test_refresh_noop_when_cache_fresh(self):
        fund_registry._set_process_cache(_registry())
        with (
            patch.object(fund_registry, "refresh_registry", new=_ORIG_REFRESH_REGISTRY),
            patch.object(fund_registry, "_load_from_upstream", new_callable=AsyncMock) as upstream,
        ):
            assert await fund_registry.refresh_registry() is True
        upstream.assert_not_awaited()

    async def test_refresh_fetches_when_stale(self):
        reg = _registry()
        with (
            patch.object(fund_registry, "refresh_registry", new=_ORIG_REFRESH_REGISTRY),
            patch.object(fund_registry, "_load_from_redis", new_callable=AsyncMock, return_value=None),
            patch.object(fund_registry, "_load_from_upstream", new_callable=AsyncMock, return_value=reg) as upstream,
        ):
            assert await fund_registry.refresh_registry() is True
        upstream.assert_awaited_once()


def _svc(db=None):
    return FinanceService(db=db or AsyncMock(), redis=None)


def _empty_tier_result():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    return result


class TestRegistrySearchIntegration:
    """FinanceService.search_symbols with a loaded registry."""

    async def test_code_hit_registers_real_name_and_short_circuits_external(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_empty_tier_result() for _ in range(3)] + [_empty_tier_result()])
        db.add = MagicMock()
        db.commit = AsyncMock()
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock) as external,
        ):
            page = await svc.search_symbols("t1", "019118")
        assert page["meta"]["total"] == 1
        row = page["data"][0]
        assert row["symbol"] == "019118"
        assert row["name"] == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        assert row["type"] == "fund"
        assert row["market"] == "CN"
        external.assert_not_awaited()
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert isinstance(added, FinanceSymbol)
        assert added.type == "fund"
        assert added.name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"

    async def test_code_hit_heals_existing_placeholder_row(self):
        db = AsyncMock()
        placeholder = MagicMock()
        placeholder.symbol = "019118"
        placeholder.name = "基金 019118"
        placeholder.type = "fund"
        placeholder.market = "CN"
        placeholder.exchange = ""
        placeholder.currency = "CNY"
        existing = MagicMock()
        existing.scalar_one_or_none.return_value = placeholder
        db.execute = AsyncMock(side_effect=[_empty_tier_result() for _ in range(3)] + [existing])
        db.commit = AsyncMock()
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock) as external,
        ):
            page = await svc.search_symbols("t1", "019118")
        assert page["data"][0]["name"] == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        assert placeholder.name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        db.commit.assert_awaited()
        db.add.assert_not_called()
        external.assert_not_awaited()

    async def test_code_hit_syncs_name_of_db_tier_result(self):
        # DB tier already surfaced a spelling variant: no duplicate result, and
        # a placeholder name on that row heals to the registry name.
        db = AsyncMock()
        db_row = MagicMock()
        db_row.symbol = "510300.SS"
        db_row.name = "基金 510300"
        db_row.type = "fund"
        db_row.market = "CN"
        db_row.exchange = "SS"
        db_row.currency = "CNY"
        exact = MagicMock()
        exact.scalars.return_value.all.return_value = [db_row]
        exact.scalar_one_or_none.return_value = None
        existing = MagicMock()
        existing.scalar_one_or_none.return_value = db_row
        db.execute = AsyncMock(side_effect=[exact, _empty_tier_result(), _empty_tier_result(), existing])
        db.commit = AsyncMock()
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.finance.FinanceService._get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
        ):
            page = await svc.search_symbols("t1", "510300")
        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == "510300.SS"
        assert page["data"][0]["name"] == "沪深300ETF华泰柏瑞"
        db.add.assert_not_called()

    async def test_text_query_appends_candidates_without_registering(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        db.add = MagicMock()
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock) as external,
        ):
            page = await svc.search_symbols("t1", "易方达优质精选")
        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == "110011"
        assert page["data"][0]["type"] == "fund"
        assert page["data"][0]["name"] == "易方达优质精选混合(QDII)"
        db.add.assert_not_called()  # candidates register at selection time
        external.assert_not_awaited()  # registry candidates skip the external hop

    async def test_cjk_miss_skips_external_when_registry_loaded(self):
        # The registry owns CJK retrieval: a zero-candidate Chinese query never
        # reaches Yahoo (it cannot know OTC funds), saving the external hop.
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock) as external,
        ):
            page = await svc.search_symbols("t1", "根本不存在某某基金")
        assert page["meta"]["total"] == 0
        external.assert_not_awaited()

    async def test_pinyin_miss_skips_external_when_registry_loaded(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock) as external,
        ):
            page = await svc.search_symbols("t1", "ZZQQXYZWVU")
        assert page["meta"]["total"] == 0
        external.assert_not_awaited()

    async def test_ticker_miss_still_reaches_external_when_registry_loaded(self):
        # Short ASCII letter runs stay ticker-shaped (AAPL ≤5 chars): the
        # external index keeps its chance even with a loaded registry.
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        svc = _svc(db)
        external_rows = [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "type": "stock",
                "market": "US",
                "exchange": "NASDAQ",
                "current_price": None,
                "change_percent": None,
                "currency": "USD",
            }
        ]
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch.object(
                FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=external_rows
            ) as external,
        ):
            page = await svc.search_symbols("t1", "AAPL")
        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == "AAPL"
        external.assert_awaited_once()

    async def test_type_filter_excludes_registry_entirely(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        svc = _svc(db)
        registry_mock = AsyncMock(return_value=_registry())
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch("app.services.fund_registry.get_registry", new=registry_mock),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=[]),
        ):
            page = await svc.search_symbols("t1", "易方达优质精选", type="stock")
        assert page["meta"]["total"] == 0
        registry_mock.assert_not_awaited()

    async def test_registry_unavailable_degrades_to_heuristic(self):
        # get_registry() → None (conftest default): the M2 heuristic path must
        # still register a bare OTC-range code with the placeholder name.
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_empty_tier_result())
        db.add = MagicMock()
        db.commit = AsyncMock()
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.services.finance.redis_set", new_callable=AsyncMock),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=[]),
        ):
            page = await svc.search_symbols("t1", "005827", type="fund")
        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == "005827"
        assert page["data"][0]["name"] == "基金 005827"
        db.add.assert_called_once()


class TestWatchlistSelectionRegistration:
    """add_to_watchlist's registry fallback (§9.3 register-on-select)."""

    def _db_for_new_fund(self, fin_symbol):
        db = AsyncMock()
        exact = MagicMock()
        exact.scalar_one_or_none.return_value = None
        variant = MagicMock()
        variant.scalar_one_or_none.return_value = None
        ensure_existing = MagicMock()
        ensure_existing.scalar_one_or_none.return_value = None
        count = MagicMock()
        count.scalar.return_value = 0
        dup = MagicMock()
        dup.scalar_one_or_none.return_value = None
        order = MagicMock()
        order.scalar.return_value = -1
        symbol_result = MagicMock()
        symbol_result.scalar_one_or_none.return_value = fin_symbol
        db.execute = AsyncMock(side_effect=[exact, variant, ensure_existing, count, dup, order, symbol_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        async def _refresh(item):
            item.__dict__.update(
                id="item-id",
                symbol_id="sym-id",
                display_order=0,
                notes=None,
                alert_threshold_percent=None,
            )

        db.refresh = AsyncMock(side_effect=_refresh)
        return db

    async def test_selected_registry_fund_registers_and_ingests(self):
        fin_symbol = MagicMock()
        fin_symbol.id = "sym-id"
        fin_symbol.symbol = "019118"
        fin_symbol.type = "fund"
        fin_symbol.name = "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        db = self._db_for_new_fund(fin_symbol)
        svc = _svc(db)
        with (
            patch("app.services.finance.redis_delete", new_callable=AsyncMock),
            patch("app.services.finance.FinanceService._get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            patch("app.services.fund_holdings.invalidate_followed_codes_cache", new_callable=AsyncMock),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as spawn,
        ):
            result = await svc.add_to_watchlist("t1", "u1", {"symbol": "019118"})
        assert result["symbol"] == "019118"
        registered = db.add.call_args_list[0][0][0]
        assert isinstance(registered, FinanceSymbol)
        assert registered.symbol == "019118"
        assert registered.type == "fund"
        assert registered.name == "景顺长城纳斯达克科技ETF联接(QDII)E人民币"
        spawn.assert_called_once_with("019118")

    async def test_unknown_code_not_in_registry_still_raises(self):
        db = AsyncMock()
        none_result = MagicMock()
        none_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=none_result)
        svc = _svc(db)
        with (
            patch("app.services.fund_registry.get_registry", new=AsyncMock(return_value=_registry())),
            pytest.raises(SymbolNotFound),
        ):
            await svc.add_to_watchlist("t1", "u1", {"symbol": "999999"})
