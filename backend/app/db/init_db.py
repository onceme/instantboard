import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.constants import SYSTEM_TENANT_ID
from app.db.partitions import ensure_quote_partitions
from app.db.session import apply_service_context, async_session_factory
from app.models.base import Base
from app.models.category import Category
from app.models.finance import BINDING_SOURCE_SEED, FinanceSymbol, FundIndexBinding
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# Fund → tracking index bindings for mainstream broad-based ETFs and feeder
# (联接) funds (fund-intraday-nav.md §3.3). Fixes the underlying_index_symbol
# dead-end: without this table no code path ever wrote a binding, so the
# index-tracking estimate branch never engaged for funds added to a watchlist.
# Pure backfill semantics: existing bindings are never overwritten (the seed
# skips known fund codes), so later admin maintenance wins over re-seeding.
# index_symbol uses finance_symbols style (Yahoo suffix) because the estimate
# path fetches index quotes through the regular quote failover chain.
FUND_INDEX_BINDINGS = [
    # ETFs (交易所交易基金)
    ("510300", "000300.SS"),  # 华泰柏瑞沪深300ETF → 沪深300
    ("510500", "000905.SS"),  # 南方中证500ETF → 中证500
    ("510050", "000016.SS"),  # 华夏上证50ETF → 上证50
    ("159915", "399006.SZ"),  # 易方达创业板ETF → 创业板指
    ("512880", "000991.SS"),  # 国泰中证全指证券公司ETF → 证券公司
    ("588000", "000688.SS"),  # 华夏上证科创板50ETF → 科创50
    ("512100", "000852.SS"),  # 南方中证1000ETF → 中证1000
    ("510880", "000015.SS"),  # 华泰柏瑞红利ETF → 上证红利
    ("159901", "399330.SZ"),  # 易方达深证100ETF → 深证100
    ("512010", "000933.SS"),  # 汇添富/易方达医药ETF → 中证医药
    ("159928", "000932.SS"),  # 汇添富中证主要消费ETF → 中证主要消费
    ("512660", "399967.SZ"),  # 国泰中证军工ETF → 中证军工
    ("512980", "399971.SZ"),  # 广发中证传媒ETF → 中证传媒
    # Feeder funds (场外联接基金)
    ("110020", "000300.SS"),  # 易方达沪深300ETF联接 → 沪深300
    ("160706", "000300.SS"),  # 嘉实沪深300ETF联接 → 沪深300
    ("000051", "000300.SS"),  # 华夏沪深300ETF联接 → 沪深300
    ("000961", "000300.SS"),  # 天弘沪深300指数 → 沪深300
    ("160119", "000905.SS"),  # 南方中证500ETF联接 → 中证500
    ("000962", "000905.SS"),  # 天弘中证500指数 → 中证500
    ("001548", "000016.SS"),  # 天弘上证50指数 → 上证50
    ("005918", "399006.SZ"),  # 天弘创业板指数 → 创业板指
    ("003765", "000933.SS"),  # 天弘中证医药100 → 中证医药
]

# Fund symbol seeds for the system tenant (fund-intraday-nav.md, M2 phase A).
# Production/staging had zero type='fund' finance_symbols rows — external search
# maps CN listed funds to 'stock' and misses OTC open-end funds entirely, so no
# tenant could add a fund to a watchlist and the intraday pipeline had nothing
# to consume. Listed ETFs/LOFs carry the exchange suffix (search/Yahoo spelling,
# `_normalize_fund_code` reduces them to the 6-digit code used by the bindings
# table); OTC open-end funds use bare codes. Coverage is a superset of
# FUND_INDEX_BINDINGS plus a few popular active funds.
FUND_SYMBOL_SEEDS = [
    # 场内 ETF / LOF（带交易所后缀，与搜索返回拼写一致）
    ("510300.SS", "华泰柏瑞沪深300ETF"),
    ("510500.SS", "南方中证500ETF"),
    ("510050.SS", "华夏上证50ETF"),
    ("159915.SZ", "易方达创业板ETF"),
    ("512880.SS", "国泰中证全指证券公司ETF"),
    ("588000.SS", "华夏上证科创板50ETF"),
    ("512100.SS", "南方中证1000ETF"),
    ("510880.SS", "华泰柏瑞红利ETF"),
    ("159901.SZ", "易方达深证100ETF"),
    ("512010.SS", "医药ETF"),
    ("159928.SZ", "汇添富中证主要消费ETF"),
    ("512660.SS", "国泰中证军工ETF"),
    ("512980.SS", "广发中证传媒ETF"),
    ("160706.SZ", "嘉实沪深300ETF联接(LOF)A"),
    ("160119.SZ", "南方中证500ETF联接(LOF)A"),
    # 场外开放式基金（6 位裸代码，无交易所后缀）
    ("110020", "易方达沪深300ETF联接A"),
    ("000051", "华夏沪深300ETF联接A"),
    ("000961", "天弘沪深300ETF联接A"),
    ("000962", "天弘中证500指数A"),
    ("001548", "天弘上证50指数A"),
    ("005918", "天弘创业板指数A"),
    ("003765", "天弘中证医药100A"),
    ("110011", "易方达优质精选混合(QDII)"),
    ("161725", "招商中证白酒指数(LOF)A"),
    ("005827", "易方达蓝筹精选混合"),
    ("320007", "诺安成长混合"),
    ("260108", "景顺长城新兴成长混合"),
]


