"""CLI entry point for retrieval."""

from __future__ import annotations

import argparse
import json

from src.db.session import get_sessionmaker
from src.retrieval.retrieve import retrieve_context


def main() -> None:
    """Run retrieval from CLI."""
    parser = argparse.ArgumentParser(description="Retrieval CLI")
    parser.add_argument("query", type=str)
    parser.add_argument("--corpus", default="clough")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        results = retrieve_context(args.query, args.corpus, args.top_k, session)
        print(json.dumps([r.model_dump() for r in results], indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
