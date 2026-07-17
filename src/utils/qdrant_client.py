"""Shared Qdrant client with simple process-level caching."""

from __future__ import annotations

from typing import Optional

from qdrant_client import QdrantClient

from src.utils.config import get_config

_CLIENT: Optional[QdrantClient] = None
_TIMEOUT: Optional[float] = None


def get_qdrant_client(timeout: float = 30.0) -> QdrantClient:
    """Return a cached Qdrant client, recreating when timeout changes."""
    global _CLIENT
    global _TIMEOUT

    if _CLIENT is None or _TIMEOUT != timeout:
        config = get_config()
        _CLIENT = QdrantClient(host=config.qdrant_host, port=config.qdrant_port, timeout=timeout)
        _TIMEOUT = timeout
    return _CLIENT
