import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# This module is imported before config.settings (see config/__init__.py), so
# .env has not been read yet. load_dotenv is idempotent, so calling it here is
# safe and makes the settings below work in local development as well as in
# Docker, where they arrive as real environment variables.
load_dotenv()

LOG_DIR = Path(__file__).parent.parent / "logs"

# Rotation is size-based rather than daily. The previous configuration opened a
# new file per day and never removed any - 45 files and 36 MB had accumulated,
# the oldest a year old. Size-based rotation is the only option that bounds disk
# usage regardless of traffic, which matters here because full prompts and
# responses are logged at INFO.
#
# Worst case on disk is LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1), so the defaults
# below cap the directory at roughly 550 MB.
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(50 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# A single rolling file, not one per date: RotatingFileHandler manages
# application.log plus application.log.1 ... .N and deletes anything older.
LOG_FILE = LOG_DIR / "application.log"

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
        # Also to stdout, which Docker captures. That stream is rotated
        # separately by the json-file driver options in docker-compose.yml.
        logging.StreamHandler(),
    ],
)

# Suppress noisy OpenTelemetry warning/validation logs
logging.getLogger("opentelemetry").setLevel(logging.ERROR)


# Get a logger instance
def get_logger(name):
    return logging.getLogger(name)
