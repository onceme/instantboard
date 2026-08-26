import logging
import math
import re
from collections import Counter, deque
from typing import Any

from app.processors.base import BaseProcessor

logger = logging.getLogger(__name__)

KEYWORD_TO_TAG: dict[str, list[str]] = {
    "GPT": ["ai", "llm"],
    "GPT-4": ["ai", "llm"],
    "GPT-4o": ["ai", "llm"],
    "Claude": ["ai", "llm"],
    "Llama": ["ai", "llm"],
    "Llama-3": ["ai", "llm"],
    "Gemini": ["ai", "llm", "multimodal"],
    "Sora": ["ai", "generative-ai", "llm"],
    "transformer": ["ai", "llm"],
    "deep learning": ["ai", "llm"],
    "neural network": ["ai", "llm"],
    "LLM": ["ai", "llm"],
    "large language model": ["ai", "llm"],
    "machine learning": ["ai"],
    "ML": ["ai"],
    "artificial intelligence": ["ai"],
    "AI": ["ai"],
    "DALL-E": ["ai", "generative-ai"],
    "Midjourney": ["ai", "generative-ai"],
    "Stable Diffusion": ["ai", "generative-ai"],
    "image generation": ["ai", "generative-ai"],
    "video generation": ["ai", "generative-ai"],
    "3D generation": ["ai", "generative-ai"],
    "NVIDIA": ["ai", "ai-hardware", "embedded", "chip-design"],
    "NVIDIA GPU": ["ai", "ai-hardware"],
    "GPU": ["ai", "ai-hardware"],
    "TPU": ["ai", "ai-hardware"],
    "NPU": ["ai", "ai-hardware", "embedded", "embedded-ai"],
    "Groq": ["ai", "ai-hardware"],
    "HBM": ["ai", "ai-hardware"],
    "alignment": ["ai", "ai-ethics"],
    "AI safety": ["ai", "ai-ethics"],
    "AI bias": ["ai", "ai-ethics"],
    "AI regulation": ["ai", "ai-ethics"],
    "explainable AI": ["ai", "ai-ethics"],
    "multimodal": ["ai", "multimodal"],
    "vision language": ["ai", "multimodal"],
    "visual language model": ["ai", "multimodal"],
    "speech AI": ["ai", "multimodal"],
    "OCR": ["ai", "multimodal"],
    "AutoGPT": ["ai", "ai-agent"],
    "LangChain": ["ai", "ai-agent"],
    "RAG": ["ai", "ai-agent"],
    "Coding Agent": ["ai", "ai-agent"],
    "AI assistant": ["ai", "ai-agent"],
    "AI agent": ["ai", "ai-agent"],
    "chatgpt": ["ai", "llm", "ai-agent"],
    "openai": ["ai", "llm"],
    "anthropic": ["ai", "llm", "ai-ethics"],
    "prompt": ["ai", "llm"],
    "fine-tuning": ["ai", "llm"],
    "reasoning": ["ai", "llm"],
    "inference": ["ai", "ai-hardware", "llm"],
    "quantization": ["ai", "ai-hardware", "embedded-ai"],
    "model compression": ["ai", "ai-hardware", "embedded-ai"],
    "Atlas": ["robotics", "humanoid"],
    "Optimus": ["robotics", "humanoid"],
    "Figure": ["robotics", "humanoid"],
    "Figure 01": ["robotics", "humanoid"],
    "humanoid": ["robotics", "humanoid"],
    "bipedal": ["robotics", "humanoid"],
    "双足": ["robotics", "humanoid"],
    "运动控制": ["robotics", "humanoid"],
    "AMR": ["robotics", "industrial"],
    "AGV": ["robotics", "industrial"],
    "welding robot": ["robotics", "industrial"],
    "assembly robot": ["robotics", "industrial"],
    "flexible manufacturing": ["robotics", "industrial"],
    "产线自动化": ["robotics", "industrial"],
    "UR": ["robotics", "cobot"],
    "Franka": ["robotics", "cobot"],
    "collaborative robot": ["robotics", "cobot"],
    "cobot": ["robotics", "cobot"],
    "ISO 10218": ["robotics", "cobot"],
    "Waymo": ["robotics", "autonomous-driving"],
    "Tesla FSD": ["robotics", "autonomous-driving"],
    "autonomous driving": ["robotics", "autonomous-driving"],
    "self-driving": ["robotics", "autonomous-driving"],
    "lidar": ["robotics", "autonomous-driving"],
    "V2X": ["robotics", "autonomous-driving"],
    "L4": ["robotics", "autonomous-driving"],
    "L5": ["robotics", "autonomous-driving"],
    "eVTOL": ["robotics", "drone"],
    "drone": ["robotics", "drone"],
    "UAV": ["robotics", "drone"],
    "农业植保": ["robotics", "drone"],
    "无人机": ["robotics", "drone"],
    "ROS2": ["robotics", "robot-software"],
    "ROS": ["robotics", "robot-software"],
    "MoveIt": ["robotics", "robot-software"],
    "Isaac Sim": ["robotics", "robot-software"],
    "simulation": ["robotics", "robot-software"],
    "robot": ["robotics"],
    "robotics": ["robotics"],
    "autonomous": ["robotics", "autonomous-driving"],
    "automation": ["robotics", "industrial"],
    "MQTT": ["embedded", "iot-edge"],
    "CoAP": ["embedded", "iot-edge"],
    "edge computing": ["embedded", "iot-edge"],
    "边缘计算": ["embedded", "iot-edge"],
    "digital twin": ["embedded", "iot-edge"],
    "IoT": ["embedded", "iot-edge"],
    "TinyML": ["embedded", "embedded-ai", "iot-edge"],
    "sensor": ["embedded", "iot-edge"],
    "RISC-V": ["embedded", "risc-v"],
    "RV32": ["embedded", "risc-v"],
    "RV64": ["embedded", "risc-v"],
    "vector extension": ["embedded", "risc-v"],
    "open source ISA": ["embedded", "risc-v"],
    "FreeRTOS": ["embedded", "rtos"],
    "Zephyr": ["embedded", "rtos"],
    "RTLinux": ["embedded", "rtos"],
    "RTOS": ["embedded", "rtos"],
    "real-time OS": ["embedded", "rtos"],
    "实时操作系统": ["embedded", "rtos"],
    "FPGA": ["embedded", "fpga"],
    "Xilinx": ["embedded", "fpga"],
    "Altera": ["embedded", "fpga"],
    "HLS": ["embedded", "fpga"],
    "partial reconfiguration": ["embedded", "fpga"],
    "EDA": ["embedded", "chip-design"],
    "Chiplet": ["embedded", "chip-design"],
    "3D packaging": ["embedded", "chip-design"],
    "post-Moore": ["embedded", "chip-design"],
    "芯片设计": ["embedded", "chip-design"],
    "embedded": ["embedded"],
    "firmware": ["embedded", "iot-edge"],
    "microcontroller": ["embedded", "iot-edge"],
    "MCU": ["embedded", "iot-edge"],
    "Arduino": ["embedded", "iot-edge"],
    "Raspberry Pi": ["embedded", "iot-edge"],
    "Starlink": ["space", "satellite-internet"],
    "Kuiper": ["space", "satellite-internet"],
    "OneWeb": ["space", "satellite-internet"],
    "satellite internet": ["space", "satellite-internet"],
    "phased array": ["space", "satellite-internet"],
    "相控阵": ["space", "satellite-internet"],
    "SpaceX": ["space", "commercial-space", "rocket-tech"],
    "Blue Origin": ["space", "commercial-space"],
    "Rocket Lab": ["space", "commercial-space"],
    "commercial space": ["space", "commercial-space"],
    "商业航天": ["space", "commercial-space"],
    "launch market": ["space", "commercial-space"],
    "Mars": ["space", "deep-space"],
    "moon base": ["space", "deep-space"],
    "月球基地": ["space", "deep-space"],
    "deep space": ["space", "deep-space"],
    "深空探测": ["space", "deep-space"],
    "telescope": ["space", "deep-space"],
    "planetary science": ["space", "deep-space"],
    "ISS": ["space", "orbital"],
    "Tiangong": ["space", "orbital"],
    "天宫": ["space", "orbital"],
    "in-orbit": ["space", "orbital"],
    "在轨服务": ["space", "orbital"],
    "space debris": ["space", "orbital"],
    "reusable rocket": ["space", "rocket-tech"],
    "可回收": ["space", "rocket-tech"],
    "liquid oxygen methane": ["space", "rocket-tech"],
    "液氧甲烷": ["space", "rocket-tech"],
    "rocket engine": ["space", "rocket-tech"],
    "发动机创新": ["space", "rocket-tech"],
    "ISRU": ["space", "space-manufacturing"],
    "太空3D打印": ["space", "space-manufacturing"],
    "月球采矿": ["space", "space-manufacturing"],
    "原位资源利用": ["space", "space-manufacturing"],
    "space": ["space"],
    "rocket": ["space", "rocket-tech"],
    "satellite": ["space", "satellite-internet"],
    "NASA": ["space", "deep-space"],
    "ESA": ["space", "deep-space"],
    "astronaut": ["space", "orbital"],
    "S&P 500": ["finance", "market-indices"],
    "沪深300": ["finance", "china-stock"],
    "上证指数": ["finance", "china-stock"],
    "NAV": ["finance", "fund-nav"],
    "基金净值": ["finance", "fund-nav"],
    "ETF": ["finance", "fund-nav"],
    "估值": ["finance", "fund-nav"],
    "gold": ["finance", "commodities"],
    "黄金": ["finance", "commodities"],
    "crude oil": ["finance", "commodities"],
    "原油": ["finance", "commodities"],
    "期货": ["finance", "commodities"],
    "A股": ["finance", "china-stock"],
    "指数": ["finance", "market-indices"],
    "股票": ["finance", "china-stock"],
    "行情": ["finance", "china-stock"],
}

