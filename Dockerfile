# ---------------------------------------
# Stage 1: Builder
# ---------------------------------------
FROM python:3.12 AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (e.g., for building some C-extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment
RUN python -m venv /opt/venv
# Ensure the virtualenv is used by default in this stage
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python packages into the virtual environment
RUN pip install --no-cache-dir -r requirements.txt
# Ensure gunicorn is installed for the production server
RUN pip install --no-cache-dir gunicorn

# ---------------------------------------
# Stage 2: Final Runtime
# ---------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV TZ="Asia/Kolkata"

# Install tzdata and set timezone
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Ensure we use the virtualenv from the builder stage
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy the pre-built virtual environment from the builder stage
# This avoids installing build-essential and other bloat in the final image
COPY --from=builder /opt/venv /opt/venv

# Copy the application code
COPY . .

# Expose the application port
EXPOSE 8000

# Docker HEALTHCHECK uses /health (liveness) deliberately, NOT /ready.
# Restarting the container because Postgres is unreachable would be wrong - the
# process is healthy, its dependency is not. Point the load balancer at
# /convai/ready instead, which returns 503 when the database cannot be reached.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Worker count. These are ASYNC (uvicorn) workers: each runs an event loop that
# already handles many concurrent requests, so the "(2 x cores) + 1" rule for
# synchronous workers does not apply - the guide is roughly one worker per core.
#
# The binding constraint here is not CPU but database connections, because pools
# cannot be shared across processes:
#     workers x (concurrently active tenants + 1) x DB_MAX_CONN <= max_connections
# Measured on this deployment: max_connections is 2500 and 43 tenant databases
# exist, so connections are not the constraint - at 4 workers the worst case is
# 704, well inside budget. Worker count is therefore a *cache* decision.
#
# It is 1 because the pagination cache lives in process memory: a follow-up
# ("show me more") only finds its cached query when it lands on the same worker
# that served the original, so at N workers it succeeds roughly 1 time in N.
# One async worker handles ~20 concurrent users comfortably - the event loop
# does the I/O and BEDROCK_EXECUTOR_THREADS covers the blocking Bedrock calls.
#
# Raise above 1 only together with a shared query cache (Redis) or sticky
# sessions, or follow-up pagination starts failing for most users.
#
# Fewer workers also means less fragmentation of the per-process caches
# (pagination, table schemas, embeddings), since each worker keeps its own copy.
#
# Gunicorn reads WEB_CONCURRENCY when -w is not given, so this is overridable at
# run time without rebuilding the image.
ENV WEB_CONCURRENCY=1

CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
