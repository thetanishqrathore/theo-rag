"""Structured logging setup."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import structlog
from structlog.stdlib import LoggerFactory, BoundLogger
from structlog.contextvars import merge_contextvars

from src.observability.logging import ensure_schema
from src.utils.config import get_config


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging.

    Args:
        level: Logging level name (e.g., "INFO").

    Returns:
        None
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root_logger.handlers:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    config = get_config()
    log_dir = Path(config.log_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "api.log").resolve()
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            merge_contextvars,
            ensure_schema,
        ],
    )

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root_logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    if not any(
        isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() == log_file
        for h in root_logger.handlers
    ):
        # Rotate so a long-running service cannot fill the disk.
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=int(config.log_max_bytes),
            backupCount=int(config.log_backup_count),
        )
        file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    structlog.configure(
        logger_factory=LoggerFactory(),
        wrapper_class=BoundLogger,
        cache_logger_on_first_use=True,
        processors=[
            merge_contextvars,
            ensure_schema,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ]
    )
