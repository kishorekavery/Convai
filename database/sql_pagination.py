"""
Deterministic OFFSET pagination for a previously executed SELECT.
"""

import re
from typing import Optional, Tuple

from config import get_logger

logging = get_logger(__name__)

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_OFFSET_RE = re.compile(r"\bOFFSET\s+(\d+)", re.IGNORECASE)
DEFAULT_PAGE_SIZE = 50


def _last_match(pattern: re.Pattern, sql: str) -> Optional[re.Match]:
    """Return the final match of ``pattern`` in ``sql``, or None."""
    match = None
    for match in pattern.finditer(sql):
        pass
    return match


def extract_limit(sql: str) -> Optional[int]:
    """Return the outermost LIMIT value, or None when the query has no LIMIT."""
    match = _last_match(_LIMIT_RE, sql)
    return int(match.group(1)) if match else None


def extract_offset(sql: str) -> int:
    """Return the outermost OFFSET value. A query with no OFFSET starts at 0."""
    match = _last_match(_OFFSET_RE, sql)
    return int(match.group(1)) if match else 0


def apply_offset(sql: str, offset: int) -> str:
    """Return ``sql`` with its outermost OFFSET set to ``offset``."""
    if offset < 0:
        raise ValueError(f"OFFSET must not be negative, got {offset}.")

    match = _last_match(_OFFSET_RE, sql)
    if match:
        return f"{sql[: match.start()]}OFFSET {offset}{sql[match.end():]}"

    return f"{sql.rstrip()} OFFSET {offset}"


def next_page_sql(
    sql: str, current_offset: int, page_size: Optional[int] = None
) -> Optional[Tuple[str, int, int]]:
    """Build the query for the page after ``current_offset``."""
    if not sql or not sql.strip():
        return None

    resolved_page_size = page_size or extract_limit(sql)
    if not resolved_page_size or resolved_page_size <= 0:
        logging.info("Query has no usable LIMIT clause; refusing to paginate: %s", sql)
        return None

    next_offset = current_offset + resolved_page_size
    return apply_offset(sql, next_offset), next_offset, resolved_page_size
