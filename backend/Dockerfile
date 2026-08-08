# Build from the REPOSITORY ROOT, not from backend/:
#
#     docker build -f backend/Dockerfile -t pme-care .
#
# The context is the root so the image can carry the frontend as well as the API
# and serve both from one port. That is the whole deployment for a single local
# server: one container, one port, no CORS, no separate static server.

# ---- Build stage: install dependencies into a virtualenv -------------------
FROM python:3.12-slim AS build
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1
COPY backend/requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install -r requirements.txt

# ---- Runtime stage: minimal image, non-root user ---------------------------
FROM python:3.12-slim
WORKDIR /app
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    # Where app.main looks for the SPA; see _mount_frontend.
    FRONTEND_DIR=/app/frontend
COPY --from=build /venv /venv
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
# No build step, so the frontend ships as-is.
COPY frontend ./frontend
RUN useradd --no-create-home appuser && chown -R appuser /app
USER appuser
# Cloud Run injects $PORT; default 8080. Single worker per instance:
# concurrency is handled by the platform (FT-1), keeping memory in the free tier.
# Migrations are deliberately NOT run here — see DEPLOYING.md.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
