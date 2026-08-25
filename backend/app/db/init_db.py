import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.constants import SYSTEM_TENANT_ID
from app.db.session import async_session_factory
from app.models.base import Base
from app.models.category import Category
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


async def create_tables():
    _engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("All database tables created")
    finally:
        await _engine.dispose()


# Sources with is_active=False are disabled because no collector can run for them
# yet, or the collected data has no consumption chain yet (e.g. 天天基金: the generic
# web_scrape collector exists, but the fund-NAV pipeline is a follow-up feature).
# They are kept as templates for future development. Sources whose collector resolves
# via app.collectors.resolve_collector (source_type match or config.library override)
# are active by default.

FINANCE_SOURCES = [
    {
        "name": "东方财富-A股实时",
        "source_type": "web_scrape",
        "url": "https://push2.eastmoney.com/api/qt/stock/get",
        # EastMoneyCollector is registered as "eastmoney"; the explicit config.library
        # overrides source_type=web_scrape (which would otherwise resolve to the generic
        # WebScrapeCollector) — see app.collectors.resolve_collector.
        "config": {"library": "eastmoney", "data_type": "cn_indices"},
        "refresh_interval_seconds": 15,
        "priority": 1,
        "is_active": True,
    },
    {
        "name": "yfinance-沪深300指数",
        "source_type": "api",
        "url": "https://query1.finance.yahoo.com/v7/finance/chart/000001.SS",
        "config": {
            "library": "yfinance",
            "symbols": ["000001.SS", "399001.SZ", "000300.SS"],
            "history_period": "5d",
            "retry_on_fail": True,
        },
        "refresh_interval_seconds": 30,
        "priority": 2,
        # Resolves via config.library=yfinance
        "is_active": True,
    },
    {
        "name": "yfinance-世界市场指数",
        "source_type": "api",
        "url": "https://query1.finance.yahoo.com/v7/finance/chart/^GSPC",
        "config": {
            "library": "yfinance",
            "symbols": ["^GSPC", "^DJI", "^IXIC", "^HSI", "^N225", "^FTSE", "^GDAXI"],
            "history_period": "5d",
            "retry_on_fail": True,
        },
        "refresh_interval_seconds": 30,
        "priority": 2,
        # Resolves via config.library=yfinance
        "is_active": True,
    },
    {
        "name": "Alpha Vantage-市场指数(failover)",
        "source_type": "api",
        "url": "https://www.alphavantage.co/query",
        "config": {"api_key_env": "ALPHA_VANTAGE_API_KEY", "method": "GET", "function": "TIME_SERIES_INTRADAY"},
        "refresh_interval_seconds": 30,
        "priority": 5,
        # Collector exists but the template is incomplete (no symbols, needs ALPHA_VANTAGE_API_KEY)
        "is_active": False,
    },
    {
        "name": "IEX Cloud-美股行情(可选)",
        "source_type": "api",
        "url": "https://cloud.iexapis.com/stable/stock/AAPL/quote",
        "config": {"library": "iex_cloud", "data_type": "stock_quote", "symbols": ["AAPL", "MSFT", "GOOGL"]},
        "refresh_interval_seconds": 30,
        "priority": 5,
        # Collector is registered (config.library=iex_cloud) but the source is optional
        # and needs IEX_CLOUD_API_KEY; kept disabled as an activation template.
        "is_active": False,
    },
    {
        "name": "yfinance-大宗商品",
        "source_type": "api",
        "url": "https://query1.finance.yahoo.com/v7/finance/chart/GC=F",
        "config": {
            "library": "yfinance",
            "symbols": ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "ZS=F"],
            "history_period": "5d",
        },
        "refresh_interval_seconds": 60,
        "priority": 3,
        # Resolves via config.library=yfinance
        "is_active": True,
    },
    {
        "name": "天天基金-官方NAV",
        "source_type": "web_scrape",
        "url": "https://fund.eastmoney.com/f10/F10DataApi.aspx",
        "config": {"selector": "table", "url_pattern": "fund.eastmoney.com"},
        "refresh_interval_seconds": 86400,
        "priority": 1,
        # The generic web_scrape collector exists now, but the fund-NAV consumption
        # chain (get_fund_nav / display pipeline) is a follow-up feature; activating
        # this source earlier would only collect data nothing consumes.
        "is_active": False,
    },
]

