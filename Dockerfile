# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Cloud Run sets PORT automatically (default 8080)
ENV PORT=8080

# Run with gunicorn: 1 worker, 8 threads, 300s timeout for AI processing
CMD exec gunicorn \
    --bind "0.0.0.0:$PORT" \
    --workers 1 \
    --threads 8 \
    --timeout 300 \
    --log-level info \
    main:app
