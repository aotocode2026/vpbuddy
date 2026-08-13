FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VPBUDDY_DATA_DIR=/var/lib/vpbuddy/meetings \
    VPBUDDY_DOCS_DIR=/var/lib/vpbuddy/docs

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . \
    && mkdir -p /var/lib/vpbuddy/meetings /var/lib/vpbuddy/docs

VOLUME ["/var/lib/vpbuddy"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"]

CMD ["python", "-m", "vpbuddy.server.fastapi_app", "--host", "0.0.0.0", "--port", "8765"]