# ── TF-IDF tertiary tag extraction ────────────────────────────────────────────
# Streaming variant: statistics live in an in-process sliding window over the
# last TFIDF_WINDOW_SIZE processed documents (observation order = processing
# order), so memory stays bounded and idf tracks the current news mix without
# any external index. Extraction stays skipped until TFIDF_WARMUP_SIZE documents
# have been observed — below that, idf values are meaningless.
TFIDF_WINDOW_SIZE = 2000
TFIDF_WARMUP_SIZE = 50
# Tertiary tags only supplement sparse rule results; rich rule matches keep the
# deterministic rule-only output.
TFIDF_MIN_RULE_TAGS = 3
TFIDF_MAX_TERTIARY_TAGS = 3
TFIDF_TITLE_WEIGHT = 2
# score = (title_tf * TFIDF_TITLE_WEIGHT + summary_tf) * idf,
# idf = ln((1 + N) / (1 + df)) + 1 (smoothed, sklearn-style).
TFIDF_MIN_SCORE = 2.5
# Candidates must already recur in the window — a term seen once or twice is
# noise, not a trending topic.
TFIDF_MIN_DOC_FREQ = 3
TFIDF_MIN_TOKEN_LEN = 3
TFIDF_MAX_TOKEN_LEN = 40
# Matches the manual-tag API format ^[a-z0-9-]{1,32}$ (see api.md §条目手动打标).
TFIDF_MAX_TAG_LEN = 32

