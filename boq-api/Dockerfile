FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN mkdir -p /data/uploads && chown -R app:app /data
USER app

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health').status==200 else 1)"

# Migrate, seed (idempotent: roles/permissions/UOMs/admin), then serve. nginx in front sets X-Forwarded-*.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8100 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
