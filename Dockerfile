# APN CORE Server - Production Dockerfile
# Multi-stage build for minimal image size

FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels -r requirements.txt

# Production image
FROM python:3.11-slim

LABEL maintainer="APN CORE Team"
LABEL version="1.0.0"
LABEL description="Alpha Protocol Network - Sovereign Mesh Node"

# Create non-root user for security
RUN groupadd -r apn && useradd -r -g apn apn

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache /wheels/*

# Copy application code
COPY --chown=apn:apn . .

# Create data directories
RUN mkdir -p /home/apn/.apn/logs && chown -R apn:apn /home/apn

# Switch to non-root user
USER apn

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APN_HOST=0.0.0.0 \
    APN_PORT=8000 \
    APN_LOG_LEVEL=INFO

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5).raise_for_status()"

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the application
CMD ["python", "apn_server.py"]
