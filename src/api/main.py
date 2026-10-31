"""FastAPI application entry point."""

from __future__ import annotations

import structlog
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path
from sentence_transformers import SentenceTransformer, CrossEncoder

from src.api.routes import router, set_models
from src.observability.middleware import RequestLoggingMiddleware
from src.utils.config import get_config
from src.utils.logging import setup_logging
from src.utils.ollama_client import warm_ollama_models, unload_ollama_models

logger = structlog.get_logger()

app = FastAPI(
    title="Theology RAG API",
    description="Evidence-first RAG system for theological teachings",
    version="1.0.0",
)

config = get_config()
canonical_dir = Path(config.canonical_path)
canonical_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(canonical_dir)), name="static")
allowed_origins = [origin.strip() for origin in config.api_allowed_origins.split(",") if origin.strip()]
if not allowed_origins:
    allowed_origins = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(router)

REPO_ROOT = Path(__file__).resolve().parents[2]
# Prefixes owned by the API and canonical evidence; never answered with the SPA shell.
_NON_SPA_PREFIXES = ("api/", "static/", "docs", "redoc", "openapi.json")


def _resolve_frontend_dist(raw_path: str) -> Path | None:
    if not raw_path.strip():
        return None
    dist = Path(raw_path).expanduser()
    if not dist.is_absolute():
        dist = REPO_ROOT / dist
    return dist.resolve() if (dist / "index.html").is_file() else None


frontend_dist = _resolve_frontend_dist(config.frontend_dist_path)
if frontend_dist is not None:
    if (frontend_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        if full_path.startswith(_NON_SPA_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (frontend_dist / full_path).resolve()
        if full_path and candidate.is_file() and frontend_dist in candidate.parents:
            return FileResponse(candidate)
        # SPA shell must not be cached, so a rebuilt frontend is picked up immediately.
        return FileResponse(frontend_dist / "index.html", headers={"Cache-Control": "no-cache"})


@app.on_event("startup")
async def startup() -> None:
    setup_logging()
    log = logger.bind(function="api_startup")
    config = get_config()
    worker_env = os.getenv("UVICORN_WORKERS") or os.getenv("WEB_CONCURRENCY")
    if worker_env:
        try:
            workers = int(worker_env)
        except ValueError:
            workers = 0
        if workers and workers > 1:
            log.warning("multi_worker_detected", workers=workers)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("loading_models", embedding=config.embedding_model, reranker=config.reranker_model)
    embedder = SentenceTransformer(config.embedding_model, device=device)
    reranker = CrossEncoder(config.reranker_model, device=device)
    set_models(embedder, reranker)
    log.info("models_loaded", device=device)
    if config.ollama_warm_on_startup:
        warm_ollama_models(
            [config.ollama_model, config.secondary_ollama_model],
            keep_alive=config.ollama_keepalive,
        )


@app.on_event("shutdown")
async def shutdown() -> None:
    setup_logging()
    config = get_config()
    if config.ollama_warm_on_startup:
        unload_ollama_models([config.ollama_model, config.secondary_ollama_model])
    logger.info("api_shutting_down")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)