TECH_AI_SOURCES = [
    {
        "name": "MIT Tech Review AI Feed",
        "source_type": "rss",
        "url": "https://www.technologyreview.com/feed/",
        "config": {"parse_rules": {"summary": "excerpt"}},
        "refresh_interval_seconds": 300,
        "priority": 3,
    },
    {
        # Firebase API via HackerNewsCollector (source_type matches COLLECTOR_REGISTRY):
        # unlike the hnrss.org RSS feed it fills extra_data.hn_score, so the tech
        # hot_score HN weighting (services/tech.py) actually receives real scores.
        "name": "HackerNews-AI/ML",
        "source_type": "hackernews",
        "url": "https://hacker-news.firebaseio.com/v0/newstories.json",
        "config": {"story_type": "newstories", "query": "AI machine learning LLM"},
        "refresh_interval_seconds": 120,
        "priority": 2,
    },
    {
        "name": "Arxiv CS.AI",
        "source_type": "rss",
        "url": "https://arxiv.org/rss/cs.AI",
        "config": {"parse_rules": {"summary": "abstract", "extra": {"arxiv_id": "id"}}},
        "refresh_interval_seconds": 1800,
        "priority": 4,
    },
    {
        "name": "OpenAI Blog",
        "source_type": "web_scrape",
        "url": "https://openai.com/blog",
        # Generic WebScrapeCollector (CSS parse_rules); selectors are deliberately
        # permissive — a site redesign yields empty results, never a crash.
        # NOTE (2026-08-25): openai.com blocks plain HTTP clients with a 403
        # (anti-bot), regardless of User-Agent. fetch_data maps 403/429 to a
        # graceful empty result (no collector failure), so the seed stays active
        # as a template that starts producing data once the block is lifted.
        "config": {
            "selector": "article",
            "parse_rules": {
                "item_selector": "article, ul li",
                "title_selector": "h2, h3, h4",
                "link_selector": "a[href]",
                "summary_selector": "p",
                "limit": 20,
            },
        },
        "refresh_interval_seconds": 1800,
        "priority": 3,
        "is_active": True,
    },
    {
        "name": "The Batch (deeplearning.ai)",
        "source_type": "rss",
        "url": "https://deeplearning.ai/the-batch/",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 604800,
        "priority": 5,
    },
]

TECH_ROBOTICS_SOURCES = [
    {
        # Firebase API via HackerNewsCollector; see HackerNews-AI/ML seed for rationale.
        "name": "HackerNews-Robotics",
        "source_type": "hackernews",
        "url": "https://hacker-news.firebaseio.com/v0/newstories.json",
        "config": {"story_type": "newstories", "query": "robot robotics drones"},
        "refresh_interval_seconds": 120,
        "priority": 2,
    },
    {
        "name": "The Robot Report (Google News)",
        "source_type": "rss",
        "url": "https://news.google.com/rss/search?q=robotics+robots+automation&hl=en-US&gl=US&ceid=US:en",
        "config": {"parse_rules": {"summary": "description"}},
        "refresh_interval_seconds": 300,
        "priority": 3,
    },
    {
        "name": "IEEE Robotics",
        "source_type": "rss",
        "url": "https://www.ieee.org/publications/rss_feed.xml",
        "config": {"parse_rules": {"summary": "abstract"}},
        "refresh_interval_seconds": 86400,
        "priority": 5,
    },
    {
        "name": "ROS Blog",
        "source_type": "rss",
        "url": "https://ros.org/blog/rss.xml",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 604800,
        "priority": 6,
    },
    {
        "name": "Automotive News",
        "source_type": "web_scrape",
        "url": "https://www.autonews.com",
        # Generic WebScrapeCollector (CSS parse_rules). A live DOM probe was not
        # possible: autonews.com answers 403 to plain HTTP clients even with a
        # browser User-Agent (anti-bot), so these are deliberately permissive,
        # generic-newsroom selectors (h2/[class*=title] structure assumed).
        # While the site blocks us, fetch_data maps the 403 to a graceful empty
        # result anyway; the rules kick in once the block is lifted.
        "config": {
            "selector": "article",
            "parse_rules": {
                "item_selector": "article, [class*=story], [class*=teaser], h2, h3",
                "title_selector": "h2 a, h3 a, [class*=title] a, a[href]",
                "link_selector": "h2 a, h3 a, [class*=title] a, a[href]",
                "summary_selector": "p.excerpt, p",
                "limit": 20,
            },
        },
        "refresh_interval_seconds": 86400,
        "priority": 5,
        "is_active": True,
    },
]

