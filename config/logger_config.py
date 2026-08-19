import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# This module is imported before config.settings (see config/__init__.py), so
# .env has not been read yet. load_dotenv is idempotent, so calling it here is
# safe and makes the settings below work in local development as well as in
# Docker, where they arrive as real environment variables.
load_dotenv()

LOG_DIR = Path(__file__).parent.parent / "logs"

LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Whether to write full prompts and model responses to the log.
LOG_PROMPTS = os.getenv("LOG_PROMPTS", "False").lower() == "true"

os.makedirs(LOG_DIR, exist_ok=True)

# Generate log filename dynamically using date function (e.g., logs/application_2026-08-10.log)
today_date = datetime.now().strftime("%Y-%m-%d")
LOG_FILE = LOG_DIR / f"application_{today_date}.log"

# Rotate daily at midnight
file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logging.basicConfig(
    level=LOG_LEVEL,
    handlers=[
        file_handler,
        console_handler,
    ],
)

# Suppress noisy OpenTelemetry warning/validation logs
logging.getLogger("opentelemetry").setLevel(logging.ERROR)


# Get a logger instance
def get_logger(name):
    return logging.getLogger(name)
