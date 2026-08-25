import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

_IGNORED_LINK_SCHEMES = ("javascript:", "mailto:", "tel:")


class WebScrapeCollector(BaseCollector):
    """Generic HTML scraper for web_scrape sources.

    Parsing is driven entirely by CSS selectors in config.parse_rules:
      item_selector    container element for each item (optional; when absent,
                       every title_selector match is treated as one item)
      title_selector   title element inside the container (falls back to the
                       first h1-h4 heading)
      link_selector    element whose href is the item URL (optional; falls back
                       to the container's own href or its first <a href>)
      summary_selector optional summary element
      date_selector    optional date element (<time datetime> attribute wins over
                       text); parsed with dateutil, current time on failure
      limit            max items (default 20, capped at 100)

    Items missing a title or a URL are skipped and relative links are resolved
    against source.url. The selectors are intentionally forgiving: a site
    redesign yields an empty result rather than a crash. 403/429 responses are
    treated as "nothing this cycle" (empty success), everything else goes
    through the BaseCollector retry/failure path.
    """

    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 5

    DEFAULT_USER_AGENT = "instantboard-collector/1.0"
    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100
    SUMMARY_MAX_CHARS = 300
    HEADING_TAGS = ("h1", "h2", "h3", "h4")

    async def fetch_data(self, source: Any) -> Any:
        url = str(getattr(source, "url", "") or "")
        if not url:
            logger.warning(f"No URL configured for source {getattr(source, 'name', 'unknown')}")
            return None

        config = getattr(source, "config", {}) or {}
        user_agent = str(config.get("user_agent") or self.DEFAULT_USER_AGENT)
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=headers)
            except httpx.TimeoutException:
                logger.warning(f"Web scrape timeout for {url}")
                raise
            except httpx.HTTPError as e:
                logger.warning(f"Web scrape HTTP error for {url}: {e}")
                raise

            if response.status_code in (403, 429):
                logger.warning(
                    f"Web scrape blocked/rate limited ({response.status_code}) for {url}; returning empty result"
                )
                return []
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} for {url}")
            return response.text

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data or not isinstance(raw_data, str):
            return []

        soup = self._build_soup(raw_data, source)
        if soup is None:
            return []

        config = getattr(source, "config", {}) or {}
        rules = config.get("parse_rules") or {}
        if not isinstance(rules, dict):
            rules = {}

        item_selector = str(rules.get("item_selector") or "").strip()
        title_selector = str(rules.get("title_selector") or "").strip()
        link_selector = str(rules.get("link_selector") or "").strip()
        summary_selector = str(rules.get("summary_selector") or "").strip()
        date_selector = str(rules.get("date_selector") or "").strip()

        try:
            limit = int(rules.get("limit") or self.DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        limit = max(1, min(limit, self.MAX_LIMIT))

        if item_selector:
            containers = soup.select(item_selector)
            containers_are_titles = False
        elif title_selector:
            containers = soup.select(title_selector)
            containers_are_titles = True
        else:
            logger.info(f"No item_selector/title_selector in parse_rules for {getattr(source, 'name', 'unknown')}")
            return []

        base_url = str(getattr(source, "url", "") or "")
        scraped_at = datetime.now(UTC).isoformat()

        items: list[dict] = []
        for container in containers:
            if len(items) >= limit:
                break

            if containers_are_titles:
                title = self._clean_text(container)
            else:
                title = self._extract_title(container, title_selector)
            if not title:
                continue

            url = self._extract_url(container, link_selector, base_url)
            if not url:
                continue

            summary = ""
            if summary_selector:
                summary_node = container.select_one(summary_selector)
                if summary_node is not None:
                    summary = self._clean_text(summary_node)[: self.SUMMARY_MAX_CHARS]

            items.append(
                {
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "published_at": self._extract_date(container, date_selector),
                    "extra_data": {"scraped_url": base_url, "scraped_at": scraped_at},
                }
            )

        return items

    def _build_soup(self, html: str, source: Any) -> BeautifulSoup | None:
        name = getattr(source, "name", "unknown")
        try:
            return BeautifulSoup(html, "lxml")
        except Exception as e:
            logger.info(f"lxml parser failed for {name} ({e}); falling back to html.parser")
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.warning(f"Failed to parse HTML for {name}: {e}")
            return None

    def _extract_title(self, container: Any, title_selector: str) -> str:
        node = container.select_one(title_selector) if title_selector else None
        if node is None:
            for tag in self.HEADING_TAGS:
                node = container.find(tag)
                if node is not None:
                    break
        if node is None:
            return ""
        return self._clean_text(node)

    def _extract_url(self, container: Any, link_selector: str, base_url: str) -> str:
        node = container.select_one(link_selector) if link_selector else None
        if node is None and container.name == "a" and container.get("href"):
            node = container
        if node is None:
            node = container.find("a", href=True)

        href = str(node.get("href") or "").strip() if node is not None else ""
        if not href or href == "#" or href.lower().startswith(_IGNORED_LINK_SCHEMES):
            return ""
        return urljoin(base_url, href)

    def _extract_date(self, container: Any, date_selector: str) -> str:
        raw_date = ""
        if date_selector:
            node = container.select_one(date_selector)
            if node is not None:
                raw_date = str(node.get("datetime") or "").strip() or self._clean_text(node)
        if not raw_date:
            return datetime.now(UTC).isoformat()
        try:
            return date_parser.parse(raw_date).isoformat()
        except (ValueError, OverflowError, TypeError):
            logger.debug(f"Unparseable scraped date {raw_date!r}; falling back to current time")
            return datetime.now(UTC).isoformat()

    @staticmethod
    def _clean_text(node: Any) -> str:
        return " ".join(node.get_text(" ", strip=True).split())
