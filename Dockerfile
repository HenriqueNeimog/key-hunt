FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KEY_HUNT_DATABASE_URL=sqlite:////data/key_hunt.db \
    KEY_HUNT_MEDIA_DIR=/data/media \
    KEY_HUNT_ENVIRONMENT=production

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN python -m pip install --no-cache-dir ".[analysis]"

RUN useradd --create-home --uid 10001 keyhunt \
    && mkdir -p /data/media \
    && chown -R keyhunt:keyhunt /app /data
USER keyhunt

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

