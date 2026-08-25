from app.collectors.finance.alpha_vantage_collector import AlphaVantageCollector
from app.collectors.finance.eastmoney_collector import EastMoneyCollector
from app.collectors.finance.finnhub_collector import FinnhubCollector
from app.collectors.finance.iex_cloud_collector import IEXCloudCollector
from app.collectors.finance.yfinance_collector import YFinanceCollector
from app.collectors.tech.arxiv_collector import ArxivCollector
from app.collectors.tech.hackernews_collector import HackerNewsCollector
from app.collectors.tech.reddit_collector import RedditCollector
from app.collectors.tech.rss_collector import RSSCollector

COLLECTOR_REGISTRY = {
    "yfinance": YFinanceCollector,
    "alpha_vantage": AlphaVantageCollector,
    "eastmoney": EastMoneyCollector,
    "finnhub": FinnhubCollector,
    "iex_cloud": IEXCloudCollector,
    "rss": RSSCollector,
    "hackernews": HackerNewsCollector,
    "arxiv": ArxivCollector,
    "reddit": RedditCollector,
}


def get_collector(source_type: str) -> type | None:
    return COLLECTOR_REGISTRY.get(source_type)


def resolve_collector(source_type: str, config: dict | None = None) -> type | None:
    """Resolve the collector class for a source.

    Primary lookup is by source_type (rss -> RSSCollector, ...). Template-style
    sources whose source_type has no collector of its own (e.g. source_type=api,
    web_scrape or social) can name one explicitly via config.library (yfinance /
    eastmoney / alpha_vantage / finnhub / iex_cloud / reddit / ...). Returns None
    when nothing matches, i.e. the source cannot be collected yet.
    """
    collector_cls = get_collector(source_type)
    if collector_cls is not None:
        return collector_cls
    library = str((config or {}).get("library", "") or "")
    if not library:
        return None
    return COLLECTOR_REGISTRY.get(library)
