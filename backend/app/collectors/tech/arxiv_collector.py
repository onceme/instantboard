import logging
from typing import Any
from xml.etree import ElementTree

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class ArxivCollector(BaseCollector):
    timeout_seconds = 15
    max_retries = 3
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 10

    BASE_URL = "http://export.arxiv.org/api/query"
    ARXIV_CATEGORIES = [
        "cs.AI",
        "cs.RO",
        "cs.EM",
        "cs.LG",
        "cs.CL",
        "cs.CV",
        "cs.NE",
        "cs.AR",
        "cs.SE",
    ]

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        categories = config.get("categories", ["cs.AI"])
        max_results = config.get("max_results", 20)

        search_query = " OR ".join([f"cat:{cat}" for cat in categories])
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(self.BASE_URL, params=params)
                if response.status_code != 200:
                    logger.warning(f"Arxiv API returned {response.status_code}")
                    return None
                return response.text
            except httpx.TimeoutException:
                logger.warning("Arxiv API timeout")
                return None
            except httpx.HTTPError as e:
                logger.warning(f"Arxiv API HTTP error: {e}")
                return None

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data:
            return []

        try:
            root = ElementTree.fromstring(raw_data)
        except ElementTree.ParseError as e:
            logger.warning(f"Arxiv XML parse error: {e}")
            return []

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        items = []
        for entry in root.findall("atom:entry", ns):
            title_elem = entry.find("atom:title", ns)
            title = title_elem.text.strip() if title_elem is not None else ""

            summary_elem = entry.find("atom:summary", ns)
            summary = summary_elem.text.strip() if summary_elem is not None else ""

            id_elem = entry.find("atom:id", ns)
            arxiv_url = id_elem.text.strip() if id_elem is not None else ""
            pdf_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    break

            published_elem = entry.find("atom:published", ns)
            published_at = ""
            if published_elem is not None:
                published_at = published_elem.text.strip()

            updated_elem = entry.find("atom:updated", ns)
            updated_at = ""
            if updated_elem is not None:
                updated_at = updated_elem.text.strip()

            authors = []
            for author in entry.findall("atom:author", ns):
                name_elem = author.find("atom:name", ns)
                if name_elem is not None:
                    authors.append(name_elem.text.strip())

            categories = []
            for cat in entry.findall("atom:category", ns):
                term = cat.get("term", "")
                if term:
                    categories.append(term)

            primary_category = ""
            primary_cat_elem = entry.find("arxiv:primary_category", ns)
            if primary_cat_elem is not None:
                primary_category = primary_cat_elem.get("term", "")

            comment_elem = entry.find("arxiv:comment", ns)
            comments = comment_elem.text.strip() if comment_elem is not None else ""

            item = {
                "title": title,
                "url": arxiv_url,
                "summary": summary[:500] if summary else "",
                "published_at": published_at,
                "author": ", ".join(authors),
                "image_url": "",
                "extra_data": {
                    "arxiv_id": arxiv_url.split("/")[-1] if arxiv_url else "",
                    "pdf_url": pdf_url,
                    "authors": authors,
                    "categories": categories,
                    "primary_category": primary_category,
                    "updated_at": updated_at,
                    "comments": comments,
                },
            }

            items.append(item)

        return items
