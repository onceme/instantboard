"""Fund holdings ingestion subsystem (fund-intraday-nav.md §5).

Fetches the latest disclosed top-N holdings for Chinese mutual funds from the
EastMoney f10 jjcc archive endpoint, replaces each fund's snapshot wholesale
when a newer report period appears, and maintains the per-fund disclosure
meta (freshness / coverage / anomaly state) consumed by the intraday
estimator. Every upstream request goes through the budget governor
(§5.5: ≤8 req/min with an 80% safety margin — no burst path may punch
through the budget).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys, get_redis_client, redis_delete, redis_get, redis_set
from app.models.finance import (
    DISCLOSURE_STATUS_ANOMALOUS,
    DISCLOSURE_STATUS_OK,
    DISCLOSURE_STATUS_STALE,
    FinanceSymbol,
    FundHoldingsMeta,
    FundHoldingSnapshot,
    FundIndexBinding,
    FundNAVEstimate,
)
from app.services.upstream_budget import (
    get_budget_governor,
    infer_stock_market,
    secid_market,
    to_em_secid,
)

logger = logging.getLogger(__name__)

# Disclosure anomaly threshold (days, fund-intraday-nav.md §5.4): report
# periods older than this mark the fund anomalous (estimates fall back to
# index tracking / latest official and the UI warns).
FUND_HOLDINGS_ANOMALOUS_DAYS = 730

# The `topline` parameter caps holdings rows returned PER report period. The
# default endpoint value of 10 returns only the top-10 holdings per period;
# quarterly reports disclose only top-10 anyway, but semi-annual / annual
# reports disclose the full book, which needs a larger topline to be returned.
# Probed 2026-09-01 (polite, topline=10 → 10 rows/period, topline=30 → ~20
# rows/period for a fund disclosing ~20 stocks). Driven by
# settings.fund_holdings_topline (fund-intraday-nav.md §13 M3 §1.2). See §1.2
# — parameterized so full-holdings ingestion lifts coverage beyond top-10.
F10_JJCC_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline={topline}"
# Mandatory: without a fundf10.eastmoney.com Referer the endpoint answers 404
# (verified 2026-08-31). Browser-style UA matches the TiantianFundCollector
# convention.
F10_REFERER = "https://fundf10.eastmoney.com/"
F10_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 instantboard-fund-nav/1.0"
)
FETCH_TIMEOUT_SECONDS = 15

# Patient acquisition (daily cron path, §5.5): when the per-minute budget is
# exhausted, wait and retry instead of skipping — the daily job must refresh
# every followed fund, and serial pacing is exactly how it stays under the
# 8-req/min budget (~7.5s natural spacing). 24 × 5s caps one fund's wait at
# two minutes so a wedged budget can never stall the whole nightly round.
PATIENT_RETRY_ATTEMPTS = 24
PATIENT_RETRY_SLEEP_SECONDS = 5.0

# `var apidata={ content:"<html>", ...}` — the inner HTML uses single-quoted
# attributes, so a non-greedy match up to the closing `",` / `"}` extracts it.
_APIDATA_CONTENT_RE = re.compile(r'content:"(.*?)"\s*[,}]', re.DOTALL)
_REPORT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TABLE_OPEN_RE = re.compile(r"<table\b", re.IGNORECASE)
# EastMoney unify quote link carrying the secid (.../unify/r/1.600519).
_SECID_LINK_RE = re.compile(r"/unify/r/(\d+\.[\w.]+)")


@dataclass
class HoldingRow:
    stock_code: str
    stock_name: str | None
    market: str
    secid: str | None
    weight_percent: float
    shares_held: float | None = None


@dataclass
class HoldingsParseResult:
    report_date: date | None = None
    rows: list[HoldingRow] = field(default_factory=list)


def _build_soup(html: str) -> BeautifulSoup | None:
    """BeautifulSoup with lxml → html.parser fallback (WebScrapeCollector
    convention)."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception as exc:  # noqa: BLE001 - parser availability guard
        logger.info(f"fund_holdings: lxml parser failed ({exc}); falling back to html.parser")
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception as exc2:  # noqa: BLE001
            logger.warning(f"fund_holdings: html.parser fallback failed too: {exc2}")
            return None


