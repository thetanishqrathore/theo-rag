"""CLI entry point for grounded generation."""

from __future__ import annotations

import argparse
import json

from src.db.session import get_sessionmaker
from src.retrieval.retrieve import retrieve_context
from src.generation.generate import generate_grounded_answer
from src.utils.config import get_config


def main() -> None:
    """Run retrieval + generation from CLI.

    Args:
        None

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Grounded generation CLI")
    parser.add_argument("query", type=str)
    parser.add_argument("--corpus", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    config = get_config()
    corpus = args.corpus or config.corpus_name

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        parent_chunks = retrieve_context(args.query, corpus, args.top_k, session)
        result = generate_grounded_answer(
            query=args.query,
            parent_chunks=parent_chunks,
            corpus=corpus,
            model=args.model,
        )
        print(json.dumps(result.model_dump(), indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
