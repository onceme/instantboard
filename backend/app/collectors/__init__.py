from app.collectors.finance.alpha_vantage_collector import AlphaVantageCollector
from app.collectors.finance.eastmoney_collector import EastMoneyCollector
from app.collectors.finance.finnhub_collector import FinnhubCollector
from app.collectors.finance.fund_nav_collector import TiantianFundCollector
from app.collectors.finance.iex_cloud_collector import IEXCloudCollector
from app.collectors.finance.yfinance_collector import YFinanceCollector
from app.collectors.tech.arxiv_collector import ArxivCollector
from app.collectors.tech.hackernews_collector import HackerNewsCollector
from app.collectors.tech.reddit_collector import RedditCollector
from app.collectors.tech.rss_collector import RSSCollector
from app.collectors.tech.twitter_collector import TwitterCollector
from app.collectors.tech.web_scrape_collector import WebScrapeCollector

COLLECTOR_REGISTRY = {
    "yfinance": YFinanceCollector,
    "alpha_vantage": AlphaVantageCollector,
    "eastmoney": EastMoneyCollector,
    "finnhub": FinnhubCollector,
    "iex_cloud": IEXCloudCollector,
    # Official Chinese fund NAV (finance-tab.md §3.3); resolved via
    # config.library=tiantian_fund (bare api/web_scrape source_types do not
    # match it), same override pattern as eastmoney/yfinance/reddit.
    "tiantian_fund": TiantianFundCollector,
    "rss": RSSCollector,
    "hackernews": HackerNewsCollector,
    "arxiv": ArxivCollector,
    "reddit": RedditCollector,
    "twitter": TwitterCollector,
    "web_scrape": WebScrapeCollector,
}


def get_collector(source_type: str) -> type | None:
    return COLLECTOR_REGISTRY.get(source_type)


def resolve_collector(source_type: str, config: dict | None = None) -> type | None:
    """Resolve the collector class for a source.

    An explicit config.library always wins when it names a registered collector:
    template-style sources use it to override their source_type (e.g.
    source_type=web_scrape + library=eastmoney → EastMoneyCollector, not the
    generic WebScrapeCollector). Otherwise the source_type itself selects the
    collector (rss / hackernews / arxiv / web_scrape). Returns None when nothing
    matches, i.e. the source cannot be collected yet (e.g. api/social sources
    without a library).
    """
    library = str((config or {}).get("library", "") or "")
    if library:
        collector_cls = COLLECTOR_REGISTRY.get(library)
        if collector_cls is not None:
            return collector_cls
    return get_collector(source_type)
