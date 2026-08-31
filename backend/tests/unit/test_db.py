"""Unit tests for app/db package."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── session.py ───────────────────────────────────────────────────
class TestSessionModule:
    def test_engine_import(self):
        from app.db.session import engine

        assert engine is not None

    def test_async_session_factory(self):
        from app.db.session import async_session_factory

        assert async_session_factory is not None

    async def test_get_db_session_generator(self):
        import app.db.session as session_mod
        from app.db.session import get_db_session

        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        with patch.object(session_mod, "async_session_factory") as mock_factory:
            mock_factory.return_value = mock_session
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            gen = get_db_session()
            session = await gen.__anext__()
            assert session == mock_session
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()


# ── init_db.py ───────────────────────────────────────────────────
class TestInitDBModule:
    def test_finance_sources_constant(self):
        from app.db.init_db import FINANCE_SOURCES

        assert isinstance(FINANCE_SOURCES, list)
        assert len(FINANCE_SOURCES) > 0
        first = FINANCE_SOURCES[0]
        assert "name" in first
        assert "source_type" in first
        assert "url" in first
        assert "config" in first
        assert "refresh_interval_seconds" in first

    def test_commodity_seed_symbols_match_display_config(self):
        """Consistency guard: the seeded commodity symbols must equal COMMODITIES_CONFIG,
        otherwise some displayed commodities never receive data (and seed symbols are wasted).
        Regression for the ZC=F (corn) seed vs Brent (BZ=F) display mismatch."""
        from app.db.init_db import FINANCE_SOURCES
        from app.services.finance import COMMODITIES_CONFIG

        commodity_sources = [
            src
            for src in FINANCE_SOURCES
            if src.get("config", {}).get("library") == "yfinance"
            and any("=F" in sym for sym in src.get("config", {}).get("symbols", []))
        ]
        assert len(commodity_sources) == 1
        seed_symbols = set(commodity_sources[0]["config"]["symbols"])
        config_symbols = {item["symbol"] for item in COMMODITIES_CONFIG}
        assert seed_symbols == config_symbols

    def test_hackernews_seeds_resolve_to_hackernews_collector(self):
        """Regression: HN seeds must use the Firebase API collector (fills
        extra_data.hn_score, so tech hot_score HN weighting works) instead of
        hnrss.org RSS feeds (no score field, weighting stuck at 0)."""
        from app.collectors import resolve_collector
        from app.collectors.tech.hackernews_collector import HackerNewsCollector
        from app.db.init_db import TECH_AI_SOURCES, TECH_ROBOTICS_SOURCES

        hn_sources = [src for src in TECH_AI_SOURCES + TECH_ROBOTICS_SOURCES if src["name"].startswith("HackerNews")]
        assert len(hn_sources) == 2
        for src in hn_sources:
            assert resolve_collector(src["source_type"], src.get("config")) is HackerNewsCollector
            assert src["refresh_interval_seconds"] == 120
            assert src.get("is_active", True) is True
            assert src["config"].get("query")

    def test_reddit_seed_is_active_and_collectable(self):
        """The Reddit cross-domain seed must be collectable out of the box:
        config.library=reddit overrides source_type=social (bare "social" has no
        collector) and config.subreddits feeds RedditCollector's per-subreddit
        fetch. Regression against the seed reverting to the inactive template
        (missing library → resolve_collector None → NoCollectorAvailable on the
        create/enable path, scheduler skipping it as uncollectable)."""
        from app.collectors import resolve_collector
        from app.collectors.tech.reddit_collector import RedditCollector
        from app.db.init_db import TECH_CROSS_DOMAIN_SOURCES

        reddit_sources = [src for src in TECH_CROSS_DOMAIN_SOURCES if src["config"].get("library") == "reddit"]
        assert len(reddit_sources) == 1
        src = reddit_sources[0]
        assert src["name"] == "Reddit-科技全领域"
        assert src["source_type"] == "social"
        assert src["is_active"] is True
        assert src["config"]["subreddits"] == ["artificial", "robotics", "embedded", "space"]
        assert src["refresh_interval_seconds"] == 600
        assert resolve_collector(src["source_type"], src["config"]) is RedditCollector

    def test_active_seeds_all_resolve_to_collectors(self):
        """Every seed with is_active=True must resolve to a registered collector.
        Otherwise the scheduler skips the job ('No collector for ...') and the
        create/enable API rejects the source with NoCollectorAvailable — active
        seeds that can never collect are exactly the state the pre-flight check
        exists to prevent."""
        from app.collectors import resolve_collector
        from app.db.init_db import (
            FINANCE_SOURCES,
            TECH_AI_SOURCES,
            TECH_CROSS_DOMAIN_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
        )

        all_sources = (
            FINANCE_SOURCES
            + TECH_AI_SOURCES
            + TECH_ROBOTICS_SOURCES
            + TECH_EMBEDDED_SOURCES
            + TECH_SPACE_SOURCES
            + TECH_CROSS_DOMAIN_SOURCES
        )
        assert all_sources
        for src in all_sources:
            if src.get("is_active", True):
                assert resolve_collector(src["source_type"], src.get("config")) is not None, src["name"]

    def test_tech_sources_constants(self):
        from app.db.init_db import TECH_AI_SOURCES, TECH_EMBEDDED_SOURCES, TECH_ROBOTICS_SOURCES, TECH_SPACE_SOURCES

        assert isinstance(TECH_AI_SOURCES, list)
        assert isinstance(TECH_ROBOTICS_SOURCES, list)
        assert isinstance(TECH_EMBEDDED_SOURCES, list)
        assert isinstance(TECH_SPACE_SOURCES, list)
        assert len(TECH_AI_SOURCES) > 0
        assert len(TECH_ROBOTICS_SOURCES) > 0
        assert len(TECH_EMBEDDED_SOURCES) > 0
        assert len(TECH_SPACE_SOURCES) > 0

    def test_cross_domain_sources(self):
        from app.db.init_db import TECH_CROSS_DOMAIN_SOURCES

        assert isinstance(TECH_CROSS_DOMAIN_SOURCES, list)
        assert len(TECH_CROSS_DOMAIN_SOURCES) > 0

    async def test_create_tables(self):
        from app.db.init_db import create_tables

        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        mocked_begin_ctx = AsyncMock()
        mocked_begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mocked_begin_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_engine = AsyncMock()
        mock_engine.begin = MagicMock(return_value=mocked_begin_ctx)
        mock_engine.dispose = AsyncMock()

        with (
            patch("app.db.init_db.create_async_engine", return_value=mock_engine),
            patch("app.db.init_db.settings") as mock_settings,
        ):
            mock_settings.database_url = "postgresql+asyncpg://test"
            await create_tables()
            mock_conn.run_sync.assert_called_once()

    async def test_seed_idempotent_when_all_exists(self):
        """Idempotent seeding: when tenants/categories/sources all exist, re-running seed creates nothing."""
        from app.db.init_db import (
            FINANCE_SOURCES,
            FUND_INDEX_BINDINGS,
            FUND_SYMBOL_SEEDS,
            TECH_AI_SOURCES,
            TECH_CROSS_DOMAIN_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
            seed_default_data,
        )

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        existing = MagicMock()
        all_seed_names = [
            src["name"]
            for src in (
                FINANCE_SOURCES
                + TECH_AI_SOURCES
                + TECH_ROBOTICS_SOURCES
                + TECH_EMBEDDED_SOURCES
                + TECH_SPACE_SOURCES
                + TECH_CROSS_DOMAIN_SOURCES
            )
        ]

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count in (1, 2, 3, 4):
                # system tenant / default tenant / finance category / tech category all exist
                r.scalar_one_or_none.return_value = existing
            elif call_count == 5:
                # all seed sources already exist
                r.all.return_value = [(name,) for name in all_seed_names]
            elif call_count == 6:
                # all fund index bindings already exist (fund-intraday-nav.md §3.3 seed)
                r.all.return_value = [(code,) for code, _ in FUND_INDEX_BINDINGS]
            elif call_count == 7:
                # all fund symbols already exist with type='fund' (idempotent:
                # seed_fund_symbols adds nothing and reconciles nothing)
                r.scalars.return_value.all.return_value = [
                    MagicMock(symbol=symbol, type="fund") for symbol, _ in FUND_SYMBOL_SEEDS
                ]
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()

        mock_session.add.assert_not_called()
        mock_session.commit.assert_called()

    async def test_seed_backfills_missing_sources_when_tenants_exist(self):
        """Idempotent seeding: when tenants exist (old logic skipped entirely), missing sources are still created."""
        from app.db.init_db import (
            FINANCE_SOURCES,
            TECH_AI_SOURCES,
            TECH_CROSS_DOMAIN_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
            seed_default_data,
        )
        from app.models.source import Source, SourceHealth

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        added_objects = []
        mock_session.add.side_effect = lambda obj: added_objects.append(obj)

        existing = MagicMock()
        all_defs = (
            FINANCE_SOURCES
            + TECH_AI_SOURCES
            + TECH_ROBOTICS_SOURCES
            + TECH_EMBEDDED_SOURCES
            + TECH_SPACE_SOURCES
            + TECH_CROSS_DOMAIN_SOURCES
        )

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count in (1, 2, 3, 4):
                # tenants and categories already exist (the parts backfilled after a historically interrupted seed)
                r.scalar_one_or_none.return_value = existing
            elif call_count == 5:
                # only the first seed source exists, the rest are missing
                r.all.return_value = [(all_defs[0]["name"],)]
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()

        mock_session.commit.assert_called()
        source_count = sum(1 for obj in added_objects if isinstance(obj, Source))
        health_count = sum(1 for obj in added_objects if isinstance(obj, SourceHealth))
        assert source_count == len(all_defs) - 1
        assert health_count == len(all_defs) - 1

    async def test_seed_default_data_full_run(self):
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count in (1, 2, 3, 4):
                # system tenant / default tenant / finance category / tech category all missing
                r.scalar_one_or_none.return_value = None
            elif call_count == 5:
                # existing source names: none
                r.all.return_value = []
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()
        mock_session.commit.assert_called()
        mock_session.add.assert_called()

    async def test_init_db_calls_create_and_seed(self):
        with (
            patch("app.db.init_db.create_tables", new_callable=AsyncMock) as mock_create,
            patch("app.db.init_db.seed_default_data", new_callable=AsyncMock) as mock_seed,
        ):
            from app.db.init_db import init_db

            await init_db()
            mock_create.assert_called_once()
            mock_seed.assert_called_once()

    async def test_seed_existing_system_tenant(self):
        """When system tenant already exists, skip creating it but continue seeding the rest."""
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        existing_tenant = MagicMock()
        existing_tenant.id = "00000000-0000-0000-0000-000000000000"

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                # system tenant exists
                r.scalar_one_or_none.return_value = existing_tenant
            elif call_count == 2:
                # default tenant does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 3:
                # finance category does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 4:
                # tech category does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 5:
                r.all.return_value = []  # no sources yet
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()
        mock_session.commit.assert_called()

    async def test_seed_existing_default_tenant(self):
        """When default tenant already exists."""
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        existing_default = MagicMock()
        existing_default.id = "11111111-1111-1111-1111-111111111111"

        existing_system = MagicMock()
        existing_system.id = "00000000-0000-0000-0000-000000000000"

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = existing_system
            elif call_count == 2:
                # default tenant already exists
                r.scalar_one_or_none.return_value = existing_default
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # finance cat missing
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # tech cat missing
            elif call_count == 5:
                r.all.return_value = []  # no sources yet
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()
        mock_session.commit.assert_called()

    async def test_seed_existing_categories(self):
        """When finance and tech categories already exist."""
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        existing_system = MagicMock()
        existing_system.id = "00000000-0000-0000-0000-000000000000"

        existing_finance_cat = MagicMock()
        existing_finance_cat.id = "cat-finance"
        existing_tech_cat = MagicMock()
        existing_tech_cat.id = "cat-tech"

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = existing_system
            elif call_count == 2:
                r.scalar_one_or_none.return_value = None  # default tenant missing
            elif call_count == 3:
                # finance category exists
                r.scalar_one_or_none.return_value = existing_finance_cat
            elif call_count == 4:
                # tech category exists
                r.scalar_one_or_none.return_value = existing_tech_cat
            elif call_count == 5:
                r.all.return_value = []  # no sources
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()
        mock_session.commit.assert_called()

    async def test_seed_existing_sources_skip(self):
        """Do not recreate sources when all seed sources already exist (deduplicated by name)."""
        from app.db.init_db import (
            FINANCE_SOURCES,
            FUND_INDEX_BINDINGS,
            FUND_SYMBOL_SEEDS,
            TECH_AI_SOURCES,
            TECH_CROSS_DOMAIN_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
            seed_default_data,
        )

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        all_seed_names = [
            src["name"]
            for src in (
                FINANCE_SOURCES
                + TECH_AI_SOURCES
                + TECH_ROBOTICS_SOURCES
                + TECH_EMBEDDED_SOURCES
                + TECH_SPACE_SOURCES
                + TECH_CROSS_DOMAIN_SOURCES
            )
        ]

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = None  # no system tenant
            elif call_count == 2:
                r.scalar_one_or_none.return_value = None  # no default tenant
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # no finance cat
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # no tech cat
            elif call_count == 5:
                # new flow: query existing source names, all present
                r.all.return_value = [(name,) for name in all_seed_names]
            elif call_count == 6:
                # fund index bindings all present → binding seed skips
                # (fund-intraday-nav.md §3.3)
                r.all.return_value = [(code,) for code, _ in FUND_INDEX_BINDINGS]
            elif call_count == 7:
                # fund symbols all present as type='fund' → seed adds nothing
                # (fund-intraday-nav.md M2 phase A)
                r.scalars.return_value.all.return_value = [
                    MagicMock(symbol=symbol, type="fund") for symbol, _ in FUND_SYMBOL_SEEDS
                ]
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()
        mock_session.commit.assert_called()
        # add is called only 4 times for tenants/categories, never for
        # sources/bindings/fund symbols
        assert mock_session.add.call_count == 4  # system tenant, default tenant, finance cat, tech cat

    async def test_seed_full_source_creation(self):
        """Full source creation path with all source types."""
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        # Track what gets added
        added_objects = []

        def track_add(obj):
            added_objects.append(obj)

        mock_session.add.side_effect = track_add

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = None  # no system tenant
            elif call_count == 2:
                r.scalar_one_or_none.return_value = None  # no default tenant
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # no finance cat
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # no tech cat
            elif call_count == 5:
                r.all.return_value = []  # no sources
            return r

        with (
            patch("app.db.init_db.async_session_factory", return_value=mock_session),
            patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)),
        ):
            await seed_default_data()

        # Should have created system tenant, default tenant, 2 categories, and many sources
        mock_session.commit.assert_called()
        # Verify sources were added (finance + tech sources)
        from app.db.init_db import (
            FINANCE_SOURCES,
            TECH_AI_SOURCES,
            TECH_CROSS_DOMAIN_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
        )

        total_tech = (
            len(TECH_AI_SOURCES)
            + len(TECH_ROBOTICS_SOURCES)
            + len(TECH_EMBEDDED_SOURCES)
            + len(TECH_SPACE_SOURCES)
            + len(TECH_CROSS_DOMAIN_SOURCES)
        )
        expected_sources = len(FINANCE_SOURCES) + total_tech
        # Count Source objects added (excluding tenants and categories)
        from app.models.source import Source, SourceHealth

        source_count = sum(1 for obj in added_objects if isinstance(obj, Source))
        health_count = sum(1 for obj in added_objects if isinstance(obj, SourceHealth))
        assert source_count == expected_sources
        assert health_count == expected_sources
