import re

# Chat history arrives as one flat string of role-tagged segments, e.g.
#   "user: what are the recent work orders, ai: Here are the 50 most recent...,
#    user: what about last month"
# The lookahead ends a segment at the next role tag. \b guards against splitting
# on text that merely starts with "user"/"ai" (e.g. a value like "user23432343").
_TURN_RE = re.compile(
    r"\b(user|ai)\s*:\s*(.*?)(?=\s*\b(?:user|ai)\s*:|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Assistant answers are full natural-language responses and can be long. The
# classifier only needs enough to resolve a reference, so they are clipped.
MAX_CONTEXT_MESSAGE_CHARS = 300


def parse_chat_turns(chat_history: str) -> list:
    """Split the raw chat history into ordered (role, message) pairs."""
    if not chat_history:
        return []

    turns = []
    for match in _TURN_RE.finditer(chat_history):
        role = "User" if match.group(1).lower() == "user" else "Assistant"
        message = match.group(2).strip().strip(",").strip()
        if message:
            turns.append((role, message))
    return turns


def get_last_n_exchanges(
    chat_history: str,
    n: int = 3,
    max_message_chars: int = MAX_CONTEXT_MESSAGE_CHARS,
) -> str:
    """Render the most recent ``n`` exchanges as a transcript for the classifier."""
    turns = parse_chat_turns(chat_history)
    if not turns:
        return ""

    recent = turns[-(n * 2):]

    lines = []
    for role, message in recent:
        if len(message) > max_message_chars:
            message = message[:max_message_chars].rstrip() + "..."
        lines.append(f"{role}: {message}")

    return "\n".join(lines)


# Words that can appear in a request that asks for nothing but the next page.
# Deliberately small: this is a second filter applied only after the classifier
# has already said "pagination", and the two error directions are not equal.
# Wrongly calling a real question "bare" dead-ends the user; wrongly calling a
# bare phrase "substantive" just tries to answer it, which fails gracefully.
_PAGINATION_WORDS = frozenset(
    {
        "more", "next", "page", "pages", "continue", "show", "give", "me",
        "the", "please", "pls", "further", "another", "remaining", "rest",
        "record", "records", "row", "rows", "result", "results", "item",
        "items", "entry", "entries", "set", "one", "ones", "go", "on", "and",
        "some", "few", "additional",
    }
)


def is_bare_pagination_request(text: str, max_words: int = 6) -> bool:
    """True when ``text`` asks for nothing but the next page."""
    words = re.findall(r"[a-z]+|\d+", (text or "").lower())
    if not words or len(words) > max_words:
        return False
    return all(word.isdigit() or word in _PAGINATION_WORDS for word in words)


def get_last_n_user_queries(chat_history: str, n: int = 3) -> list:
    """Extract all user queries and return the last n."""

    # Find all user queries
    user_queries = re.findall(
        r"user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)", chat_history, re.DOTALL | re.IGNORECASE
    )
    # print(user_queries)

    if user_queries:
        # Clean up whitespace/commas
        user_queries = [u.strip().strip(",") for u in user_queries]
        # print(f"Raw: {user_queries}")

        # Return the last n queries (or fewer if not enough exist).
        # [-n:] not [:n]: this fed the SQL and final-response prompts the three
        # OLDEST messages of the conversation, so in any exchange longer than
        # three turns the model was given stale context and never saw what the
        # user had just been talking about.
        return user_queries[-n:]

    return ""


def get_last_and_current_user_query(chat_history: str, user_query: str) -> str:
    """Concatenate the last n user queries with the current one."""
    last_n_queries = get_last_n_user_queries(chat_history, 1)

    if last_n_queries:
        combined = ". ".join(last_n_queries) + ". " + user_query.strip().strip(",")
        return combined

    return user_query
