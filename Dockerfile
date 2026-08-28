FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PANTRYOS_LISTEN_HOST=0.0.0.0 \
    PANTRYOS_LISTEN_PORT=8765 \
    PANTRYOS_DATA_DIR=/data \
    PANTRYOS_DATABASE_PATH=/data/pantryos.sqlite3 \
    PANTRYOS_BACKUP_DIR=/data/backups

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends tesseract-ocr \
  && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY app ./app
COPY custom_components ./custom_components
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
COPY .github ./.github
COPY pyproject.toml README.md Dockerfile compose.yaml .dockerignore hacs.json ./

RUN groupadd --system --gid 10001 pantryos \
  && useradd --system --uid 10001 --gid pantryos --home-dir /app --shell /usr/sbin/nologin pantryos \
  && mkdir -p /data \
  && chown -R pantryos:pantryos /data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/v1/health/ready', timeout=3).read()"

ENTRYPOINT ["python", "scripts/docker_entrypoint.py"]
CMD ["python", "app/server.py"]

