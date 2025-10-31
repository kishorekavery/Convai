import logging
from pathlib import Path
from datetime import date
import os

# Log file path
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = Path(__file__).parent.parent / "logs" / f"{date.today()}-application.log"

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),  # Logs to a file
        # logging.StreamHandler()  # Logs to console
    ]
)

# Get a logger instance
def get_logger(name):
    return logging.getLogger(name)