async def seed_fund_symbols(session, system_tenant_id) -> tuple[int, int]:
    """Idempotent fund-symbol backfill with type reconciliation
    (fund-intraday-nav.md M2 phase A + Task C).

    Backfills system-tenant ``type='fund'`` rows for every code in
    FUND_SYMBOL_SEEDS, keyed by the normalized 6-digit code so a pre-existing
    row for the same code is recognized regardless of its stored spelling
    (bare ``510300`` ↔ suffixed ``510300.SS``). When such a row exists but with
    a different type — the historical contamination where a fund code was
    mis-registered as ``'stock'`` — this reconciles the type to ``'fund'`` so
    the fund pipeline recognizes it. Type reconciliation only ever moves a row
    toward ``'fund'`` for a seed code; it never reverses a fund back to stock
    and never touches non-seed symbols.

    Returns (seeded_count, reconciled_count).
    """
    from app.services.fund_holdings import _normalize_fund_code_shared

    existing_result = await session.execute(select(FinanceSymbol).where(FinanceSymbol.tenant_id == system_tenant_id))
    existing_by_norm_code: dict[str, FinanceSymbol] = {}
    for sym in existing_result.scalars().all():
        norm = _normalize_fund_code_shared(str(sym.symbol or ""))
        if norm:
            existing_by_norm_code.setdefault(norm, sym)

    missing: list[FinanceSymbol] = []
    reconciled = 0
    for fund_symbol, fund_name in FUND_SYMBOL_SEEDS:
        norm = _normalize_fund_code_shared(fund_symbol)
        existing = existing_by_norm_code.get(norm) if norm else None
        if existing is None:
            missing.append(
                FinanceSymbol(
                    tenant_id=system_tenant_id,
                    symbol=fund_symbol,
                    name=fund_name,
                    type="fund",
                    market="CN",
                    exchange="",
                    currency="CNY",
                    is_active=True,
                )
            )
        elif existing.type != "fund":
            # Task C: correct a mis-registered type (e.g. a fund code stored as
            # 'stock'). Preserve the existing name/market/currency; only fix type.
            existing.type = "fund"
            reconciled += 1

    if missing:
        for row in missing:
            session.add(row)
        await session.flush()
        logger.info(f"Seeded {len(missing)} fund symbols for system tenant")
    else:
        logger.info("All seed fund symbols already exist, skipping fund symbol seed")
    if reconciled:
        logger.info(f"Reconciled {reconciled} pre-existing fund-code symbol(s) to type='fund'")
    return len(missing), reconciled


async def create_tables():
    _engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("All database tables created")
        # finance_quotes is a partitioned parent on PostgreSQL; inserts fail
        # until month partitions exist, so supply them right after table
        # creation. No-op on SQLite and on plain pre-partitioning tables
        # (database.md §3.1) — the entrypoint.sh create-tables fallback, the
        # main.py lifespan and the worker startup all go through here.
        await ensure_quote_partitions(_engine)
    finally:
        await _engine.dispose()


