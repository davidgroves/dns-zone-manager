# DNS API Dockerfile
# Multi-stage build for smaller final image

# Python build stage
FROM ghcr.io/astral-sh/uv:python3.13-trixie AS builder

# Install git (required for hatch-vcs to detect version from .git)
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy .git directory for version detection
COPY .git .git

# Copy dependency files
COPY pyproject.toml uv.lock README.md ./

# Copy backend source code
COPY backend/ ./backend/

# Create virtual environment and install dependencies
RUN uv sync --frozen --no-dev --extra api


# Runtime stage
FROM python:3.13-slim AS runtime

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy backend source code
COPY --from=builder /app/backend /app/backend

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "dns_zone_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
