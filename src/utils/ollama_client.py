"""Shared Ollama client helpers for consistent configuration."""

from __future__ import annotations

import time
from typing import Iterable, Tuple

import ollama
import structlog

from src.utils.config import get_config

logger = structlog.get_logger()
_LAST_WARM: dict[str, float] = {}


def _normalize_model_name(model_name: str | None) -> str:
    """Normalize Ollama model identifiers for robust comparisons."""
    raw = (model_name or "").strip().lower()
    if not raw:
        return ""
    # Ollama may return names like "gpt-oss:120b (latest)" in some clients.
    raw = raw.split("(", 1)[0].strip()
    return raw


def _model_name_matches(requested_model: str, running_model: str) -> bool:
    """Check if a running model name corresponds to requested model."""
    req = _normalize_model_name(requested_model)
    run = _normalize_model_name(running_model)
    if not req or not run:
        return False
    if req == run:
        return True
    # Be tolerant to aliases / suffix forms while avoiding unrelated matches.
    if run.startswith(req + ":") or req.startswith(run + ":"):
        return True
    if run.startswith(req + " ") or req.startswith(run + " "):
        return True
    return False


def get_ollama_client(timeout: float | None = None) -> ollama.Client:
    """Return a configured Ollama client.

    Args:
        timeout: Optional override for request timeout in seconds.

    Returns:
        Configured Ollama client.
    """
    config = get_config()
    return ollama.Client(host=config.ollama_host, timeout=timeout or config.ollama_timeout)


def _default_warm_timeout(model_name: str) -> float:
    config = get_config()
    if model_name == config.ollama_model:
        return 120.0
    return 30.0


def warm_ollama_model(model_name: str, keep_alive: str, timeout: float | None = None) -> None:
    """Warm a model in Ollama memory."""
    client = get_ollama_client(timeout=timeout or _default_warm_timeout(model_name))
    client.generate(
        model=model_name,
        prompt=" ",
        options={"num_ctx": 1, "temperature": 0},
        keep_alive=keep_alive,
    )


def _parse_keepalive_seconds(keep_alive: str) -> int:
    """Parse keep-alive string like '30s', '5m', '1h' into seconds."""
    raw = keep_alive.strip().lower()
    if raw.endswith("s"):
        return int(raw[:-1])
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    if raw.endswith("h"):
        return int(raw[:-1]) * 3600
    # Default: treat as seconds if no suffix
    return int(raw)


def maybe_warm_ollama_model(model_name: str, keep_alive: str) -> Tuple[bool, float]:
    """Warm model if it's likely expired; returns (warmed, warmup_ms)."""
    ttl = _parse_keepalive_seconds(keep_alive)
    now = time.monotonic()
    last = _LAST_WARM.get(model_name)
    if last is not None and (now - last) < ttl:
        # Guard against local-cache false positives when Ollama has evicted the model.
        try:
            client = get_ollama_client(timeout=5.0)
            ps = client.ps()
            running_models = []
            for item in ps.get("models", []):
                running_models.append(item.get("name", ""))
                running_models.append(item.get("model", ""))
            if any(_model_name_matches(model_name, running) for running in running_models):
                return False, 0.0
        except Exception:  # noqa: BLE001
            # Avoid warm/reload thrash when ps() is flaky: trust local keepalive cache.
            return False, 0.0

    # If we have no local warm timestamp (or it expired), do one best-effort process check
    # before warming. This avoids unnecessary warm calls after app restarts.
    if last is None or (now - last) >= ttl:
        try:
            client = get_ollama_client(timeout=5.0)
            ps = client.ps()
            running_models = []
            for item in ps.get("models", []):
                running_models.append(item.get("name", ""))
                running_models.append(item.get("model", ""))
            if any(_model_name_matches(model_name, running) for running in running_models):
                _LAST_WARM[model_name] = now
                return False, 0.0
        except Exception:  # noqa: BLE001
            # If ps() itself is unavailable, fall through to a defensive warm once.
            pass

    start = time.perf_counter()
    warm_ollama_model(model_name, keep_alive=keep_alive)
    _LAST_WARM[model_name] = time.monotonic()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return True, elapsed_ms


def warm_ollama_models(model_names: Iterable[str], keep_alive: str) -> None:
    """Warm multiple models, logging failures without raising."""
    for model_name in model_names:
        try:
            warm_ollama_model(model_name, keep_alive)
            _LAST_WARM[model_name] = time.monotonic()
            logger.info("ollama_model_warmed", model=model_name, keep_alive=keep_alive)
        except Exception as exc:  # noqa: BLE001 - warmup should not block startup
            logger.warning("ollama_model_warm_failed", model=model_name, error=str(exc))


def unload_ollama_models(model_names: Iterable[str]) -> None:
    """Request model unloads by setting keep_alive=0s."""
    for model_name in model_names:
        try:
            warm_ollama_model(model_name, keep_alive="0s")
            _LAST_WARM.pop(model_name, None)
            logger.info("ollama_model_unloaded", model=model_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ollama_model_unload_failed", model=model_name, error=str(exc))
