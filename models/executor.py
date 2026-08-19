"""Shared thread pool for blocking model calls."""

import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config import get_logger
from config import BEDROCK_EXECUTOR_THREADS

logging = get_logger(__name__)

_executor: Optional[ThreadPoolExecutor] = None


def get_bedrock_executor() -> ThreadPoolExecutor:
    """Return the process-wide executor for blocking model calls."""
    global _executor
    if _executor is None:
        logging.info(
            "Creating the Bedrock call executor with %s threads.",
            BEDROCK_EXECUTOR_THREADS,
        )
        _executor = ThreadPoolExecutor(
            max_workers=BEDROCK_EXECUTOR_THREADS,
            thread_name_prefix="bedrock",
        )
    return _executor


def shutdown_bedrock_executor(wait: bool = True) -> None:
    """Shut the pool down. Called from the FastAPI lifespan handler."""
    global _executor
    if _executor is not None:
        logging.info("Shutting down the Bedrock call executor.")
        _executor.shutdown(wait=wait)
        _executor = None


# Backstop for paths that bypass the lifespan handler (scripts, tests).
atexit.register(lambda: shutdown_bedrock_executor(wait=False))