TECH_EMBEDDED_SOURCES = [
    {
        "name": "Hackaday",
        "source_type": "rss",
        "url": "https://hackaday.com/blog/feed/",
        "config": {"parse_rules": {"summary": "excerpt"}},
        "refresh_interval_seconds": 300,
        "priority": 2,
    },
    {
        "name": "Embedded.com",
        "source_type": "rss",
        "url": "https://www.embedded.com/feed/",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 300,
        "priority": 3,
    },
    {
        "name": "RISC-V International Blog",
        "source_type": "web_scrape",
        "url": "https://riscv.org/blog/",
        # Generic WebScrapeCollector (CSS parse_rules). Selectors tuned against
        # the live DOM (2026-08-25): WordPress/Salient theme, blog grid cards are
        # <div class="... grid-design recent-posts"> containing <a class="... title"
        # href=...>, <p class="... excerpt"> and <span class="publish-date">.
        # Plain fallbacks (article/h2/time) cover a future redesign.
        "config": {
            "selector": "article",
            "parse_rules": {
                "item_selector": "div.recent-posts, article",
                "title_selector": "a.title, h2",
                "link_selector": "a.title, a[href]",
                "summary_selector": "p.excerpt, p",
                "date_selector": "span.publish-date, time",
                "limit": 20,
            },
        },
        "refresh_interval_seconds": 1800,
        "priority": 4,
        "is_active": True,
    },
    {
        "name": "EE Times",
        "source_type": "rss",
        "url": "https://www.eetimes.com/rss/",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 86400,
        "priority": 5,
    },
    {
        "name": "Zephyr Project Blog",
        "source_type": "rss",
        "url": "https://zephyrproject.org/blog/rss",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 2592000,
        "priority": 6,
    },
]

TECH_SPACE_SOURCES = [
    {
        "name": "SpaceNews",
        "source_type": "rss",
        "url": "https://spacenews.com/feed/",
        "config": {"parse_rules": {"summary": "excerpt"}},
        "refresh_interval_seconds": 300,
        "priority": 2,
    },
    {
        "name": "NASA News",
        "source_type": "rss",
        "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "config": {"parse_rules": {"summary": "description"}},
        "refresh_interval_seconds": 1800,
        "priority": 4,
    },
    {
        "name": "SpaceX Updates",
        "source_type": "web_scrape",
        "url": "https://www.spacex.com/updates/",
        # Generic WebScrapeCollector (CSS parse_rules); spacex.com/updates is a
        # JS-heavy timeline, so these selectors are permissive — an SPA-rendered
        # page simply yields an empty result rather than an error.
        # NOTE (2026-08-25): spacex.com blocks plain HTTP clients with a 403
        # (anti-bot), regardless of User-Agent. fetch_data maps 403/429 to a
        # graceful empty result (no collector failure), so the seed stays active
        # as a template that starts producing data once the block is lifted.
        "config": {
            "selector": "article",
            "parse_rules": {
                "item_selector": "article, section[class*=update], div[class*=update]",
                "title_selector": "h1, h2, h3, [class*=title]",
                "link_selector": "a[href]",
                "summary_selector": "p",
                "date_selector": "time, [class*=date]",
                "limit": 20,
            },
        },
        "refresh_interval_seconds": 1800,
        "priority": 3,
        "is_active": True,
    },
    {
        "name": "ESA News",
        "source_type": "rss",
        "url": "https://www.esa.int/RSS",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 1800,
        "priority": 4,
    },
    {
        "name": "Ars Technica Space",
        "source_type": "rss",
        "url": "https://arstechnica.com/science/feed/",
        "config": {"parse_rules": {"summary": "excerpt"}},
        "refresh_interval_seconds": 300,
        "priority": 3,
    },
]

TECH_CROSS_DOMAIN_SOURCES = [
    {
        "name": "Reddit-科技全领域",
        "source_type": "social",
        "url": "https://www.reddit.com/r/artificial+robotics+embedded+space/new.json",
        "config": {"platform": "reddit", "query": "r/artificial+robotics+embedded+space", "parse_rules": {}},
        "refresh_interval_seconds": 600,
        "priority": 4,
        "is_active": False,  # RedditCollector exists (library=reddit); seed subreddits config is a follow-up
    },
    {
        "name": "Google News Tech",
        "source_type": "rss",
        "url": "https://news.google.com/rss/search?q=technology+AI+robotics",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 300,
        "priority": 5,
    },
]


