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

# Command to run the application using gunicorn with 9 uvicorn workers.
# This replaces the single-process `uvicorn` to allow maximum concurrency
# as requested in our previous checklist!
CMD ["gunicorn", "main:app", "-w", "9", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