def _parse_weight_text(text: str) -> float | None:
    cleaned = (text or "").strip()
    if not cleaned.endswith("%"):
        return None
    try:
        return float(cleaned[:-1].strip().replace(",", ""))
    except ValueError:
        return None


def _parse_shares_text(text: str) -> float | None:
    cleaned = (text or "").strip().replace(",", "")
    if not cleaned or "." in cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 1 else None


def _looks_like_security_code(stock_code: str | None) -> bool:
    """Heuristic separating real security codes from layout noise.

    CN A-shares are 6 digits, HK 5 (zero-padded), US tickers carry letters —
    the live jjcc table's leading 序号 (row index) column produces 1-2 digit
    pure-numeric cells that must never be mistaken for codes (2026-09-01
    incident: index cells ingested as HK codes, estimates degraded).
    """
    code = (stock_code or "").strip().upper()
    if not code or len(code) > 10:
        return False
    return not (code.isdigit() and len(code) < 5)


def _parse_holding_row(cells: list) -> HoldingRow | None:
    """One holdings table row → HoldingRow (None: not a data row).

    Two live column layouts are handled (2026-09-01 probe): the compact shape
    (code link | name | ... | weight%) and the f10 archive shape, which adds a
    leading 序号 (index) column plus 最新价/涨跌幅/相关资讯 columns before the
    weight. The code cell is therefore located by scanning for the first cell
    carrying an EastMoney unify quote link (its href embeds the secid); only
    when no cell links do we fall back to the legacy cells[0] text. Everything
    past the code cell is classified defensively: the LAST percent cell is the
    weight, the first integer-ish cell is the share count, decimals
    (price/value) and prose cells (related-news links) are ignored.
    """
    if not cells:
        return None

    stock_code: str | None = None
    secid: str | None = None
    stock_name: str | None = None
    code_index = -1

    for idx, cell in enumerate(cells):
        for link in cell.find_all("a", href=True):
            href_match = _SECID_LINK_RE.search(str(link.get("href") or ""))
            if not href_match:
                continue
            secid = href_match.group(1)
            link_text = link.get_text(strip=True)
            if link_text:
                stock_code = link_text.upper()
                code_index = idx
            break
        if code_index >= 0:
            break

    if code_index < 0:
        first_cell_text = cells[0].get_text(strip=True)
        if first_cell_text and re.fullmatch(r"[\w.]{1,10}", first_cell_text):
            stock_code = first_cell_text.upper()
            code_index = 0
    if not stock_code or not _looks_like_security_code(stock_code):
        return None

    name_cell = cells[code_index + 1] if code_index + 1 < len(cells) else None
    if name_cell is not None:
        name_link = name_cell.find("a", href=True)
        if name_link is not None and name_link.get_text(strip=True):
            stock_name = name_link.get_text(strip=True)
        else:
            plain = name_cell.get_text(strip=True)
            if plain and not plain.endswith("%"):
                stock_name = plain

    shares: float | None = None
    percent_cells: list[float] = []
    for cell in cells[code_index + 1 :]:
        text = cell.get_text(strip=True)
        cell_weight = _parse_weight_text(text)
        if cell_weight is not None:
            percent_cells.append(cell_weight)
            continue
        if shares is None:
            shares = _parse_shares_text(text)
    if not percent_cells:
        return None
    weight = percent_cells[-1]
    if weight <= 0:
        return None

    market = secid_market(secid) if secid else None
    if not market:
        market = infer_stock_market(stock_code)
        secid = to_em_secid(stock_code, market)

    return HoldingRow(
        stock_code=stock_code,
        stock_name=stock_name,
        market=market or "",
        secid=secid,
        weight_percent=weight,
        shares_held=shares,
    )


