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
