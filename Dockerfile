# ─── Stage 1: frontend build ─────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: python runtime ─────────────────────────
FROM python:3.11-slim AS app
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

COPY app.py db.py s3_uploader.py ./
COPY stand/ ./stand/
COPY eval/evaluation_results.json ./eval/evaluation_results.json
COPY --from=frontend /fe/dist ./frontend/dist

ENV PORT=5000 \
    FLASK_DEBUG=0 \
    FRONTEND_DIST=/app/frontend/dist

EXPOSE 5000
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5000", "--workers", "3", "--timeout", "120"]