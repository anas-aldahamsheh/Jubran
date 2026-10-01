# Jubran API (production image).
#   docker compose --profile app up -d --build        (from the project root)
# Configuration comes from environment variables (see .env.example); no .env file is copied in.
# Database migrations run automatically when the server starts (safe with several workers).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY src ./src

# Dish photos are written to /app/media (a volume in docker-compose.yml).
RUN useradd --create-home --uid 10001 app && mkdir -p /app/media && chown -R app /app
USER app

EXPOSE 8000

# No --reload (development only). Workers: set WEB_CONCURRENCY (default 1).
# Behind a reverse proxy (HTTPS), set FORWARDED_ALLOW_IPS to the proxy's address so
# visitors' real addresses are used for rate limits.
CMD ["uvicorn", "jubran.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
