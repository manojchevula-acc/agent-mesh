from sqlalchemy import text
from mcp_server.db import get_engine

engine = get_engine()

with engine.connect() as conn:
    result = conn.execute(text("SELECT DATABASE();"))
    print("Connected to database:", result.scalar())