def parse_f10_jjcc(response_text: str) -> HoldingsParseResult:
    """Parse the jjcc archive payload into the newest report period's rows.

    The response carries one box per recent report period; report dates appear
    as text markers before each table. Strategy (§5.3): slice the content at
    every table start, associate each slice with the closest PRECEDING date
    marker, keep the newest period, and replace the fund's whole snapshot from
    it. Rows without a code cell or a percent cell are skipped defensively; a
    site redesign yields an empty result, never a crash.
    """
    result = HoldingsParseResult()

    match = _APIDATA_CONTENT_RE.search(response_text or "")
    if not match:
        return result
    content_html = match.group(1)

    table_starts = [m.start() for m in _TABLE_OPEN_RE.finditer(content_html)]
    if not table_starts:
        return result
    date_positions = [(m.start(), m.group(1)) for m in _REPORT_DATE_RE.finditer(content_html)]

    period_rows: dict[date, list[HoldingRow]] = {}
    for index, start in enumerate(table_starts):
        # Slice one table's HTML (up to the next table) and parse it in
        # isolation — avoids re-serialized position matching entirely.
        end = table_starts[index + 1] if index + 1 < len(table_starts) else len(content_html)

        report_date: date | None = None
        preceding = [entry for entry in date_positions if entry[0] <= start]
        for _, raw_date in reversed(preceding):
            try:
                report_date = date.fromisoformat(raw_date)
                break
            except ValueError:
                continue

        table_soup = _build_soup(content_html[start:end])
        if table_soup is None:
            continue
        for tr in table_soup.find_all("tr"):
            row = _parse_holding_row(tr.find_all("td"))
            if row is not None:
                key = report_date if report_date is not None else date.min
                period_rows.setdefault(key, []).append(row)

    if not period_rows:
        return result

    dated = {d: rows for d, rows in period_rows.items() if d != date.min}
    if dated:
        newest = max(dated)
        result.report_date = newest
        result.rows = dated[newest]
    else:
        result.rows = period_rows.get(date.min, [])
    return result


