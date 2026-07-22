"""
Trace-based live groundedness monitor.

Pulls recent "4. final_response" spans for a tenant from the Phoenix
collector this app already reports to (COLLECTOR_ENDPOINT), judges each
answer against the exact data context it was given
(the "metadata.grounding_context" span attribute set in
routers/llm_inference.py), writes the verdict back as a Phoenix span
annotation (visible per-trace in the Phoenix UI), and writes a local CSV
summary + prints the worst-scoring spans for follow-up.

NOTE: DynamicProjectProcessor (routers/llm_inference.py) routes each
tenant's spans into a Phoenix project named after their `database_name`,
so this script judges ONE tenant project per run - pass --project for the
tenant you want to check, or loop this script over your known tenant
database names.

Usage:
    python -m evals.run_trace_groundedness_eval --project asianpaints.ai --hours 24
    python -m evals.run_trace_groundedness_eval --project asianpaints.ai --hours 24 --dry-run
"""

import argparse
import csv
from datetime import datetime, timedelta, timezone

from phoenix.client import Client
import sys
import os

path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(path)

from config import get_logger, COLLECTOR_ENDPOINT, PHOENIX_API_KEY
from evals.judge import GroundednessJudge

logging = get_logger(__name__)

FINAL_RESPONSE_SPAN_NAME = "4. final_response"
ANSWER_ATTR = "llm.output_messages.0.message.content"
CONTEXT_ATTR = "metadata.grounding_context"
QUESTION_ATTR = "metadata.user_question"


def _phoenix_base_url(collector_endpoint: str) -> str:
    """COLLECTOR_ENDPOINT is the OTLP traces ingestion URL
    (e.g. http://host:6006/v1/traces); the REST client needs the server root."""
    return collector_endpoint.rsplit("/v1/traces", 1)[0]


def _get_attr(attributes: dict, dotted_key: str):
    """Read an OTel attribute that the Phoenix API may return either as a
    flat dotted key or as nested JSON (dot segments as nested dict keys)."""

    if not attributes:
        return None
    if dotted_key in attributes:
        return attributes[dotted_key]

    node = attributes
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def fetch_final_response_spans(client: Client, project: str, hours: int, limit: int) -> list:
    start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    # Filtered client-side (rather than via the `name=` server filter) so this
    # script works against older self-hosted Phoenix servers too.
    spans = client.spans.get_spans(
        project_identifier=project,
        start_time=start_time,
        limit=limit,
    )
    return [s for s in spans if s.get("name") == FINAL_RESPONSE_SPAN_NAME]


def evaluate_spans(spans: list, judge: GroundednessJudge, client: Client, dry_run: bool) -> list:
    rows = []
    for span in spans:
        attributes = span.get("attributes") or {}
        span_id = (span.get("context") or {}).get("span_id")
        answer = _get_attr(attributes, ANSWER_ATTR)
        context = _get_attr(attributes, CONTEXT_ATTR)
        question = _get_attr(attributes, QUESTION_ATTR) or ""

        if not answer:
            logging.warning("Span %s has no answer content, skipping.", span_id)
            continue
        if context is None:
            logging.warning(
                "Span %s has no '%s' attribute (produced before the tracing "
                "update, or a non-standard trace) - skipping.", span_id, CONTEXT_ATTR,
            )
            continue

        try:
            verdict = judge.judge(user_input=str(question), context=str(context), answer=str(answer))
        except Exception as e:
            logging.error("Judge failed for span %s: %s", span_id, e)
            continue

        rows.append(
            {
                "span_id": span_id,
                "label": verdict["label"],
                "unsupported_claims": "; ".join(verdict.get("unsupported_claims", [])),
                "rationale": verdict.get("rationale", ""),
            }
        )

        if not dry_run and span_id:
            client.spans.add_span_annotation(
                span_id=span_id,
                annotation_name="groundedness",
                annotator_kind="LLM",
                label=verdict["label"],
                score=1.0 if verdict["label"] == "grounded" else 0.0,
                explanation=verdict.get("rationale", ""),
            )

    return rows


def write_summary_csv(rows: list, path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Phoenix project name (tenant database_name)")
    parser.add_argument("--hours", type=int, default=24, help="Look-back window in hours (default: 24)")
    parser.add_argument("--limit", type=int, default=500, help="Max spans to pull before filtering (default: 500)")
    parser.add_argument("--dry-run", action="store_true", help="Judge but don't write annotations back to Phoenix")
    parser.add_argument("--out", default="groundedness_report.csv", help="Path to write the summary CSV")
    args = parser.parse_args()

    client = Client(base_url=_phoenix_base_url(COLLECTOR_ENDPOINT), api_key=PHOENIX_API_KEY)
    judge = GroundednessJudge()

    spans = fetch_final_response_spans(client, args.project, args.hours, args.limit)
    print(
        f"Fetched {len(spans)} '{FINAL_RESPONSE_SPAN_NAME}' spans from "
        f"project={args.project!r} over the last {args.hours}h."
    )

    rows = evaluate_spans(spans, judge, client, args.dry_run)
    write_summary_csv(rows, args.out)

    total = len(rows)
    grounded = sum(1 for r in rows if r["label"] == "grounded")
    rate = (grounded / total * 100) if total else 0.0
    print(f"Judged {total} spans - grounded rate: {grounded}/{total} ({rate:.1f}%)")
    if total:
        print(f"Summary written to {args.out}" + ("" if not args.dry_run else " (annotations NOT written back - dry run)"))
    else:
        print("Nothing to write - no matching spans had a judgeable answer/context.")

    worst = [r for r in rows if r["label"] != "grounded"]
    if worst:
        print(f"\n{len(worst)} span(s) flagged for review:")
        for r in worst[:20]:
            print(f"  - span_id={r['span_id']} label={r['label']} rationale={r['rationale']}")


if __name__ == "__main__":
    main()
