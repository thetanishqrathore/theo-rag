"""Advisory semantic review for topical PDF validation (non-blocking)."""

from __future__ import annotations

from typing import Any

import httpx

from src.utils.config import get_config


def run_shadow_semantic_review(paragraph_texts: list[str], profile: str) -> dict[str, Any]:
    """Run optional LLM shadow review; never used as a gating signal."""
    config = get_config()
    if not config.dean_pdf_validation_shadow_llm:
        return {"enabled": False, "status": "disabled"}
    content = "\n".join(paragraph_texts[:25])
    if not content.strip():
        return {"enabled": True, "status": "skipped", "reason": "no_content"}

    prompt = (
        "Rate semantic coherence and hierarchy preservation for extracted theological document blocks.\n"
        f"Profile: {profile}\n"
        "Return compact JSON with keys: score(0-1), findings(list), warnings(list).\n"
        "Content:\n"
        f"{content}"
    )
    payload = {
        "model": config.dean_pdf_shadow_llm_model,
        "prompt": prompt,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=float(config.dean_pdf_shadow_llm_timeout_sec)) as client:
            response = client.post(f"{config.ollama_host}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            return {
                "enabled": True,
                "status": "ok",
                "model": config.dean_pdf_shadow_llm_model,
                "raw_response": (data.get("response") or "").strip(),
            }
    except Exception as exc:  # pragma: no cover - defensive, non-critical path
        return {"enabled": True, "status": "error", "error": str(exc)}
