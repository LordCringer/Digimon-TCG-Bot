# --- Build stage: install dependencies ---
FROM python:3.12-slim AS base

# Security: run as a dedicated non-root user, never root
RUN useradd --create-home --shell /bin/bash botuser
WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application code
COPY bot.py .

# Drop root privileges
USER botuser

# Health check hits the endpoint the bot exposes on startup (see bot.py).
# Docker will mark the container unhealthy (and your orchestrator can
# restart it) if this stops responding.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status==200 else 1)"

EXPOSE 8080

CMD ["python", "bot.py"]
