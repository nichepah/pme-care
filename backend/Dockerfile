# ---- Build stage: install dependencies into a virtualenv -------------------
FROM python:3.12-slim AS build
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install -r requirements.txt

# ---- Runtime stage: minimal image, non-root user ---------------------------
FROM python:3.12-slim
WORKDIR /app
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=build /venv /venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
RUN useradd --no-create-home appuser && chown -R appuser /app
USER appuser
# Cloud Run injects $PORT; default 8080. Single worker per instance:
# concurrency is handled by Cloud Run (FT-1), keeping memory in free tier.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
