"""Database session configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.utils.config import get_config


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        config = get_config()
        connect_args = {}
        if config.database_url.startswith("postgresql"):
            connect_args["options"] = f"-c statement_timeout={config.db_statement_timeout_ms}"
        _engine = create_engine(
            config.database_url,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout,
            pool_recycle=config.db_pool_recycle,
            pool_pre_ping=config.db_pool_pre_ping,
            connect_args=connect_args,
        )
    return _engine


def get_sessionmaker():
    engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


# FastAPI dependency

def get_db():
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