# Words joined by '-', '+' or '#' stay single tokens so technical terms like
# "risc-v", "gpt-4o" and "tf-idf" survive tokenization.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[+#\-][a-z0-9]+)*")
_TAG_UNSAFE_RE = re.compile(r"[^a-z0-9-]+")
_HYPHENS_RE = re.compile(r"-{2,}")

# ~115 common English stopwords plus URL/HTML noise that survives tokenization
# of scraped snippets; keeps the candidate pool free of words that dominate any
# feed without carrying topic meaning.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "might",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "one",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        # URL / markup noise
        "amp",
        "asp",
        "aspx",
        "com",
        "css",
        "gif",
        "href",
        "htm",
        "html",
        "http",
        "https",
        "img",
        "jpg",
        "jpeg",
        "jsp",
        "net",
        "org",
        "php",
        "png",
        "rss",
        "src",
        "svg",
        "url",
        "webp",
        "www",
        "xml",
    }
)


def _slugify_tag(token: str) -> str:
    slug = _TAG_UNSAFE_RE.sub("-", token)
    slug = _HYPHENS_RE.sub("-", slug).strip("-")
    return slug[:TFIDF_MAX_TAG_LEN]


class TechTopicExtractor:
    """Primary/secondary tag rules plus TF-IDF tertiary tags.

    Every call observes the document into an in-process sliding-window corpus
    (deque of per-document term sets + a df counter decremented on eviction —
    simple incremental bookkeeping, no rebuilds). When the rule match yields
    fewer than TFIDF_MIN_RULE_TAGS tags, high-frequency terminology is scored
    with tf * idf over the window and appended as tertiary tags. All TF-IDF
    failures degrade to the plain rule output; an item is never lost to tagging.
    """

    def __init__(self) -> None:
        self._window: deque[frozenset[str]] = deque()
        self._df: dict[str, int] = {}

    def extract_tags(self, title: str, summary: str = "") -> list[str]:
        title = title or ""
        summary = summary or ""
        text_lower = f"{title} {summary}".lower()
        tags_set: set[str] = set()

        matched_keywords = []
        for keyword, tag_list in KEYWORD_TO_TAG.items():
            if keyword.lower() in text_lower:
                tags_set.update(tag_list)
                matched_keywords.append(keyword)

        if not tags_set:
            tags_set.add("general")

        try:
            self.observe(title, summary)
        except Exception:
            logger.warning("TF-IDF corpus update failed; continuing without it", exc_info=True)

        tertiary_tags: list[str] = []
        if len(tags_set) < TFIDF_MIN_RULE_TAGS:
            try:
                tertiary_tags = self.extract_tfidf_tags(title, summary)
            except Exception:
                logger.warning("TF-IDF tertiary tag extraction failed; keeping rule tags only", exc_info=True)

        return self._sort_tags(tags_set, tertiary_tags)

    def extract_with_level1(self, title: str, level1_tag: str, summary: str = "") -> list[str]:
        tags = self.extract_tags(title, summary)
        if level1_tag not in tags:
            tags.insert(0, level1_tag)
        return tags

    def observe(self, title: str, summary: str) -> None:
        """Feed one processed document into the sliding-window corpus."""
        terms = set(self._tokenize(title))
        terms.update(self._tokenize(summary))
        if not terms:
            return
        if len(self._window) >= TFIDF_WINDOW_SIZE:
            evicted = self._window.popleft()
            for term in evicted:
                count = self._df.get(term, 0)
                if count <= 1:
                    self._df.pop(term, None)
                else:
                    self._df[term] = count - 1
        self._window.append(frozenset(terms))
        for term in terms:
            self._df[term] = self._df.get(term, 0) + 1

    def extract_tfidf_tags(self, title: str, summary: str) -> list[str]:
        """Rank title terms by tf * idf over the window and return at most
        TFIDF_MAX_TERTIARY_TAGS slugified tertiary tags, best score first.

        Deterministic for a given corpus state: ties break on the slug.
        """
        if len(self._window) < TFIDF_WARMUP_SIZE:
            return []
        title_counts = Counter(self._tokenize(title))
        if not title_counts:
            return []
        summary_counts = Counter(self._tokenize(summary))
        corpus_size = len(self._window)
        best: dict[str, float] = {}
        for term, title_tf in title_counts.items():
            df = self._df.get(term, 0)
            if df < TFIDF_MIN_DOC_FREQ:
                continue
            tf = TFIDF_TITLE_WEIGHT * title_tf + summary_counts.get(term, 0)
            idf = math.log((1 + corpus_size) / (1 + df)) + 1.0
            score = tf * idf
            if score < TFIDF_MIN_SCORE:
                continue
            slug = _slugify_tag(term)
            if slug and score > best.get(slug, 0.0):
                best[slug] = score
        ranked = sorted(best.items(), key=lambda entry: (-entry[1], entry[0]))
        return [slug for slug, _ in ranked[:TFIDF_MAX_TERTIARY_TAGS]]

    def _tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        tokens: list[str] = []
        for match in _TOKEN_RE.finditer(text.lower()):
            token = match.group(0)
            if len(token) < TFIDF_MIN_TOKEN_LEN or len(token) > TFIDF_MAX_TOKEN_LEN:
                continue
            if token.isdigit() or token in _STOPWORDS:
                continue
            tokens.append(token)
        return tokens

    def _sort_tags(self, tags: set[str], tertiary: list[str] | None = None) -> list[str]:
        level1_order = ["finance", "tech", "robotics", "ai", "embedded", "space"]
        level2_order = [
            "china-stock",
            "market-indices",
            "commodities",
            "fund-nav",
            "watchlist",
            "humanoid",
            "industrial",
            "cobot",
            "autonomous-driving",
            "drone",
            "robot-software",
            "llm",
            "generative-ai",
            "ai-hardware",
            "ai-ethics",
            "multimodal",
            "ai-agent",
            "iot-edge",
            "risc-v",
            "rtos",
            "fpga",
            "chip-design",
            "embedded-ai",
            "commercial-space",
            "satellite-internet",
            "deep-space",
            "orbital",
            "rocket-tech",
            "space-manufacturing",
        ]

        tertiary = tertiary or []
        tertiary_set = set(tertiary)
        result = []
        for tag in level1_order:
            if tag in tags:
                result.append(tag)
        for tag in level2_order:
            if tag in tags and tag not in result:
                result.append(tag)
        for tag in sorted(tags):
            if tag not in result and tag not in tertiary_set:
                result.append(tag)
        # Tertiary (TF-IDF) tags keep their score order, appended after the
        # level-1/level-2/other buckets; duplicates keep their earlier slot.
        for tag in tertiary:
            if tag not in result:
                result.append(tag)

        return result


