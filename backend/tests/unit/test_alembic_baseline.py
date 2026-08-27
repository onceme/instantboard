"""Lightweight checks of the Alembic migration setup (database.md §3.4).

No database access: verifies the on-disk migration scaffolding — the
versions/ directory and a single-head chain, the baseline revision covering
all 12 model tables (including the autogenerate blind spots the baseline
fixes: finance_quotes partition option, CHECK constraints, JSONB server
defaults, GIN and partial indexes), the downgrade path and the env.py
target-metadata wiring.
"""

import inspect
import re
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = BACKEND_DIR / "app" / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"

EXPECTED_BASELINE_TABLES = (
    "tenants",
    "users",
    "categories",
    "sources",
    "source_health",
    "items",
    "finance_symbols",
    "fund_nav_estimates",
    "watchlist_items",
    "finance_quotes",
    "dashboard_snapshots",
    "sse_connections",
)

EXPECTED_CHECK_CONSTRAINTS = (
    "chk_tenants_plan",
    "chk_users_sso_provider",
    "chk_users_role",
    "chk_categories_type",
    "chk_sources_type",
    "chk_source_health_status",
    "chk_finance_symbols_type",
)


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(ALEMBIC_DIR / "alembic.ini"))
    # Absolute path so the test works regardless of the pytest cwd.
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return cfg


@pytest.fixture(scope="module")
def script_dir() -> ScriptDirectory:
    return ScriptDirectory.from_config(_alembic_config())


@pytest.fixture(scope="module")
def baseline_module(script_dir: ScriptDirectory):
    roots = [rev for rev in script_dir.walk_revisions() if rev.down_revision is None]
    assert len(roots) == 1, f"expected exactly one baseline (root) revision, got {roots}"
    return roots[0].module


@pytest.fixture(scope="module")
def baseline_upgrade_source(baseline_module) -> str:
    return inspect.getsource(baseline_module.upgrade)


@pytest.fixture(scope="module")
def baseline_downgrade_source(baseline_module) -> str:
    return inspect.getsource(baseline_module.downgrade)


def test_versions_directory_has_revision_scripts(script_dir: ScriptDirectory) -> None:
    assert VERSIONS_DIR.is_dir(), "app/alembic/versions/ must exist"
    revisions = [p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py"]
    assert revisions, "versions/ must contain at least one migration script"
    assert script_dir.get_heads(), "migration chain must have at least one head"


def test_migration_chain_has_single_head(script_dir: ScriptDirectory) -> None:
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected a single migration head, got {heads}"


def test_ini_script_location_points_at_app_alembic() -> None:
    cfg = AlembicConfig(str(ALEMBIC_DIR / "alembic.ini"))
    location = cfg.get_main_option("script_location")
    assert location.replace("\\", "/").rstrip("/").endswith("app/alembic")


def test_env_imports_all_models_and_targets_base_metadata() -> None:
    env_source = (ALEMBIC_DIR / "env.py").read_text()
    # Base.metadata only holds tables once the model modules are imported.
    assert re.search(r"^import app\.models", env_source, re.MULTILINE)
    assert "from app.models.base import Base" in env_source
    assert "target_metadata = Base.metadata" in env_source
    # Async engine wiring for asyncpg (SQLAlchemy 2.0 convention).
    assert "async_engine_from_config" in env_source
    assert "run_sync" in env_source


def test_baseline_creates_all_model_tables(baseline_upgrade_source: str) -> None:
    for table in EXPECTED_BASELINE_TABLES:
        assert re.search(rf"op\.create_table\(\s*['\"]{table}['\"]", baseline_upgrade_source), (
            f"baseline migration misses table {table}"
        )


def test_baseline_finance_quotes_is_partitioned(baseline_upgrade_source: str) -> None:
    # autogenerate blind spot #1: the postgresql_partition_by dialect option
    # and the composite PK (id, timestamp) PG requires for partitioned tables.
    assert re.search(r"postgresql_partition_by\s*=\s*['\"]RANGE \(timestamp\)['\"]", baseline_upgrade_source)
    assert re.search(r"PrimaryKeyConstraint\(\s*['\"]id['\"]\s*,\s*['\"]timestamp['\"]", baseline_upgrade_source)


def test_baseline_named_check_constraints(baseline_upgrade_source: str) -> None:
    for name in EXPECTED_CHECK_CONSTRAINTS:
        assert name in baseline_upgrade_source, f"baseline migration misses {name}"


def test_baseline_jsonb_server_defaults(baseline_upgrade_source: str) -> None:
    assert baseline_upgrade_source.count("::jsonb") >= 6


def test_baseline_partial_index_on_sse_connections(baseline_upgrade_source: str) -> None:
    assert "idx_sse_connections_active" in baseline_upgrade_source
    assert re.search(r"postgresql_where\s*=", baseline_upgrade_source)


def test_baseline_gin_indexes(baseline_upgrade_source: str) -> None:
    for index in ("idx_items_tags", "idx_items_extra_data"):
        assert re.search(
            rf"['\"]{index}['\"].*?postgresql_using\s*=\s*['\"]gin['\"]",
            baseline_upgrade_source,
            re.DOTALL,
        ), f"baseline migration misses GIN index {index}"


def test_baseline_downgrade_drops_all_tables(baseline_downgrade_source: str) -> None:
    for table in EXPECTED_BASELINE_TABLES:
        assert re.search(rf"op\.drop_table\(\s*['\"]{table}['\"]", baseline_downgrade_source), (
            f"baseline downgrade misses drop of {table}"
        )
