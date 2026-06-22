"""
mcp_server/db.py
-----------------
Database engine factory for the FAB MCP server.

Reads MySQL credentials from the .env file and returns a SQLAlchemy engine
connected to the fab_semantic schema.  The password is URL-encoded so that
special characters (e.g. @) are handled correctly.
"""

import os
import logging
from urllib.parse import quote_plus

from sqlalchemy import create_engine, Engine
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env once when this module is imported
load_dotenv()


def get_engine() -> Engine:
    """
    Build and return a SQLAlchemy engine connected to fab_semantic.

    Raises:
        RuntimeError: if required environment variables are missing.
    """
    host     = os.getenv("MYSQL_HOST", "127.0.0.1")
    port     = int(os.getenv("MYSQL_PORT", "3306"))
    user     = os.getenv("MYSQL_USER", "")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "fab_semantic")

    if not user:
        raise RuntimeError("MYSQL_USER is not set in .env")
    if not password:
        raise RuntimeError("MYSQL_PASSWORD is not set in .env")

    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )

    engine = create_engine(url, echo=False, pool_pre_ping=True, pool_recycle=1800)
    logger.info("DB engine created → %s:%d / %s", host, port, database)
    return engine
