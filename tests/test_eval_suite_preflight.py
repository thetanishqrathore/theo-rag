from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_eval_suite():
    path = ROOT / "scripts" / "eval_suite.py"
    spec = importlib.util.spec_from_file_location("eval_suite", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_suite"] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


class _FakeResponse:
    def __init__(self, status_code: int, payload, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    def get(self, _url: str, headers=None, timeout: float | None = None):  # noqa: ANN001
        _ = (headers, timeout)
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise RuntimeError("No fake response configured.")
        return self._response


def test_preflight_accepts_healthy_status() -> None:
    module = _load_eval_suite()
    client = _FakeClient(
        response=_FakeResponse(
            200,
            {"status": "healthy", "services": {"postgres": "ok", "qdrant": "ok", "ollama": "ok"}},
        )
    )

    out = module._preflight_api_health(client, "http://localhost:8000")

    assert out["status"] == "healthy"
    assert out["services"]["postgres"] == "ok"


def test_preflight_rejects_non_healthy_status() -> None:
    module = _load_eval_suite()
    client = _FakeClient(
        response=_FakeResponse(
            200,
            {"status": "degraded", "services": {"postgres": "ok", "qdrant": "error", "ollama": "ok"}},
        )
    )

    with pytest.raises(RuntimeError, match="status='degraded'"):
        module._preflight_api_health(client, "http://localhost:8000")


def test_preflight_rejects_connection_errors() -> None:
    module = _load_eval_suite()
    client = _FakeClient(error=ConnectionError("refused"))

    with pytest.raises(RuntimeError, match="could not reach"):
        module._preflight_api_health(client, "http://localhost:8000")
