# =============================================================================
# Stage 1 — Builder
# Install dependencies into a virtual environment so only the venv is copied
# into the runtime stage (no pip, no build tools, no cache).
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build-time system deps needed to compile psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create venv at /app/venv so paths are identical in the runtime stage.
# Venv shebang lines are absolute — if we build at /build/venv and copy to
# /app/venv the shebangs break. Building at /app/venv avoids the mismatch.
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Copy only requirements first — leverages Docker layer cache.
# If requirements.txt hasn't changed, pip install is skipped on rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# =============================================================================
# Stage 2 — Runtime
# Lean image: no compiler, no build tools, no pip cache.
# =============================================================================
FROM python:3.12-slim AS runtime

# Runtime system dep: libpq is needed by psycopg2 at run time
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built venv from the builder — paths match so shebangs work
COPY --from=builder /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# ---------------------------------------------------------------------------
# Non-root user (requirement: "service must run as non-root")
# ---------------------------------------------------------------------------
RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

# Copy application source from the app/ subdirectory
COPY app/ ./

# Also copy migrations so alembic upgrade head works inside the container
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Lock down ownership — app user owns only what it needs to read/run
RUN chown -R app:app /app

USER app

# ---------------------------------------------------------------------------
# Configuration — all secrets and coordinates come in via environment.
# No defaults for credentials; the container will fail fast if they're absent.
# ---------------------------------------------------------------------------
# DB_HOST        — Postgres hostname  (e.g. "postgres" in Compose, RDS endpoint on AWS)
# DB_PORT        — Postgres port      (default 5432)
# DB_NAME        — Database name
# DB_USER        — Database user
# DB_PASSWORD    — Database password  (injected via Compose env / SSM on AWS)
ENV DB_PORT=5432

EXPOSE 8080

# ---------------------------------------------------------------------------
# Health check
# Calls our own /health endpoint.  The endpoint returns 503 if the DB is
# unreachable, so the container is only considered healthy when both the
# app AND the database are ready.
# ---------------------------------------------------------------------------
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8080/health || exit 1

# Run with gunicorn for production-grade concurrency
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
