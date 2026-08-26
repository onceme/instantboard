"""Trading calendar + market holiday support (finance-tab.md §3.4.4).

Covers the static MARKET_HOLIDAYS table (structure + lookups), market_closed_reason
priorities, FinanceService._is_market_open holiday handling, get_market_status, and
the market_status_reason / holiday_name fields added by _format_market_indices.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.finance import (
    MARKET_INDICES_CONFIG,
    MARKET_TIMEZONES,
    FinanceService,
)
from app.services.market_calendar import (
    CLOSED_REASON_HOLIDAY,
    CLOSED_REASON_OFF_HOURS,
    CLOSED_REASON_WEEKEND,
    MARKET_HOLIDAYS,
    get_market_holiday_name,
    is_market_holiday,
    market_closed_reason,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def _service():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    return FinanceService(db, AsyncMock())


class TestMarketHolidaysTable:
    def test_keys_match_market_timezones(self):
        assert set(MARKET_HOLIDAYS) == set(MARKET_TIMEZONES)

    def test_entries_within_covered_years(self):
        for market, days in MARKET_HOLIDAYS.items():
            assert days, f"{market} holiday table is empty"
            for iso_date, name in days.items():
                parsed = date.fromisoformat(iso_date)
                assert 2025 <= parsed.year <= 2027, (market, iso_date)
                assert name, (market, iso_date)

    def test_cn_national_day_2026(self):
        assert is_market_holiday("CN", date(2026, 10, 1)) is True
        assert get_market_holiday_name("CN", date(2026, 10, 1)) == "国庆节"

    def test_us_christmas_2026(self):
        assert is_market_holiday("US", date(2026, 12, 25)) is True
        assert get_market_holiday_name("US", date(2026, 12, 25)) == "圣诞节"

    def test_weekend_without_holiday_not_a_holiday(self):
        assert is_market_holiday("CN", date(2026, 8, 22)) is False

    def test_regular_trading_day_not_a_holiday(self):
        assert is_market_holiday("CN", date(2026, 8, 25)) is False
        assert is_market_holiday("US", date(2026, 8, 25)) is False

    def test_years_outside_table_are_not_holidays(self):
        for market in MARKET_HOLIDAYS:
            assert is_market_holiday(market, date(2024, 12, 25)) is False
            assert is_market_holiday(market, date(2030, 1, 1)) is False
            assert get_market_holiday_name(market, date(2028, 5, 1)) is None

    def test_unknown_market(self):
        assert is_market_holiday("XX", date(2026, 1, 1)) is False
        assert get_market_holiday_name("XX", date(2026, 1, 1)) is None


class TestMarketClosedReason:
    def test_holiday_during_trading_hours(self):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)
        assert market_closed_reason("CN", now) == CLOSED_REASON_HOLIDAY

    def test_weekend(self):
        now = datetime(2026, 8, 22, 10, 0, tzinfo=SHANGHAI)
        assert market_closed_reason("CN", now) == CLOSED_REASON_WEEKEND

    def test_off_hours_before_session(self):
        now = datetime(2026, 8, 25, 8, 0, tzinfo=NEW_YORK)
        assert market_closed_reason("US", now) == CLOSED_REASON_OFF_HOURS

    def test_open_during_session(self):
        now = datetime(2026, 8, 25, 10, 0, tzinfo=NEW_YORK)
        assert market_closed_reason("US", now) is None

    def test_cn_lunch_break_is_off_hours(self):
        now = datetime(2026, 8, 25, 12, 0, tzinfo=SHANGHAI)
        assert market_closed_reason("CN", now) == CLOSED_REASON_OFF_HOURS

    def test_holiday_takes_priority_over_weekend(self):
        # 2026-12-26 is a Saturday but a fixed Xetra closure day.
        now = datetime(2026, 12, 26, 12, 0, tzinfo=BERLIN)
        assert market_closed_reason("DE", now) == CLOSED_REASON_HOLIDAY

    def test_utc_now_is_converted_to_market_timezone(self):
        # 2026-09-30T20:00Z is already 2026-10-01 04:00 in Shanghai.
        now = datetime(2026, 9, 30, 20, 0, tzinfo=ZoneInfo("UTC"))
        assert market_closed_reason("CN", now) == CLOSED_REASON_HOLIDAY

    def test_unknown_market_returns_none(self):
        assert market_closed_reason("XX", datetime(2026, 10, 1, tzinfo=SHANGHAI)) is None

    def test_now_defaults_to_local_clock(self):
        fixed = datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)
        with patch("app.services.market_calendar.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            assert market_closed_reason("CN") == CLOSED_REASON_HOLIDAY
        mock_dt.now.assert_called_once()


class TestIsMarketOpenWithCalendar:
    def test_holiday_inside_trading_hours_is_closed(self):
        service = _service()
        fixed = datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            assert service._is_market_open("CN") is False

    def test_weekday_non_holiday_inside_trading_hours_is_open(self):
        service = _service()
        fixed = datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            assert service._is_market_open("CN") is True

    def test_unknown_market_still_closed(self):
        assert _service()._is_market_open("UNKNOWN") is False


class TestGetMarketStatus:
    def test_holiday_reports_name(self):
        fixed = datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            status = _service().get_market_status("CN")
        assert status == {"status": "closed", "reason": "holiday", "holiday_name": "国庆节"}

    def test_open_market_has_no_reason(self):
        fixed = datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            status = _service().get_market_status("CN")
        assert status == {"status": "open", "reason": None}
        assert "holiday_name" not in status

    def test_off_hours_reason(self):
        fixed = datetime(2026, 8, 25, 8, 0, tzinfo=NEW_YORK)
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            status = _service().get_market_status("US")
        assert status == {"status": "closed", "reason": "off_hours"}

    def test_unknown_market(self):
        assert _service().get_market_status("XX") == {"status": "closed", "reason": None}


class TestFormatMarketIndicesClosedReason:
    RESULTS = [
        {"symbol": "^GSPC", "current_price": 5000, "change": 10, "change_percent": 0.2, "timestamp": "2026-01-01"},
        {
            "symbol": "000001.SS",
            "current_price": 3500,
            "change": -5,
            "change_percent": -0.14,
            "timestamp": "2026-01-01",
        },
    ]

    def _format(self, now):
        with patch("app.services.finance.datetime") as mock_dt:
            mock_dt.now.return_value = now
            return _service()._format_market_indices(self.RESULTS)

    def test_new_fields_present_on_every_entry(self):
        formatted = self._format(datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI))
        assert len(formatted) == len(MARKET_INDICES_CONFIG)
        for entry in formatted:
            assert "market_status" in entry
            assert "market_status_reason" in entry
            assert "holiday_name" in entry
            if entry["market_status"] == "open":
                assert entry["market_status_reason"] is None
                assert entry["holiday_name"] is None

    def test_holiday_entries_carry_reason_and_name(self):
        formatted = self._format(datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI))
        cn = next(e for e in formatted if e["symbol"] == "000001.SS")
        assert cn["market_status"] == "closed"
        assert cn["market_status_reason"] == "holiday"
        assert cn["holiday_name"] == "国庆节"

    def test_open_reason_stays_none(self):
        formatted = self._format(datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI))
        cn = next(e for e in formatted if e["symbol"] == "000001.SS")
        assert cn["market_status"] == "open"
        assert cn["market_status_reason"] is None
        assert cn["holiday_name"] is None

    @pytest.mark.parametrize(
        ("symbol", "field"),
        [
            ("^GSPC", "market_status_reason"),
            ("000001.SS", "market_status_reason"),
        ],
    )
    def test_reason_is_serializable_value(self, symbol, field):
        formatted = self._format(datetime(2026, 8, 25, 3, 0, tzinfo=SHANGHAI))
        entry = next(e for e in formatted if e["symbol"] == symbol)
        assert entry[field] in (None, "weekend", "holiday", "off_hours")
