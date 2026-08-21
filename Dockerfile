# Lead Outreach OS — Production Dockerfile
#
# Multi-stage build:
#   Stage 1: Node.js — build frontend static assets
#   Stage 2: Python — install backend deps, copy frontend dist, run API
#
# Build:
#   docker build -t lead-outreach-os .
#
# Run:
#   docker run -p 8000:8000 \
#     -e DATABASE_URL=postgresql://... \
#     -e API_AUTH_TOKEN=your-secret \
#     -e CORS_ORIGINS=https://your-frontend.up.railway.app \
#     lead-outreach-os

# --- Stage 1: Build frontend ---
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python backend ---
FROM python:3.14-slim AS backend
WORKDIR /app

# Install system dependencies for psycopg2-binary
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/app/ ./app/

# Copy frontend build artifacts
COPY --from=frontend-build /app/frontend/dist/ ./frontend/dist/

# Create data and logs directories
RUN mkdir -p data logs

# Default database path (overridden by DATABASE_URL env var in production)
ENV DATABASE_URL=sqlite:///./data/lead_outreach.db

# Expose port
EXPOSE 8000

# Health check — verifies app is running and DB is reachable
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ready')" || exit 1

# Start the application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
