FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends tesseract-ocr \
  && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY app ./app
COPY custom_components ./custom_components
COPY scripts ./scripts
COPY tests ./tests
COPY pyproject.toml README.md Dockerfile compose.yaml ./

RUN groupadd --system --gid 10001 pantryos \
  && useradd --system --uid 10001 --gid pantryos --home-dir /app --shell /usr/sbin/nologin pantryos \
  && mkdir -p /app/data \
  && chown -R pantryos:pantryos /app/data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/v1/health/ready', timeout=3).read()"

ENTRYPOINT ["python", "scripts/docker_entrypoint.py"]
CMD ["python", "app/server.py", "--host", "0.0.0.0", "--port", "8765", "--data", "/app/data/pantryos.sqlite3"]

