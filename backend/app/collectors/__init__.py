from app.collectors.finance.alpha_vantage_collector import AlphaVantageCollector
from app.collectors.finance.eastmoney_collector import EastMoneyCollector
from app.collectors.finance.finnhub_collector import FinnhubCollector
from app.collectors.finance.yfinance_collector import YFinanceCollector
from app.collectors.tech.arxiv_collector import ArxivCollector
from app.collectors.tech.hackernews_collector import HackerNewsCollector
from app.collectors.tech.rss_collector import RSSCollector

COLLECTOR_REGISTRY = {
    "yfinance": YFinanceCollector,
    "alpha_vantage": AlphaVantageCollector,
    "eastmoney": EastMoneyCollector,
    "finnhub": FinnhubCollector,
    "rss": RSSCollector,
    "hackernews": HackerNewsCollector,
    "arxiv": ArxivCollector,
}


def get_collector(source_type: str) -> type | None:
    return COLLECTOR_REGISTRY.get(source_type)
