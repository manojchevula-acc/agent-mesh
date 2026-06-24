from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

MYSQL_USER = "root"
MYSQL_PASSWORD = quote_plus("manoj")
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = "9100"
MYSQL_DATABASE = "fab_curated"

engine = create_engine(
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
)

with engine.connect() as conn:
    result = conn.execute(text("SELECT DATABASE();"))
    print("Connected to database:", result.scalar())