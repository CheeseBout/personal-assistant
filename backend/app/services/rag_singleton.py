"""Process-wide RAGEngine singleton.

Loading the embedding + rerank models takes ~200MB of RAM and several seconds.
Previously each api module (chat, upload, debug) held its own ``_rag_engine``
variable, so the model could be loaded up to three times. This module is the
single source of truth — every caller imports ``get_rag_engine`` from here.
"""

from threading import Lock
from typing import Optional

from .rag import RAGEngine

_rag_engine: Optional[RAGEngine] = None
_lock = Lock()


def get_rag_engine() -> RAGEngine:
    """Return the process-wide RAGEngine, building it on first use."""
    global _rag_engine
    if _rag_engine is None:
        with _lock:
            if _rag_engine is None:
                _rag_engine = RAGEngine()
    return _rag_engine


def reset_rag_engine() -> None:
    """Drop the cached engine (test/dev use only)."""
    global _rag_engine
    with _lock:
        _rag_engine = None
