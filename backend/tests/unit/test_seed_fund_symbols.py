"""Tests for seed_fund_symbols type reconciliation (fund-intraday-nav.md Task C).

The fund-symbol seed is an idempotent backfill keyed by the normalized 6-digit
code. When a row for a seed fund code already exists but carries the wrong
type (historical test contamination registered a fund code as 'stock'), the
seed reconciles the type to 'fund'. The reconciliation must:
  - correct a pre-existing stock row for a seed code to fund,
  - create missing seed codes,
  - leave non-seed symbols untouched,
  - be idempotent across runs,
  - only ever move a row toward 'fund', never the reverse.

These tests use the real test database (db_session fixture) rather than mocks
so the select/add/flush interplay is exercised end-to-end.
"""

import uuid

import pytest
import pytest_asyncio

from app.db.init_db import FUND_SYMBOL_SEEDS, seed_fund_symbols
from app.models.finance import FinanceSymbol
from app.models.tenant import Tenant

pytestmark = pytest.mark.asyncio


@pytest.fixture
def seed_tenant_id() -> uuid.UUID:
    # A dedicated tenant per test invocation; each test rolls back.
    return uuid.uuid4()


@pytest.fixture
async def seed_tenant(db_session, seed_tenant_id):
    tenant = Tenant(
        id=seed_tenant_id,
        name="Seed Recon Tenant",
        slug=f"seed-recon-{seed_tenant_id.hex[:8]}",
        plan="enterprise",
        settings={},
        is_active=True,
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


def _make_symbol(tenant_id: uuid.UUID, symbol: str, type_: str, name: str = "x") -> FinanceSymbol:
    return FinanceSymbol(
        tenant_id=tenant_id,
        symbol=symbol,
        name=name,
        type=type_,
        market="CN",
        exchange="",
        currency="CNY",
        is_active=True,
    )


async def _get_symbol(db_session, tenant_id: uuid.UUID, symbol: str) -> FinanceSymbol | None:
    from sqlalchemy import select

    result = await db_session.execute(
        select(FinanceSymbol).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.symbol == symbol,
        )
    )
    return result.scalars().first()


class TestSeedFundSymbolsReconciliation:
    async def test_reconciles_misregistered_stock_to_fund(self, db_session, seed_tenant):
        """A seed fund code stored as 'stock' is corrected to 'fund'."""
        db_session.add(_make_symbol(seed_tenant.id, "510300.SS", "stock", "HUATAI-PINEBRIDGE FUND"))
        await db_session.flush()

        seeded, reconciled = await seed_fund_symbols(db_session, seed_tenant.id)

        assert reconciled == 1
        row = await _get_symbol(db_session, seed_tenant.id, "510300.SS")
        assert row is not None
        assert row.type == "fund"
        # The pre-existing row is updated in place — name is preserved.
        assert row.name == "HUATAI-PINEBRIDGE FUND"
        # All other seed codes were created.
        assert seeded == len(FUND_SYMBOL_SEEDS) - 1

    async def test_empty_db_seeds_all(self, db_session, seed_tenant):
        seeded, reconciled = await seed_fund_symbols(db_session, seed_tenant.id)
        assert seeded == len(FUND_SYMBOL_SEEDS)
        assert reconciled == 0
        from sqlalchemy import func, select

        count = (
            await db_session.execute(
                select(func.count())
                .select_from(FinanceSymbol)
                .where(
                    FinanceSymbol.tenant_id == seed_tenant.id,
                    FinanceSymbol.type == "fund",
                )
            )
        ).scalar()
        assert count == len(FUND_SYMBOL_SEEDS)

    async def test_idempotent_across_runs(self, db_session, seed_tenant):
        """Running the seed twice yields no new rows and no re-reconciliation."""
        db_session.add(_make_symbol(seed_tenant.id, "510300.SS", "stock"))
        await db_session.flush()

        seeded1, reconciled1 = await seed_fund_symbols(db_session, seed_tenant.id)
        assert reconciled1 == 1
        assert seeded1 == len(FUND_SYMBOL_SEEDS) - 1

        # Second run: everything now exists as 'fund'.
        seeded2, reconciled2 = await seed_fund_symbols(db_session, seed_tenant.id)
        assert seeded2 == 0
        assert reconciled2 == 0

        from sqlalchemy import func, select

        count = (
            await db_session.execute(
                select(func.count()).select_from(FinanceSymbol).where(FinanceSymbol.tenant_id == seed_tenant.id)
            )
        ).scalar()
        assert count == len(FUND_SYMBOL_SEEDS)

    async def test_non_seed_symbols_untouched(self, db_session, seed_tenant):
        """A non-seed symbol (e.g. a stock not in FUND_SYMBOL_SEEDS) is not
        modified, and a pre-existing 'fund' row is not re-counted as reconciled."""
        # Non-seed stock symbol → must stay untouched.
        db_session.add(_make_symbol(seed_tenant.id, "600519", "stock", "Kweichow Moutai"))
        # A seed code already correctly 'fund' → reconciled must stay 0 for it.
        db_session.add(_make_symbol(seed_tenant.id, "510300.SS", "fund", "already-fund"))
        await db_session.flush()

        seeded, reconciled = await seed_fund_symbols(db_session, seed_tenant.id)

        assert reconciled == 0  # the only pre-existing seed code was already fund
        assert seeded == len(FUND_SYMBOL_SEEDS) - 1
        stock_row = await _get_symbol(db_session, seed_tenant.id, "600519")
        assert stock_row.type == "stock"  # non-seed symbol untouched
        fund_row = await _get_symbol(db_session, seed_tenant.id, "510300.SS")
        assert fund_row.type == "fund"

    async def test_bare_code_contaminated_row_reconciled(self, db_session, seed_tenant):
        """A contaminated row stored as the bare code (no suffix) is matched by
        normalized code and reconciled, rather than creating a duplicate."""
        db_session.add(_make_symbol(seed_tenant.id, "510300", "stock"))
        await db_session.flush()

        seeded, reconciled = await seed_fund_symbols(db_session, seed_tenant.id)

        assert reconciled == 1
        # The bare row is corrected in place; no duplicate suffixed row is created.
        bare = await _get_symbol(db_session, seed_tenant.id, "510300")
        suffixed = await _get_symbol(db_session, seed_tenant.id, "510300.SS")
        assert bare is not None and bare.type == "fund"
        assert suffixed is None
        assert seeded == len(FUND_SYMBOL_SEEDS) - 1
