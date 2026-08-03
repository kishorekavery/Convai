"""
Side-by-side of what the intent classifier receives before vs after the
follow-up change. Run:  python tests/show_classification_before_after.py

This renders real inputs through the real helpers. It does NOT call Bedrock -
the "expected output" column is the behaviour the prompt specifies, not a
recorded model response.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataprocessing.user_query_processing import (  # noqa: E402
    get_last_and_current_user_query,
    get_last_n_exchanges,
    get_last_n_user_queries,
)

CONVERSATIONS = [
    {
        "name": "1. Ellipsis follow-up (time range changes)",
        "history": (
            "user: What are the recent work orders for plant A, "
            "ai: Here are the 50 most recent work orders for plant A. The latest is WO-4471 raised on 02-08-2026, "
            "user: what about last month"
        ),
        "message": "what about last month?",
        "expected": {
            "type": "sql",
            "is_followup": True,
            "resolved_query": "What are the work orders for plant A from last month?",
        },
    },
    {
        "name": "2. TOPIC SWITCH (short, but stands alone)",
        "history": (
            "user: What are the recent work orders for plant A, "
            "ai: Here are the 50 most recent work orders for plant A, "
            "user: how many breakdowns happened in plant B"
        ),
        "message": "how many breakdowns happened in plant B?",
        "expected": {
            "type": "sql",
            "is_followup": False,
            "resolved_query": "how many breakdowns happened in plant B?",
        },
    },
    {
        "name": "3. Pronoun with no referent",
        "history": (
            "user: Show the breakdown work orders for pump P-101, "
            "ai: There are 12 breakdown work orders for pump P-101, the oldest open since 14-07-2026, "
            "user: explain that more"
        ),
        "message": "explain that more",
        "expected": {
            "type": "sql",
            "is_followup": True,
            "resolved_query": "Explain the breakdown work orders for pump P-101 in more detail",
        },
    },
    {
        "name": "4. Deep conversation - the slice bug case",
        "history": ", ".join(
            [
                "user: what is PM compliance for plant A, ai: PM compliance for plant A is 91.2%",
                "user: what about plant B, ai: PM compliance for plant B is 88.4%",
                "user: list the overdue calibrations, ai: There are 23 overdue calibrations",
                "user: show the open safety permits, ai: There are 7 open safety permits",
                "user: which technicians closed the most work orders, ai: Top is R. Menon with 48",
                "user: only the critical ones",
            ]
        ),
        "message": "only the critical ones",
        "expected": {
            "type": "sql",
            "is_followup": True,
            "resolved_query": "Which technicians closed the most critical work orders?",
        },
    },
    {
        "name": "5. First message (no history)",
        "history": "",
        "message": "how many breakdowns last week?",
        "expected": {
            "type": "sql",
            "is_followup": False,
            "resolved_query": "how many breakdowns last week?",
        },
    },
]


def main():
    for case in CONVERSATIONS:
        history, message = case["history"], case["message"]

        print("=" * 78)
        print(case["name"])
        print("=" * 78)
        print(f'\nUser types: "{message}"\n')

        print("--- BEFORE: what the classifier received ---")
        before = f"Last User Queries: {get_last_n_user_queries(history)}"
        print(f"  {before}")
        print("  (user turns only, and [:n] takes the FIRST three, not the last)")

        print("\n--- AFTER: what the classifier receives ---")
        after = get_last_n_exchanges(history, n=3)
        for line in (after or "(no previous turns)").splitlines():
            print(f"  {line}")

        print("\n--- Retrieval / SQL input ---")
        print(f"  BEFORE: {get_last_and_current_user_query(history, message)!r}")
        print(f"  AFTER:  {case['expected']['resolved_query']!r}   <- resolved_query")

        print("\n--- Classifier output ---")
        print("  BEFORE: {'type': ..., 'message': ...}   (no follow-up signal at all)")
        print(
            f"  AFTER:  type={case['expected']['type']!r}, "
            f"is_followup={case['expected']['is_followup']}, "
            f"resolved_query={case['expected']['resolved_query']!r}"
        )
        print()


if __name__ == "__main__":
    main()
