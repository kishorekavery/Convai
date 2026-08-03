"""
Deterministic OFFSET pagination for a previously executed SELECT.

Paging a result set is pure arithmetic, so the next page is computed here rather
than asking the language model to rewrite its own SQL. That removes an LLM
round-trip from the follow-up path and makes page boundaries exact - the model
cannot miscount an OFFSET it never sees.

All helpers take SQL that has already been through
``database.sql_safety.validate_sql`` (single statement, no trailing semicolon).
"""

import re
from typing import Optional, Tuple

from config import get_logger

logging = get_logger(__name__)

# Matches the row-count of a LIMIT / OFFSET clause. finditer + "last match wins"
# is used everywhere below so that a LIMIT inside a subquery does not shadow the
# outer one that actually controls the page.
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_OFFSET_RE = re.compile(r"\bOFFSET\s+(\d+)", re.IGNORECASE)

# Mirrors instruction 7 of the SQL prompt ("Always include a LIMIT clause to
# return at most 50 rows") and the truncation check in the inference router.
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
    """
    Return ``sql`` with its outermost OFFSET set to ``offset``.

    An existing OFFSET is rewritten in place; otherwise the clause is appended,
    which is valid because a validated query ends at its LIMIT.
    """
    if offset < 0:
        raise ValueError(f"OFFSET must not be negative, got {offset}.")

    match = _last_match(_OFFSET_RE, sql)
    if match:
        return f"{sql[: match.start()]}OFFSET {offset}{sql[match.end():]}"

    return f"{sql.rstrip()} OFFSET {offset}"


def next_page_sql(
    sql: str, current_offset: int, page_size: Optional[int] = None
) -> Optional[Tuple[str, int, int]]:
    """
    Build the query for the page after ``current_offset``.

    Args:
        sql: the previously executed (validated) SELECT.
        current_offset: the OFFSET that produced the page the user just saw.
        page_size: rows per page; falls back to the query's own LIMIT.

    Returns:
        ``(next_sql, next_offset, page_size)``, or None when the query cannot be
        paginated - which is the correct answer for an aggregate such as
        ``SELECT COUNT(*) ...`` that has no LIMIT and only ever returns one row.
    """
    if not sql or not sql.strip():
        return None

    resolved_page_size = page_size or extract_limit(sql)
    if not resolved_page_size or resolved_page_size <= 0:
        logging.info("Query has no usable LIMIT clause; refusing to paginate: %s", sql)
        return None

    next_offset = current_offset + resolved_page_size
    return apply_offset(sql, next_offset), next_offset, resolved_page_size