# Sources with is_active=False are disabled because no collector can run for them
# yet, or the collected data has no consumption chain yet. They are kept as
# templates for future development. Sources whose collector resolves via
# app.collectors.resolve_collector (source_type match or config.library override)
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
        # Dedicated fund-NAV source (finance-tab.md §3.3): TiantianFundCollector
        # is registered as "tiantian_fund"; the explicit config.library overrides
        # source_type=api (bare "api" resolves to nothing) — see
        # app.collectors.resolve_collector. The collector fetches the EastMoney
        # f10 historical-NAV JSON API (https://api.fund.eastmoney.com/f10/lsjz),
        # which requires the fundf10.eastmoney.com Referer header the collector
        # always sends. fund_codes below are a reasonable default list for the
        # periodic collect_ items pipeline (its rows are FilterProcessor-dropped
        # like the other finance seeds); the daily official-NAV refresh
        # (FinanceService.update_official_nav, cron 20:00 Asia/Shanghai) derives
        # its codes from fund-type finance_symbols instead.
        "name": "天天基金-官方NAV",
        "source_type": "api",
        "url": "https://api.fund.eastmoney.com/f10/lsjz",
        "config": {
            "library": "tiantian_fund",
            "fund_codes": ["110011", "161725", "005827", "320007", "260108"],
        },
        "refresh_interval_seconds": 86400,
        "priority": 1,
        "is_active": True,
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
        # ieee.org sits behind a JS-challenge WAF that plain HTTP clients can
        # never pass (permanent 202/empty); IEEE Spectrum's official RSS is the
        # reachable successor (verified 2026-08-31 with the collector UA).
        "name": "IEEE Spectrum",
        "source_type": "rss",
        "url": "https://spectrum.ieee.org/feeds/feed.rss",
        "config": {"parse_rules": {}},
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
        # The old /rss/ endpoint is bot-blocked (403 + connection reset); the
        # WordPress feed is the official reachable successor (verified
        # 2026-08-31 with the collector UA).
        "name": "EE Times",
        "source_type": "rss",
        "url": "https://www.eetimes.com/feed/",
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
        # The legacy /RSS endpoint returns 403; rssfeed/TopNews is ESA's
        # official reachable successor (verified 2026-08-31 with the
        # collector UA).
        "name": "ESA News",
        "source_type": "rss",
        "url": "https://www.esa.int/rssfeed/TopNews",
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
        # RedditCollector is registered as "reddit"; the explicit config.library
        # overrides source_type=social (bare "social" resolves to nothing in
        # COLLECTOR_REGISTRY). The collector ignores source.url: it fetches each
        # configured subreddit individually via the public JSON endpoint
        # https://www.reddit.com/r/{subreddit}/new.json and aggregates the posts
        # with cross-subreddit de-duplication by post id; the url here merely
        # documents the covered subreddits.
        "name": "Reddit-科技全领域",
        "source_type": "social",
        "url": "https://www.reddit.com/r/artificial+robotics+embedded+space/new.json",
        "config": {
            "library": "reddit",
            "subreddits": ["artificial", "robotics", "embedded", "space"],
        },
        "refresh_interval_seconds": 600,
        "priority": 4,
        "is_active": True,
    },
    {
        "name": "Google News Tech",
        "source_type": "rss",
        "url": "https://news.google.com/rss/search?q=technology+AI+robotics",
        "config": {"parse_rules": {}},
        "refresh_interval_seconds": 300,
        "priority": 5,
    },
    {
        "name": "Twitter/X-科技话题",
        "source_type": "social",
        "url": "https://api.twitter.com/2/tweets/search/recent",
        "config": {"library": "twitter", "query": "AI OR robotics", "max_results": 20},
        "refresh_interval_seconds": 600,
        "priority": 4,
        # Collector is registered (config.library=twitter) but the source needs
        # TWITTER_BEARER_TOKEN, and the Twitter API v2 recent-search endpoint is only
        # available on paid tiers with strict request quotas; kept disabled as an
        # activation template.
        "is_active": False,
    },
]


async def seed_default_data():
    # Seeding runs cross-tenant (creates the tenants themselves plus the
    # system-tenant categories/sources), so it needs the RLS service bypass:
    # INSERT ... WITH CHECK would reject system-tenant rows for any
    # request-style tenant context.
    async with async_session_factory() as session:
        await apply_service_context(session)
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

        # Fund → tracking index bindings (fund-intraday-nav.md §3.3). Same
        # idempotent backfill shape as the source seed above: existing rows win
        # (checked by fund_code), so admin-maintained bindings survive re-runs.
        existing_bindings_result = await session.execute(select(FundIndexBinding.fund_code))
        existing_binding_codes = {code for (code,) in existing_bindings_result.all()}
        missing_bindings = [
            FundIndexBinding(
                tenant_id=system_tenant.id,
                fund_code=fund_code,
                index_symbol=index_symbol,
                source=BINDING_SOURCE_SEED,
            )
            for fund_code, index_symbol in FUND_INDEX_BINDINGS
            if fund_code not in existing_binding_codes
        ]
        if missing_bindings:
            for binding in missing_bindings:
                session.add(binding)
            await session.flush()
            logger.info(f"Seeded {len(missing_bindings)} fund index bindings for system tenant")
        else:
            logger.info("All seed fund index bindings already exist, skipping binding seed")

        # Fund symbols (fund-intraday-nav.md M2 phase A): system-tenant type='fund'
        # rows — idempotent backfill keyed by the normalized 6-digit code, matching
        # the source/binding seed shape above. Feeds the daily official-NAV job and
        # the intraday pipeline; other tenants get their own copies via search
        # auto-registration. Also reconciles a pre-existing row for a seed fund
        # code that carries the wrong type (see seed_fund_symbols).
        await seed_fund_symbols(session, system_tenant.id)

        await session.commit()
        logger.info("Default data seeded successfully")


async def init_db():
    await create_tables()
    await seed_default_data()
