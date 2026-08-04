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
    """
    Split the raw chat history into ordered (role, message) pairs.

    Returns:
        list[tuple[str, str]]: role is "User" or "Assistant", in the order the
        turns occurred. Empty list when there is no parsable history.
    """
    if not chat_history or not isinstance(chat_history, str):
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
    """
    Render the most recent ``n`` exchanges as a transcript for the classifier.

    Unlike get_last_n_user_queries this keeps the assistant's replies and the
    ordering, which is what makes a reference like "that" or "what about last
    quarter" resolvable at all.

    Args:
        chat_history (str): raw role-tagged history from the request.
        n (int): number of exchanges (a user turn plus its reply counts as one).
        max_message_chars (int): per-message clip length.

    Returns:
        str: "User: ...\\nAssistant: ..." lines, or "" when there is no history.
    """
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
    """
    True when ``text`` asks for nothing but the next page.

    "more", "next 50", "show me more", "page 2" -> True
    "WOs closed in last 7 days"                 -> False
    "show more, sorted by technician"           -> False

    A single domain word disqualifies it, which is the point: the classifier
    occasionally labels a self-contained question as pagination - especially
    after a run of "more" replies - and answering that question is far better
    than telling the user their results expired.
    """
    words = re.findall(r"[a-z]+|\d+", (text or "").lower())
    if not words or len(words) > max_words:
        return False
    return all(word.isdigit() or word in _PAGINATION_WORDS for word in words)


def get_last_n_user_queries(chat_history: str, n: int = 3) -> list:
    """
    Extract all user queries and return the last n (default 3).
    """

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
    """
    Concatenate the last n user queries with the current one.
    """
    last_n_queries = get_last_n_user_queries(chat_history, 1)

    if last_n_queries:
        combined = ". ".join(last_n_queries) + ". " + user_query.strip().strip(",")
        return combined

    return user_query


def get_last_user_query(chat_history: str) -> str:
    """
    Match the first user message up to the next user: or ai: or end of string
    """

    last_user_query_match = re.search(
        r"user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)", chat_history, re.DOTALL | re.IGNORECASE
    )

    if last_user_query_match:
        last_user_query = last_user_query_match.group(1).strip().strip(",")
        return last_user_query

    return ""


if __name__ == "__main__":
    chat_text = "user: What is the PM Compliance for the given facility, ai: The PM Compliance is 10.98%, user: user23432343, ai: ai23443234"

    last_and_current_user_query = get_last_and_current_user_query(
        chat_text, "Give the constituting workorder count grouped by nature of issue"
    )
    last_user_query = get_last_user_query(chat_text)

    print(last_and_current_user_query)
    print(last_user_query)
