"""Unit tests for app/db package."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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

    def test_tech_sources_constants(self):
        from app.db.init_db import TECH_AI_SOURCES, TECH_ROBOTICS_SOURCES, TECH_EMBEDDED_SOURCES, TECH_SPACE_SOURCES
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

        with patch("app.db.init_db.create_async_engine", return_value=mock_engine):
            with patch("app.db.init_db.settings") as mock_settings:
                mock_settings.database_url = "postgresql+asyncpg://test"
                await create_tables()
                mock_conn.run_sync.assert_called_once()

    async def test_seed_default_data_skip_existing(self):
        from app.db.init_db import seed_default_data

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.close = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            await seed_default_data()
        mock_session.close.assert_called_once()

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
            if call_count == 1:
                r.scalar.return_value = 0
            elif call_count in (2, 3, 4):
                r.scalar_one_or_none.return_value = None
            elif call_count == 5:
                r.scalar_one_or_none.return_value = None
            elif call_count == 6:
                r.scalar_one_or_none.return_value = None
            elif call_count == 7:
                r.scalar.return_value = 0
            else:
                pass
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
                await seed_default_data()
        mock_session.commit.assert_called()
        mock_session.add.assert_called()

    async def test_init_db_calls_create_and_seed(self):
        with patch("app.db.init_db.create_tables", new_callable=AsyncMock) as mock_create:
            with patch("app.db.init_db.seed_default_data", new_callable=AsyncMock) as mock_seed:
                from app.db.init_db import init_db
                await init_db()
                mock_create.assert_called_once()
                mock_seed.assert_called_once()

    async def test_seed_existing_system_tenant(self):
        """When system tenant already exists but no tenants (count=0 path won't hit, use separate logic)."""
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
                r.scalar.return_value = 0  # no tenants overall
            elif call_count == 2:
                # system tenant exists
                r.scalar_one_or_none.return_value = existing_tenant
            elif call_count == 3:
                # default tenant does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 4:
                # finance category does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 5:
                # tech category does not exist
                r.scalar_one_or_none.return_value = None
            elif call_count == 6:
                r.scalar.return_value = 0  # no sources yet
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
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
                r.scalar.return_value = 0
            elif call_count == 2:
                r.scalar_one_or_none.return_value = existing_system
            elif call_count == 3:
                # default tenant already exists
                r.scalar_one_or_none.return_value = existing_default
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # finance cat missing
            elif call_count == 5:
                r.scalar_one_or_none.return_value = None  # tech cat missing
            elif call_count == 6:
                r.scalar.return_value = 0
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
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
                r.scalar.return_value = 0
            elif call_count == 2:
                r.scalar_one_or_none.return_value = existing_system
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # default tenant missing
            elif call_count == 4:
                # finance category exists
                r.scalar_one_or_none.return_value = existing_finance_cat
            elif call_count == 5:
                # tech category exists
                r.scalar_one_or_none.return_value = existing_tech_cat
            elif call_count == 6:
                r.scalar.return_value = 0  # no sources
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
                await seed_default_data()
        mock_session.commit.assert_called()

    async def test_seed_existing_sources_skip(self):
        """When sources already exist for system tenant, skip source seeding."""
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
            if call_count == 1:
                r.scalar.return_value = 0
            elif call_count == 2:
                r.scalar_one_or_none.return_value = None  # no system tenant
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # no default tenant
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # no finance cat
            elif call_count == 5:
                r.scalar_one_or_none.return_value = None  # no tech cat
            elif call_count == 6:
                # sources already exist
                r.scalar.return_value = 10
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
                await seed_default_data()
        mock_session.commit.assert_called()
        # add should only have been called for tenants/categories, not sources
        assert mock_session.add.call_count <= 4  # system tenant, default tenant, finance cat, tech cat

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
                r.scalar.return_value = 0
            elif call_count == 2:
                r.scalar_one_or_none.return_value = None  # no system tenant
            elif call_count == 3:
                r.scalar_one_or_none.return_value = None  # no default tenant
            elif call_count == 4:
                r.scalar_one_or_none.return_value = None  # no finance cat
            elif call_count == 5:
                r.scalar_one_or_none.return_value = None  # no tech cat
            elif call_count == 6:
                r.scalar.return_value = 0  # no sources
            return r

        with patch("app.db.init_db.async_session_factory", return_value=mock_session):
            with patch.object(mock_session, "execute", new=AsyncMock(side_effect=fake_execute)):
                await seed_default_data()

        # Should have created system tenant, default tenant, 2 categories, and many sources
        mock_session.commit.assert_called()
        # Verify sources were added (finance + tech sources)
        from app.db.init_db import FINANCE_SOURCES, TECH_AI_SOURCES, TECH_ROBOTICS_SOURCES, TECH_EMBEDDED_SOURCES, TECH_SPACE_SOURCES, TECH_CROSS_DOMAIN_SOURCES
        total_tech = len(TECH_AI_SOURCES) + len(TECH_ROBOTICS_SOURCES) + len(TECH_EMBEDDED_SOURCES) + len(TECH_SPACE_SOURCES) + len(TECH_CROSS_DOMAIN_SOURCES)
        expected_sources = len(FINANCE_SOURCES) + total_tech
        # Count Source objects added (excluding tenants and categories)
        from app.models.source import Source, SourceHealth
        source_count = sum(1 for obj in added_objects if isinstance(obj, Source))
        health_count = sum(1 for obj in added_objects if isinstance(obj, SourceHealth))
        assert source_count == expected_sources
        assert health_count == expected_sources
