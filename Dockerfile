FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    KEY_HUNT_DATABASE_URL=sqlite:////data/key_hunt.db \
    KEY_HUNT_MEDIA_DIR=/data/media \
    KEY_HUNT_ENVIRONMENT=production

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --all-groups --extra analysis --no-install-project
COPY app ./app
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --all-groups --extra analysis

RUN useradd --create-home --uid 10001 keyhunt \
    && mkdir -p /data/media \
    && chown -R keyhunt:keyhunt /app /data
USER keyhunt

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
  CMD curl --fail http://127.0.0.1:8000/health/ready || exit 1

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
