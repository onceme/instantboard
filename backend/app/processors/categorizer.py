import logging
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


class TechTopicExtractor:
    def extract_tags(self, text: str) -> list[str]:
        text_lower = text.lower()
        tags_set: set[str] = set()

        matched_keywords = []
        for keyword, tag_list in KEYWORD_TO_TAG.items():
            if keyword.lower() in text_lower:
                tags_set.update(tag_list)
                matched_keywords.append(keyword)

        if not tags_set:
            tags_set.add("general")

        sorted_tags = self._sort_tags(tags_set)
        return sorted_tags

    def extract_with_level1(self, text: str, level1_tag: str) -> list[str]:
        tags = self.extract_tags(text)
        if level1_tag not in tags:
            tags.insert(0, level1_tag)
        return tags

    def _sort_tags(self, tags: set[str]) -> list[str]:
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

        result = []
        for tag in level1_order:
            if tag in tags:
                result.append(tag)
        for tag in level2_order:
            if tag in tags and tag not in result:
                result.append(tag)
        for tag in sorted(tags):
            if tag not in result:
                result.append(tag)

        return result


class CategorizerProcessor(BaseProcessor):
    _topic_extractor = TechTopicExtractor()

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
                f"{item.get('title', '')} {item.get('summary', '')}"
            )

        return item

    def _extract_tech_tags(self, item: dict, level1_slug: str) -> list[str]:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        return self._topic_extractor.extract_with_level1(text, level1_slug)

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