_shared_topic_extractor = TechTopicExtractor()


def get_topic_extractor() -> TechTopicExtractor:
    """Per-process shared extractor.

    A fresh processor chain is built for every collection run, so the TF-IDF
    corpus must live on a module-level instance to accumulate across runs;
    reclassify (services/category.py) reuses the same instance so batches see
    the corpus the pipeline built.
    """
    return _shared_topic_extractor


class CategorizerProcessor(BaseProcessor):
    _topic_extractor = get_topic_extractor()

    async def process(self, item: dict, source: Any) -> dict | None:
        category = getattr(source, "category", None)
        if category:
            item["category_id"] = str(getattr(category, "id", ""))
            category_type = getattr(category, "type", "")
            category_slug = getattr(category, "slug", "")

            if category_type == "tech":
                item["topic_tags"] = self._extract_tech_tags(item, category_slug)
            elif category_type == "finance":
                finance_tags = self._determine_finance_tags(item)
                item["topic_tags"] = ["finance"] + finance_tags
            else:
                item["topic_tags"] = [category_slug]
        else:
            item["topic_tags"] = self._topic_extractor.extract_tags(
                item.get("title", "") or "", item.get("summary", "") or ""
            )

        return item

    def _extract_tech_tags(self, item: dict, level1_slug: str) -> list[str]:
        title = item.get("title", "") or ""
        summary = item.get("summary", "") or ""
        return self._topic_extractor.extract_with_level1(title, level1_slug, summary=summary)

    def _determine_finance_tags(self, item: dict) -> list[str]:
        title = item.get("title", "").lower()
        summary = item.get("summary", "").lower() or ""
        text = f"{title} {summary}"
        extra = item.get("extra_data", {}) or {}

        if item.get("type") == "cn_stock" or extra.get("region") == "CN":
            return ["china-stock"]
        if item.get("type") in ("index", "cn_index"):
            return ["market-indices"]
        if item.get("type") == "commodity":
            return ["commodities"]
        if any(kw in text for kw in ["etf", "nav", "估值", "基金净值", "fund"]):
            return ["fund-nav"]

        if any(kw in text for kw in ["指数", "index", "s&p", "nasdaq", "沪深300", "上证", "深证"]):
            return ["market-indices"]
        if any(kw in text for kw in ["黄金", "原油", "gold", "oil", "commodity", "期货", "大宗商品"]):
            return ["commodities"]

        return ["china-stock"]
