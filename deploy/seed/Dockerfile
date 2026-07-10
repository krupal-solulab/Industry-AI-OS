# Seed / migration runner. Applies Alembic migrations, then registers the demo
# tenant in the control-plane registry (mapping it to its Keycloak Organization).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --retries 5 --timeout 180 uv

WORKDIR /app
COPY packages/shared /app/packages/shared
COPY deploy/migrations /app/migrations
COPY deploy/seed /app/seed

RUN uv pip install --system --no-sources \
    /app/packages/shared \
    alembic "psycopg[binary]>=3.1" "sqlalchemy>=2.0" httpx

CMD ["bash", "/app/seed/run.sh"]
