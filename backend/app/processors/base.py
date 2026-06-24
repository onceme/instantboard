import abc
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class BaseProcessor(abc.ABC):
    @abc.abstractmethod
    async def process(self, item: dict, source: Any) -> dict | None: ...


@dataclass
class ProcessResult:
    item: dict | None
    metadata: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ProcessorChain:
    def __init__(self, processors: list[BaseProcessor] | None = None):
        self.processors: list[BaseProcessor] = processors or []

    def add(self, processor: BaseProcessor) -> None:
        self.processors.append(processor)

    async def execute(self, raw_items: list[dict], source: Any) -> list[ProcessResult]:
        results = []
        for raw_item in raw_items:
            result = ProcessResult(item=raw_item)
            current_item = raw_item
            for processor in self.processors:
                try:
                    processed = await processor.process(current_item, source)
                    if processed is None:
                        result.item = None
                        result.metadata["filtered_by"] = processor.__class__.__name__
                        break
                    current_item = processed
                    result.item = current_item
                except Exception as e:
                    error_msg = f"{processor.__class__.__name__}: {str(e)}"
                    result.errors.append(error_msg)
                    logger.warning(
                        f"Processor {processor.__class__.__name__} failed for {raw_item.get('url', 'unknown')}: {e}"
                    )

            results.append(result)

        return results