async def seed_default_data():
    async with async_session_factory() as session:
        # Fix: the old logic skipped the whole seed as soon as the tenants table had any
        # row, so an interrupted seed could never be completed. Seeding is now idempotent:
        # there is no global skip; each entity below is "skip if it exists, create if not".
        result = await session.execute(select(Tenant).where(Tenant.slug == "system"))
        system_tenant = result.scalar_one_or_none()

        if system_tenant is None:
            system_tenant = Tenant(
                # Reuse the shared constant instead of scattering hardcoded UUID strings.
                id=SYSTEM_TENANT_ID,
                name="System",
                slug="system",
                plan="enterprise",
                settings={},
                max_users=100,
                max_categories=50,
                max_sources=200,
                is_active=True,
            )
            session.add(system_tenant)
            await session.flush()
            logger.info("System tenant created")
        else:
            logger.info("System tenant already exists")

        result = await session.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug))
        default_tenant = result.scalar_one_or_none()

        if default_tenant is None:
            default_tenant = Tenant(
                name=settings.default_tenant_name,
                slug=settings.default_tenant_slug,
                plan="free",
                settings={},
            )
            session.add(default_tenant)
            await session.flush()
            logger.info("Default tenant created")
        else:
            logger.info("Default tenant already exists")

        result = await session.execute(
            select(Category).where(
                Category.tenant_id == system_tenant.id,
                Category.slug == "finance",
            )
        )
        finance_category = result.scalar_one_or_none()

        if finance_category is None:
            finance_category = Category(
                tenant_id=system_tenant.id,
                name="财经",
                slug="finance",
                description="金融市场行情数据",
                icon="chart-line",
                color="#FF6B6B",
                type="finance",
                refresh_interval_seconds=30,
                keywords_filter=["股票", "基金", "行情", "指数", "A股", "期货", "大宗商品"],
                is_active=True,
            )
            session.add(finance_category)
            await session.flush()
            logger.info("Finance category created")
        else:
            logger.info("Finance category already exists")

        result = await session.execute(
            select(Category).where(
                Category.tenant_id == system_tenant.id,
                Category.slug == "tech",
            )
        )
        tech_category = result.scalar_one_or_none()

        if tech_category is None:
            tech_category = Category(
                tenant_id=system_tenant.id,
                name="科技",
                slug="tech",
                description="科技领域新闻资讯：AI、机器人、嵌入式、太空",
                icon="cpu",
                color="#3B82F6",
                type="tech",
                refresh_interval_seconds=300,
                keywords_filter=["AI", "机器人", "嵌入式", "太空", "RISC-V", "FPGA", "LLM"],
                is_active=True,
            )
            session.add(tech_category)
            await session.flush()
            logger.info("Tech category created")
        else:
            logger.info("Tech category already exists")

        # Fix: the old logic created all sources only when the system tenant had zero of
        # them, so a partially seeded state was never completed. Now each source is checked
        # by name and created only if missing (idempotent backfill).
        existing_names_result = await session.execute(select(Source.name).where(Source.tenant_id == system_tenant.id))
        existing_source_names = {name for (name,) in existing_names_result.all()}

        tech_sources_defs = (
            TECH_AI_SOURCES
            + TECH_ROBOTICS_SOURCES
            + TECH_EMBEDDED_SOURCES
            + TECH_SPACE_SOURCES
            + TECH_CROSS_DOMAIN_SOURCES
        )
        seed_source_defs = [(src, finance_category.id) for src in FINANCE_SOURCES] + [
            (src, tech_category.id) for src in tech_sources_defs
        ]

        missing_sources = []
        for src_data, category_id in seed_source_defs:
            if src_data["name"] in existing_source_names:
                continue
            missing_sources.append(
                Source(
                    tenant_id=system_tenant.id,
                    category_id=category_id,
                    name=src_data["name"],
                    source_type=src_data["source_type"],
                    url=src_data["url"],
                    config=src_data["config"],
                    refresh_interval_seconds=src_data["refresh_interval_seconds"],
                    is_active=src_data.get("is_active", True),
                    priority=src_data["priority"],
                )
            )

        if missing_sources:
            for source in missing_sources:
                session.add(source)
            await session.flush()

            for source in missing_sources:
                health = SourceHealth(
                    source_id=source.id,
                    status="healthy",
                    total_fetches_24h=0,
                    success_count_24h=0,
                    avg_response_time_ms=0,
                    consecutive_failures=0,
                )
                session.add(health)

            await session.flush()
            logger.info(f"Seeded {len(missing_sources)} missing data sources for system tenant")
        else:
            logger.info("All seed sources already exist, skipping source seed")

        await session.commit()
        logger.info("Default data seeded successfully")


async def init_db():
    await create_tables()
    await seed_default_data()
