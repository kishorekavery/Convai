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

# Whether to write full prompts and model responses to the log.
#
# Measured over 21 MB of production logs, three "Inference Log" messages that
# dump the prompt and response accounted for 89.6% of all volume - a single SQL
# prompt is 15-20 KB once the table schema and ten few-shot examples are
# interpolated. The same text is already recorded on the Arize Phoenix spans as
# llm.input_messages / llm.output_messages, so writing it to disk duplicates it
# and puts customer data on the log volume.
#
# Off by default: token counts, latency and stop reason are still logged, which
# is what the lines are operationally useful for. Set LOG_PROMPTS=true when
# debugging a specific generation.
LOG_PROMPTS = os.getenv("LOG_PROMPTS", "False").lower() == "true"

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
