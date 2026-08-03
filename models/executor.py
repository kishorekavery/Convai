"""
Shared thread pool for blocking model calls.

boto3 is synchronous, so every Bedrock invocation is handed to a thread via
``loop.run_in_executor``. Passing ``None`` there uses asyncio's *default*
executor, which is sized ``min(32, cpu_count + 4)`` - 12 threads on an 8-core
box. Since a request holds a thread for the whole duration of a Bedrock call
(seconds, not milliseconds), that default silently caps how many requests a
worker can have in flight, at a number nobody chose and which changes with the
host's core count.

This module makes the pool explicit and configurable. Threads here are almost
always blocked on network I/O rather than burning CPU, so the pool can be much
larger than the core count.
"""

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
