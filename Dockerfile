FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:///./data/edinet.db \
    COLLECTION_LOG_DIR=/app/data/collection-logs \
    ENABLE_DAILY_CRON=true \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cron \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY mock ./mock
COPY scripts/production-daily-sync.sh scripts/docker-entrypoint.sh ./scripts/
RUN chmod +x ./scripts/production-daily-sync.sh ./scripts/docker-entrypoint.sh \
    && mkdir -p /app/data /app/data/collection-logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
