from app.processors.base import BaseProcessor, ProcessorChain, ProcessResult
from app.processors.categorizer import CategorizerProcessor
from app.processors.dedup import DedupProcessor
from app.processors.filter import FilterProcessor
from app.processors.transformer import TransformerProcessor

__all__ = [
    "BaseProcessor",
    "ProcessorChain",
    "ProcessResult",
    "DedupProcessor",
    "FilterProcessor",
    "CategorizerProcessor",
    "TransformerProcessor",
]


def create_default_processor_chain() -> ProcessorChain:
    chain = ProcessorChain()
    chain.add(DedupProcessor())
    chain.add(FilterProcessor())
    chain.add(CategorizerProcessor())
    chain.add(TransformerProcessor())
    return chain
