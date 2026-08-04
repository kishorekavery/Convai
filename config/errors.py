"""
Client-safe error reporting.

Internal failures - Postgres messages, AWS SDK errors, tracebacks - must not be
echoed back to callers. A Postgres error names tables and columns and often
quotes a fragment of the generated SQL, and on this service those messages reach
the chat UI, so a user sees

    SQL Execution Error: column "wo_statuss" does not exist

instead of something actionable.

Instead the full detail is logged against a short random reference, and only
that reference is returned. The user can quote it in a support request and an
operator can grep for it, so nothing is lost for debugging.
"""

import uuid


def new_error_reference() -> str:
    """Return a short unique id correlating a client response with the logs."""
    return uuid.uuid4().hex[:12]


def client_error_detail(message: str, reference: str) -> str:
    """Build the user-facing ``detail`` for an HTTPException."""
    return f"{message} (reference: {reference})"
