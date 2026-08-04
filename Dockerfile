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

# Run as a non-root user. A container escape or a compromised dependency then
# lands on an unprivileged account rather than root.
#
# The uid is fixed rather than auto-assigned because ./logs is bind-mounted from
# the host: the host directory must be writable by this uid, and a stable number
# makes that a one-time `chown -R 10001 logs` instead of a moving target.
RUN groupadd --gid 10001 convai \
    && useradd --uid 10001 --gid convai --no-create-home --shell /usr/sbin/nologin convai

WORKDIR /app

# Copy the pre-built virtual environment from the builder stage
# This avoids installing build-essential and other bloat in the final image
COPY --from=builder /opt/venv /opt/venv

# Copy the application code, owned by the runtime user
COPY --chown=convai:convai . .

# The log directory must exist and be writable before dropping privileges -
# a bind mount inherits the host directory's ownership, so this only covers
# the case where no volume is mounted.
RUN mkdir -p /app/logs && chown -R convai:convai /app/logs

USER convai

# Expose the application port
EXPOSE 8000

# Docker HEALTHCHECK uses /health (liveness)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENV WEB_CONCURRENCY=1

CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