class FundHoldingsService:
    def __init__(self, db: AsyncSession, governor=None):
        self.db = db
        self.governor = governor or get_budget_governor()

    # --- budget-gated fetch ---

    async def _acquire_slot(self, name: str, patient: bool) -> bool:
        """acquire() with optional bounded waiting (§5.5 daily-job pacing)."""
        attempts = 0
        while True:
            if await self.governor.acquire(name):
                return True
            if not patient or attempts >= PATIENT_RETRY_ATTEMPTS:
                return False
            attempts += 1
            await asyncio.sleep(PATIENT_RETRY_SLEEP_SECONDS)

    async def _fetch_archive(self, fund_code: str, patient: bool = False) -> tuple[str | None, int | None]:
        """One jjcc request against the f10 endpoint. Returns (body, status);
        body is None whenever the request is refused/denied/failed.

        patient=True (daily cron) waits for the per-minute budget to refill;
        patient=False (watchlist hook / REST lazy ingest) skips immediately and
        leaves the fund for the daily job — no path may burst through the
        budget (§5.5)."""
        if not await self._acquire_slot("em_f10_holdings", patient):
            logger.debug(f"fund_holdings: budget exhausted for {fund_code}, deferring to next round")
            return None, None
        try:
            headers = {
                "Referer": F10_REFERER,
                "User-Agent": F10_USER_AGENT,
                "Accept": "text/javascript, application/javascript, */*",
            }
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    F10_JJCC_URL.format(code=fund_code, topline=settings.fund_holdings_topline),
                    headers=headers,
                )
            await self.governor.report_result("em_f10_holdings", response.status_code == 200, response.status_code)
            if response.status_code != 200:
                logger.warning(f"fund_holdings: HTTP {response.status_code} for {fund_code}")
                return None, response.status_code
            return response.text, 200
        except httpx.HTTPError as exc:
            # Connection-level failure: status None → denial escalation (§6.2).
            await self.governor.report_result("em_f10_holdings", ok=False, status_code=None)
            logger.warning(f"fund_holdings: fetch failed for {fund_code}: {exc}")
            return None, None
        finally:
            self.governor.release("em_f10_holdings")

    # --- ingestion ---

    async def ingest_fund(self, fund_code: str, symbol_id=None, patient: bool = False) -> bool:
        """Fetch + replace the holdings snapshot of one fund (§5.3/§5.4).

        Returns True when a snapshot was stored. Any per-fund failure is logged
        into the meta row (or swallowed with a warning) and retried by the
        daily job — no aggressive retries (§5.4). patient=True makes budget
        acquisition wait (daily cron serial pacing).
        """
        now = datetime.now(UTC)
        body, status = await self._fetch_archive(fund_code, patient=patient)
        if body is None:
            await self._record_meta_error(fund_code, symbol_id, f"fetch failed (status={status})")
            return False

        parsed = parse_f10_jjcc(body)

        if parsed.report_date is None or not parsed.rows:
            if parsed.report_date is None and not parsed.rows:
                # Unparsable payload (redesign / in-band error): keep the old
                # snapshot, record the failure for the daily retry.
                await self._record_meta_error(fund_code, symbol_id, "unparsable jjcc payload")
                return False
            # Zero holdings on a parseable payload → anomalous disclosure (§5.4).
            await self._replace_snapshot(fund_code, symbol_id, None, [], now)
            return False

        await self._replace_snapshot(fund_code, symbol_id, parsed.report_date, parsed.rows, now)
        return True

    async def _replace_snapshot(
        self,
        fund_code: str,
        symbol_id,
        report_date: date | None,
        rows: list[HoldingRow],
        now: datetime,
    ) -> None:
        """Transactional wholesale snapshot replacement + meta upsert."""
        try:
            await self.db.execute(delete(FundHoldingSnapshot).where(FundHoldingSnapshot.fund_code == fund_code))
            for row in rows:
                self.db.add(
                    FundHoldingSnapshot(
                        tenant_id=SYSTEM_TENANT_ID,
                        fund_code=fund_code,
                        symbol_id=symbol_id,
                        report_date=report_date,
                        stock_code=row.stock_code,
                        stock_name=row.stock_name,
                        market=row.market,
                        secid=row.secid,
                        weight_percent=row.weight_percent,
                        shares_held=row.shares_held,
                        fetched_at=now,
                    )
                )

            status = self._disclosure_status(report_date, len(rows))
            top10 = round(sum(r.weight_percent for r in rows), 4) if rows else None
            await self._upsert_meta(fund_code, symbol_id, report_date, top10, len(rows), status, now, None)

            await self.db.commit()
            await self._write_cache(fund_code, report_date, top10, status, rows)
            logger.info(
                f"fund_holdings: {fund_code} snapshot updated "
                f"(report={report_date}, holdings={len(rows)}, status={status})"
            )
        except Exception as exc:
            logger.warning(f"fund_holdings: snapshot replace failed for {fund_code}: {exc}")
            with contextlib.suppress(Exception):
                await self.db.rollback()

    def _disclosure_status(self, report_date: date | None, holdings_count: int) -> str:
        if report_date is None or holdings_count == 0:
            return DISCLOSURE_STATUS_ANOMALOUS
        age_days = (datetime.now(UTC).date() - report_date).days
        if age_days > FUND_HOLDINGS_ANOMALOUS_DAYS:
            return DISCLOSURE_STATUS_ANOMALOUS
        if age_days > settings.fund_nav_holdings_fresh_days:
            return DISCLOSURE_STATUS_STALE
        return DISCLOSURE_STATUS_OK

    async def _upsert_meta(
        self,
        fund_code: str,
        symbol_id,
        report_date: date | None,
        top10_weight_sum: float | None,
        holdings_count: int,
        status: str,
        now: datetime,
        last_error: str | None,
    ) -> None:
        result = await self.db.execute(select(FundHoldingsMeta).where(FundHoldingsMeta.fund_code == fund_code))
        meta = result.scalar_one_or_none()
        if meta is None:
            meta = FundHoldingsMeta(tenant_id=SYSTEM_TENANT_ID, fund_code=fund_code)
            self.db.add(meta)
        meta.symbol_id = symbol_id if symbol_id is not None else meta.symbol_id
        if report_date is not None:
            meta.latest_report_date = report_date
            meta.top10_weight_sum = top10_weight_sum
            meta.holdings_count = holdings_count
            meta.disclosure_status = status
        else:
            meta.disclosure_status = status
        meta.last_fetched_at = now
        meta.last_error = last_error

    async def _record_meta_error(self, fund_code: str, symbol_id, message: str) -> None:
        """Persist last_error for the daily retry without disturbing existing
        snapshot data (403/404/parse failures, §5.4)."""
        logger.warning(f"fund_holdings: {fund_code}: {message}")
        try:
            await self._upsert_meta(
                fund_code, symbol_id, None, None, 0, DISCLOSURE_STATUS_OK, datetime.now(UTC), message
            )
            await self.db.commit()
        except Exception as exc:
            logger.warning(f"fund_holdings: meta error record failed for {fund_code}: {exc}")
            with contextlib.suppress(Exception):
                await self.db.rollback()

    async def _codes_with_unusable_snapshots(self, fund_codes: list[str]) -> set[str]:
        """Codes whose stored snapshot carries no plausible security code at
        all — the signature of rows ingested by a broken parser (2026-09-01
        序号-column incident). Such snapshots look current to the meta gate
        but can never produce quotes, so they must be re-ingested once the
        parser is fixed. Empty result when every code has ≥1 valid row."""
        if not fund_codes:
            return set()
        result = await self.db.execute(
            select(FundHoldingSnapshot.fund_code, FundHoldingSnapshot.stock_code).where(
                FundHoldingSnapshot.fund_code.in_(fund_codes)
            )
        )
        valid: set[str] = set()
        present: set[str] = set()
        for fund_code, stock_code in result.all():
            present.add(fund_code)
            if _looks_like_security_code(stock_code):
                valid.add(fund_code)
        return {code for code in fund_codes if code in present and code not in valid}

    async def codes_needing_ingestion(self, fund_codes: list[str]) -> set[str]:
        """Batched needs_ingestion gate for the REST lazy-ingest fallback
        (§5.1 case 3): one query instead of N. Codes with a snapshot whose
        rows carry no plausible security code are re-ingested too (repair
        path for parser-broken legacy snapshots)."""
        if not fund_codes:
            return set()
        result = await self.db.execute(select(FundHoldingsMeta).where(FundHoldingsMeta.fund_code.in_(fund_codes)))
        metas = {meta.fund_code: meta for meta in result.scalars().all()}
        needing: set[str] = set()
        for code in fund_codes:
            meta = metas.get(code)
            if meta is None or meta.last_error or meta.latest_report_date is None:
                needing.add(code)
        needing |= await self._codes_with_unusable_snapshots([c for c in fund_codes if c not in needing])
        return needing

    async def needs_ingestion(self, fund_code: str) -> bool:
        """Add-to-watchlist hook gate (§5.1): ingest when there is no snapshot
        yet or the last attempt failed; freshness updates are the daily job's
        business. A stored snapshot without a single plausible security code
        counts as missing (parser-broken legacy rows get replaced)."""
        result = await self.db.execute(
            select(func.count()).select_from(FundHoldingsMeta).where(FundHoldingsMeta.fund_code == fund_code)
        )
        meta_count = result.scalar() or 0
        if meta_count == 0:
            return True
        meta_result = await self.db.execute(select(FundHoldingsMeta).where(FundHoldingsMeta.fund_code == fund_code))
        meta = meta_result.scalar_one_or_none()
        if meta is None:
            return True
        if meta.last_error:
            return True
        if meta.latest_report_date is None:
            return True
        return bool(await self._codes_with_unusable_snapshots([fund_code]))

    # --- read path (cache through) ---

    async def get_holdings(self, fund_code: str) -> dict | None:
        """Latest snapshot + meta for one fund: Redis first (TTL 1h), PG
        fallback (§7.2 step 6). None when the fund has never been ingested."""
        cache_key = RedisKeys.fund_holdings_key(fund_code)
        try:
            cached = await redis_get(cache_key)
            if cached:
                payload = json.loads(cached)
                if isinstance(payload, dict):
                    return payload
        except Exception as exc:  # noqa: BLE001 - cache degrades to PG
            logger.debug(f"fund_holdings: cache read failed for {fund_code}: {exc}")

        rows_result = await self.db.execute(
            select(FundHoldingSnapshot)
            .where(FundHoldingSnapshot.fund_code == fund_code)
            .order_by(FundHoldingSnapshot.report_date.desc())
        )
        rows = list(rows_result.scalars().all())
        meta_result = await self.db.execute(select(FundHoldingsMeta).where(FundHoldingsMeta.fund_code == fund_code))
        meta = meta_result.scalar_one_or_none()
        if not rows and meta is None:
            return None

        report_date = rows[0].report_date if rows else (meta.latest_report_date if meta else None)
        top10 = float(meta.top10_weight_sum) if meta and meta.top10_weight_sum is not None else None
        status = meta.disclosure_status if meta else DISCLOSURE_STATUS_ANOMALOUS
        payload = {
            "fund_code": fund_code,
            "report_date": report_date.isoformat() if report_date else None,
            "coverage_percent": top10,
            "disclosure_status": status,
            "holdings": [
                {
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "market": row.market,
                    "secid": row.secid,
                    "weight_percent": float(row.weight_percent),
                }
                for row in rows
                if (report_date is not None and row.report_date == report_date) or not rows
            ],
        }
        try:
            await redis_set(cache_key, json.dumps(payload), ex=RedisKeys.FUND_HOLDINGS_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"fund_holdings: cache write failed for {fund_code}: {exc}")
        return payload

    async def _write_cache(
        self,
        fund_code: str,
        report_date: date | None,
        top10: float | None,
        status: str,
        rows: list[HoldingRow],
    ) -> None:
        payload = {
            "fund_code": fund_code,
            "report_date": report_date.isoformat() if report_date else None,
            "coverage_percent": top10,
            "disclosure_status": status,
            "holdings": [
                {
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "market": row.market,
                    "secid": row.secid,
                    "weight_percent": row.weight_percent,
                }
                for row in rows
            ],
        }
        try:
            await redis_set(RedisKeys.fund_holdings_key(fund_code), json.dumps(payload), ex=RedisKeys.FUND_HOLDINGS_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"fund_holdings: cache write failed for {fund_code}: {exc}")

    # --- index bindings (read side of the dead-end fix, §3.3) ---

    async def resolve_index_binding(self, fund_code: str, symbol_id=None) -> tuple[str, float] | None:
        """Binding read order (§3.3): fund_index_bindings → residual
        fund_nav_estimates row of the same symbol → None.

        Returns (index_symbol, tracking_ratio).
        """
        if fund_code:
            result = await self.db.execute(select(FundIndexBinding).where(FundIndexBinding.fund_code == fund_code))
            binding = result.scalar_one_or_none()
            if binding is not None:
                return str(binding.index_symbol), float(binding.tracking_ratio or 1.0)

        if symbol_id is not None:
            legacy_result = await self.db.execute(
                select(FundNAVEstimate)
                .where(
                    FundNAVEstimate.symbol_id == symbol_id,
                    FundNAVEstimate.underlying_index_symbol.is_not(None),
                )
                .order_by(FundNAVEstimate.estimate_timestamp.desc())
                .limit(1)
            )
            legacy = legacy_result.scalar_one_or_none()
            if legacy is not None and legacy.underlying_index_symbol:
                return str(legacy.underlying_index_symbol), 1.0
        return None

    # --- followed-codes union (§5.2 / §8) ---

    async def get_followed_codes(self) -> tuple[list[str], dict[str, set[str]]]:
        """Cross-tenant union of followed fund codes + per-tenant maps.

        Cached as a Redis SET of "{tenant_id}:{fund_code}" members under
        fund_followed_codes (TTL 60s; watchlist mutations delete the key).
        Cache miss rebuilds from one PG join.
        """
        cache_key = RedisKeys.fund_followed_codes_key()
        try:
            client = await get_redis_client()
            members = await client.smembers(cache_key)
        except Exception as exc:  # noqa: BLE001 - rebuild from PG
            logger.debug(f"fund_holdings: followed-set read failed (rebuilding): {exc}")
            members = None

        if members:
            per_tenant: dict[str, set[str]] = {}
            for member in members:
                tenant_id, _, code = str(member).partition(":")
                if tenant_id and code:
                    per_tenant.setdefault(tenant_id, set()).add(code)
            union = sorted({code for codes in per_tenant.values() for code in codes})
            return union, per_tenant

        return await self._rebuild_followed_codes(cache_key)

    async def _rebuild_followed_codes(self, cache_key: str) -> tuple[list[str], dict[str, set[str]]]:
        from app.models.watchlist import WatchlistItem

        stmt = (
            select(WatchlistItem.tenant_id, FinanceSymbol.symbol)
            .join(FinanceSymbol, WatchlistItem.symbol_id == FinanceSymbol.id)
            .where(FinanceSymbol.type == "fund", FinanceSymbol.is_active)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        per_tenant: dict[str, set[str]] = {}
        for tenant_id, symbol in rows:
            code = _normalize_fund_code_shared(str(symbol or ""))
            if not code:
                continue
            per_tenant.setdefault(str(tenant_id), set()).add(code)

        union = sorted({code for codes in per_tenant.values() for code in codes})

        if per_tenant:
            try:
                client = await get_redis_client()
                members = [f"{tenant_id}:{code}" for tenant_id, codes in per_tenant.items() for code in codes]
                await client.delete(cache_key)
                if members:
                    await client.sadd(cache_key, *members)
                    await client.expire(cache_key, RedisKeys.FUND_FOLLOWED_CODES_TTL)
            except Exception as exc:  # noqa: BLE001 - cache is best-effort
                logger.debug(f"fund_holdings: followed-set write failed: {exc}")
        return union, per_tenant


async def invalidate_followed_codes_cache() -> None:
    """Watchlist add/remove hook (§5.2): force a union rebuild on next read."""
    try:
        await redis_delete(RedisKeys.fund_followed_codes_key())
    except Exception as exc:  # noqa: BLE001 - cache expiry (60s) bounds staleness
        logger.debug(f"fund_holdings: followed-set invalidation failed: {exc}")


def _normalize_fund_code_shared(symbol: str) -> str | None:
    """Local copy of FinanceService._normalize_fund_code semantics (kept
    import-cycle-free: finance.py imports this module for the watchlist hook).
    """
    code = str(symbol or "").strip().upper()
    for suffix in (".SS", ".SZ", ".OF"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
            break
    return code if len(code) == 6 and code.isdigit() else None


async def maybe_ingest_fund_holdings(fund_code: str) -> None:
    """Fire-and-forget hook body for add_to_watchlist (§5.1): ingest only when
    needed, never raise — failures are logged and the daily job retries."""
    try:
        from app.db.session import apply_service_context, async_session_factory

        async with async_session_factory() as session:
            await apply_service_context(session)
            service = FundHoldingsService(db=session)
            if await service.needs_ingestion(fund_code):
                await service.ingest_fund(fund_code)
    except Exception as exc:  # noqa: BLE001 - hook must never surface errors
        logger.warning(f"fund_holdings: hook ingestion failed for {fund_code}: {exc}")


def spawn_holdings_ingestion(fund_code: str) -> None:
    """Safe create_task wrapper: silently drops the hook when no event loop is
    running (sync unit-test contexts)."""
    try:
        asyncio.get_running_loop().create_task(maybe_ingest_fund_holdings(fund_code))
    except RuntimeError:
        return
