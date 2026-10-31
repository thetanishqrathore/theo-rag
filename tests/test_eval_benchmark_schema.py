from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_benchmark_v1_validates_cleanly() -> None:
    module = _load_module(
        "eval_validate_benchmark",
        ROOT / "scripts" / "eval_validate_benchmark.py",
    )
    rows = module._load_jsonl(ROOT / "eval" / "benchmark" / "v1_cases.jsonl")
    issues, stats = module.validate_rows(rows)

    assert not issues
    assert stats["valid"] is True
    assert stats["total_cases"] == 200
    assert stats["corpus_counts"]["clough"] == 100
    assert stats["corpus_counts"]["dean"] == 100
    assert stats["tier_counts"]["gold"] == 90
    assert stats["tier_counts"]["silver"] == 110
