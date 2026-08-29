FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    TZ=Asia/Tokyo

WORKDIR /app

COPY requirements.txt /app/app/
RUN pip install --no-cache-dir -r /app/app/requirements.txt

# Copy application code
COPY . /app/app

# Setup: permissions, seed database, asset directories
RUN chmod +x /app/app/docker/entrypoint.sh && \
    mv /app/app/data/private_server.sqlite3 /app/app/seed.sqlite3 && \
    mkdir -p /app/app/data /app/main.5465.com.aniplex.nananiji/assets /app/com.aniplex.nananiji

# API health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8888/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["/app/app/docker/entrypoint.sh"]
CMD []
