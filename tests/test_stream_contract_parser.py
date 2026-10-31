from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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


def test_stream_parser_accepts_final_contract() -> None:
    module = _load_eval_suite()
    raw = "\n".join(
        [
            "event: message",
            'data: {"type":"status","phase":"retrieving","message":"Finding evidence..."}',
            "",
            "event: message",
            'data: {"type":"token","content":"Hello "}',
            "",
            "event: message",
            'data: {"type":"final","answer_mode":"confident","grounded":true,"refusal":false,"generation_time_ms":10,"follow_ups":[],"used_evidence_ids":[]}',
            "",
        ]
    )
    frames = module.parse_sse_text(raw)
    contract = module.parse_stream_contract(frames)

    assert contract["final_received"] is True
    assert contract["stream_incomplete"] is False
    assert contract["contract_errors"] == []


def test_stream_parser_flags_missing_final() -> None:
    module = _load_eval_suite()
    raw = "\n".join(
        [
            "event: message",
            'data: {"type":"status","phase":"retrieving","message":"Finding evidence..."}',
            "",
            "event: message",
            'data: {"type":"token","content":"partial"}',
            "",
        ]
    )
    frames = module.parse_sse_text(raw)
    contract = module.parse_stream_contract(frames)

    assert contract["final_received"] is False
    assert contract["stream_incomplete"] is True
    assert "Missing final event" in contract["contract_errors"]
