"""Database initialization and testing script."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import structlog
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()

DATABASE_URL = os.getenv('DATABASE_URL')


def reset_database():
    engine = create_engine(DATABASE_URL)

    logger.info("dropping_all_tables")
    with engine.connect() as conn:
        conn.execute(text("""
            DROP SCHEMA public CASCADE;
            CREATE SCHEMA public;
            GRANT ALL ON SCHEMA public TO raguser;
        """))
        conn.commit()

    logger.info("reading_init_sql")
    init_sql = Path("scripts/init.sql").read_text()

    logger.info("executing_schema_creation")
    with engine.connect() as conn:
        conn.execute(text(init_sql))
        conn.commit()

    logger.info("database_reset_complete")


def test_database():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    tests_passed = []

    logger.info("test_tables_exist")
    result = session.execute(text("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name
    """))
    tables = [row[0] for row in result]

    expected_tables = [
        'corpus', 'file_tags', 'source_files',
        'series', 'parts', 'chapters', 'lessons',
        'paragraphs', 'parent_chunks', 'child_chunks',
        'dean_collections', 'dean_books', 'dean_lessons'
    ]

    for table in expected_tables:
        if table in tables:
            logger.info("table_exists", table=table)
            tests_passed.append(True)
        else:
            logger.error("table_missing", table=table)
            tests_passed.append(False)

    logger.info("test_corpus_initialized")
    result = session.execute(text("SELECT COUNT(*) FROM corpus"))
    count = result.scalar()

    if count > 0:
        logger.info("corpus_initialized", count=count)
        tests_passed.append(True)
    else:
        logger.error("corpus_empty")
        tests_passed.append(False)

    session.close()

    if all(tests_passed):
        logger.info("all_tests_passed", total=len(tests_passed))
        return True
    else:
        logger.error("some_tests_failed", passed=sum(tests_passed), total=len(tests_passed))
        return False


if __name__ == '__main__':
    import sys

    if '--reset' in sys.argv:
        reset_database()
    elif '--test' in sys.argv:
        success = test_database()
        sys.exit(0 if success else 1)
    else:
        print("Usage: python scripts/init_db.py [--reset | --test]")
        sys.exit(1)
