FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libgl1 && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy deps first (layer cache)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY backend/src/ ./src/
COPY backend/config/ ./config/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "gernas_rag.main:app", "--host", "0.0.0.0", "--port", "8000"]
