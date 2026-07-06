"""
Malformed Query Report — Convai / Bedrock / Arize-Phoenix
==========================================================
Pulls spans from the project's own Arize Phoenix instance and produces
a CSV report of:

  1. SQL-generation failures  → "3. sql_generation" spans with
       • status_code == ERROR, OR
       • sql.row_count == 0        (empty-result queries)

  2. Rejected / irrelevant queries → "1. intent_classification" spans
       • whose status_code == ERROR  (out-of-scope, rejected, etc.)

Configuration is read from the same environment variables used by the
FastAPI application (config/settings.py):

    COLLECTOR_ENDPOINT   e.g.  http://maintverse.com:6006/v1/traces
    PHOENIX_API_KEY      optional bearer token
    LOOKBACK_HOURS       default 400
    OUTPUT_DIR           default ./data/collected_spans
"""

from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config — mirrors what config/settings.py exports so that this script can
# be run standalone (python malformed_query_report.py) without importing the
# FastAPI application.
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv()

# Derive the bare Phoenix base URL from COLLECTOR_ENDPOINT.
# COLLECTOR_ENDPOINT in settings.py points to  <host>/v1/traces
# The REST query API lives at                   <host>/v1/spans  etc.
_raw_endpoint: str = os.getenv(
    "COLLECTOR_ENDPOINT", "http://maintverse.com:6006/v1/traces"
)
PHOENIX_BASE_URL: str = _raw_endpoint.rstrip("/").removesuffix("/v1/traces")

PHOENIX_API_KEY: Optional[str] = os.getenv("PHOENIX_API_KEY")

LOOKBACK_DAYS: float = float(os.getenv("LOOKBACK_DAYS", "17"))   # default ~17 days
LOOKBACK_HOURS: int   = int(LOOKBACK_DAYS * 24)                    # used internally
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "./data/collected_spans")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Auth headers — same pattern used in llm_inference.py
_AUTH_HEADERS: Dict[str, str] = (
    {"Authorization": f"Bearer {PHOENIX_API_KEY}"} if PHOENIX_API_KEY else {}
)

# Span names as defined in llm_inference.py
SPAN_INTENT_CLASSIFICATION = "1. intent_classification"
SPAN_SQL_GENERATION         = "3. sql_generation"

# ---------------------------------------------------------------------------
# Rate-limit error exclusion
# These patterns match errors raised by user_quota_limiter.py — they are
# expected throttle responses, not real application bugs, so we exclude them.
# ---------------------------------------------------------------------------
_RATE_LIMIT_PATTERNS: List[str] = [
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "throttl",          # throttled / throttling
    "too many requests",
    "429",
]

def _is_rate_limit_error(msg: str) -> bool:
    """Return True if *msg* looks like a user quota / rate-limit error."""
    if not msg:
        return False
    lower = msg.lower()
    return any(p in lower for p in _RATE_LIMIT_PATTERNS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phoenix REST helpers
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None, timeout: int = 30) -> Optional[dict]:
    """GET wrapper with auth and error handling. Returns parsed JSON or None."""
    url = f"{PHOENIX_BASE_URL}{path}"
    try:
        r = requests.get(url, params=params, headers=_AUTH_HEADERS, timeout=timeout)
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                # Endpoint returned 200 but non-JSON body (e.g. plain-text health)
                return {}  # treat as success with empty payload
        if r.status_code == 404:
            logger.warning("404 from Phoenix at %s", url)
            return None
        logger.error("Phoenix %s -> HTTP %s: %s", url, r.status_code, r.text[:300])
        return None
    except requests.exceptions.ConnectionError:
        logger.error("Cannot connect to Phoenix at %s -- is it running?", PHOENIX_BASE_URL)
        return None
    except Exception as exc:
        logger.error("Request error: %s", exc)
        return None


def _health_check() -> bool:
    """
    Probe the Phoenix server with a raw HTTP request.
    Intentionally avoids JSON parsing — /health may return plain text or
    an empty 200 body depending on the Phoenix version.
    """
    url = f"{PHOENIX_BASE_URL}/health"
    try:
        r = requests.get(url, headers=_AUTH_HEADERS, timeout=5)
        return r.status_code < 500  # 200, 401, 404 all mean the server is up
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return False


def get_phoenix_projects() -> List[str]:
    """
    Return all project names visible in this Phoenix instance.
    Each project corresponds to one client database_name as set by
    DynamicProjectProcessor in llm_inference.py.
    """
    data = _get("/v1/projects", timeout=10)
    if data:
        names = [p.get("name") for p in data.get("data", []) if p.get("name")]
        if names:
            logger.info("Discovered %d Phoenix project(s): %s", len(names), names)
            return names
    logger.warning("No projects found — falling back to 'default'")
    return ["default"]


# All span names produced by llm_inference.py
ALL_SPAN_NAMES: List[str] = [
    "chat_chain",                  # parent span  (HTTPException / 500 errors land here)
    "1. intent_classification",
    "2. embedding_generation",
    "3. sql_generation",
    "4. final_response",
]


def fetch_spans(
    project_name: str,
    span_name: str = None,
    hours: int = LOOKBACK_HOURS,
) -> List[Dict]:
    """
    Fetch spans from *project_name* via the correct Phoenix 11.x REST endpoint:
        GET /v1/projects/{project_identifier}/spans

    The API has a hard max of 1000 spans per page; we paginate via the
    'next_cursor' field until all pages are consumed.

    If *span_name* is given, client-side filtering is applied after fetch
    (the endpoint does not support a span-name filter parameter).
    """
    start_time = (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    all_spans: List[Dict] = []
    cursor: Optional[str] = None
    page_size = 1000  # API maximum

    while True:
        params: Dict = {
            "start_time": start_time,
            "limit":      page_size,
        }
        if cursor:
            params["cursor"] = cursor

        data = _get(f"/v1/projects/{project_name}/spans", params=params)
        if data is None:
            break

        page_spans = data.get("data", [])
        all_spans.extend(page_spans)

        # Check for next page
        next_cursor = data.get("next_cursor") or (
            data.get("meta", {}) or {}
        ).get("next_cursor")
        if not next_cursor or not page_spans:
            break
        cursor = next_cursor

    # Client-side filter by span name
    if span_name:
        all_spans = [s for s in all_spans if s.get("name") == span_name]

    label = span_name or "ALL"
    logger.info(
        "  [%s] '%s' -> %d span(s) fetched", project_name, label, len(all_spans)
    )
    return all_spans


# ---------------------------------------------------------------------------
# Attribute extraction helpers — keyed to the attribute names used in
# llm_inference.py (SpanAttributes from openinference.semconv.trace)
# ---------------------------------------------------------------------------

def _get_attr(span: Dict, key: str, default=None):
    """
    Read an attribute from a span.
    Handles two layouts:
      1. Flat attributes dict:  span['attributes']['sql.row_count']
      2. Nested context dict:   span['context']['trace_id']  (for context.* keys)
    """
    # context.* keys live under span['context'], not span['attributes']
    if key.startswith("context."):
        sub_key = key[len("context."):]
        ctx = span.get("context") or {}
        return ctx.get(sub_key, default)

    attrs = span.get("attributes") or {}
    # Direct flat key
    if key in attrs:
        return attrs[key]
    # dot-path fallback (e.g. 'user.id' stored as nested dict in some versions)
    parts = key.split(".")
    cur = attrs
    for p in parts:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _span_ids(span: Dict):
    """Return (trace_id, span_id) from the span's context dict."""
    ctx = span.get("context") or {}
    return ctx.get("trace_id"), ctx.get("span_id")


def _extract_user_query(span: Dict) -> Optional[str]:
    """
    Pull the original user query.  Tries the locations that llm_inference.py
    populates:
      • input.value          (set on the parent chat_chain span and sub-spans)
      • llm.input_messages.0.message.content  (SQL prompt injected into span3)
      • user.raw_input       (legacy / custom attribute)
    """
    for key in (
        "user.raw_input",
        "input.value",
        "llm.input_messages.0.message.content",
    ):
        val = _get_attr(span, key)
        if val:
            return _parse_query_from_text(str(val))
    return None


def _parse_query_from_text(text: str) -> str:
    """
    Best-effort extraction of the human turn from a formatted prompt string.
    Handles JSON message arrays and common text patterns.
    """
    stripped = text.strip()

    # JSON message list  [{"role": "user", "content": "..."}, ...]
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, list):
                for msg in reversed(obj):
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        return msg.get("content", text)
            elif isinstance(obj, dict):
                for k in ("user_query", "query", "user", "input", "question"):
                    if k in obj:
                        return str(obj[k])
        except json.JSONDecodeError:
            pass

    # Plain-text label patterns
    for pattern in (
        r"(?:User|Human):\s*(.*?)(?:\n|$)",
        r"(?:Question|Query|Input):\s*(.*?)(?:\n|$)",
        r'"user_query"\s*:\s*"([^"]*)"',
        r'"query"\s*:\s*"([^"]*)"',
        r'"input"\s*:\s*"([^"]*)"',
    ):
        m = re.search(pattern, stripped, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()

    # Fallback: first 250 characters
    return (stripped[:250] + "...") if len(stripped) > 250 else stripped


def _extract_generated_sql(span: Dict) -> Optional[str]:
    """
    Extract the SQL that the model produced.  llm_inference.py records it
    via sql_agent which sets sql.generated_query or embeds it in output.value.
    """
    sql = _get_attr(span, "sql.generated_query")
    if sql:
        return str(sql)

    output = _get_attr(span, "output.value") or _get_attr(
        span, "llm.output_messages.0.message.content"
    )
    if output:
        out_str = str(output)
        # Accept if it looks like SQL
        if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b", out_str, re.IGNORECASE):
            return out_str
        # JSON wrapper  {"sql": "SELECT ..."}
        try:
            obj = json.loads(out_str)
            if isinstance(obj, dict):
                return obj.get("sql") or obj.get("query")
        except json.JSONDecodeError:
            pass

    return None


def _sql_status_bucket(span: Dict) -> str:
    """
    Classify the outcome of a sql_generation span into one of:
        success | empty_result | execution_error
    Uses the attributes written by sql_agent and the span status_code.
    """
    status_code  = span.get("status_code", "OK")
    row_count    = _get_attr(span, "sql.row_count")
    exec_status  = _get_attr(span, "sql.execution_status")
    error_msg    = _get_attr(span, "sql.error_message") or _get_attr(span, "error.message")

    if exec_status == "execution_error" or status_code == "ERROR":
        return "execution_error"
    if row_count is not None and (row_count == 0 or row_count == "0"):
        return "empty_result"
    if error_msg:
        return "execution_error"
    if row_count is not None:
        try:
            return "success" if int(row_count) > 0 else "empty_result"
        except (ValueError, TypeError):
            pass
    return "success"


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------

def build_sql_df(spans: List[Dict], project_name: str) -> pd.DataFrame:
    """
    DataFrame for '3. sql_generation' spans.
    Keeps execution_error (status_code==ERROR) AND empty_result (row_count==0).
    """
    records = []
    for span in spans:
        user_query    = _extract_user_query(span)
        generated_sql = _extract_generated_sql(span)

        if not user_query:
            continue

        bucket = _sql_status_bucket(span)
        if bucket == "success":
            continue

        error_message_raw = _get_attr(span, "sql.error_message") or _get_attr(span, "error.message") or ""

        # Skip rate-limit / quota errors — expected throttle responses, not bugs
        if _is_rate_limit_error(error_message_raw) or _is_rate_limit_error(str(span.get("status_message", ""))):
            continue

        trace_id, span_id = _span_ids(span)
        records.append({
            "trace_id":      trace_id,
            "span_id":       span_id,
            "project_name":  project_name,
            "user_id":       _get_attr(span, "user.id"),
            "user_query":    user_query,
            "generated_sql": generated_sql,
            "status":        bucket,
            "row_count":     _get_attr(span, "sql.row_count", 0),
            "error_type":    _get_attr(span, "sql.error_type"),
            "error_message": error_message_raw,
            "llm_model":     _get_attr(span, "llm.model_name"),
            "timestamp":     span.get("start_time") or datetime.now(tz=timezone.utc).isoformat(),
            "collected_at":  datetime.now(tz=timezone.utc).isoformat(),
        })

    logger.info("  [%s] sql_generation: %d spans -> %d failures kept", project_name, len(spans), len(records))
    return pd.DataFrame(records)


def build_all_errors_df(span_name: str, spans: List[Dict], project_name: str) -> pd.DataFrame:
    """
    Generic error collector — works for ALL span types.

    Captures every span where:
      - status_code == 'ERROR'  (HTTP exceptions, Bedrock errors, quota errors, etc.)
      - OR sql.row_count == 0   (only meaningful on sql_generation spans)

    This gives a unified view of everything that went wrong, regardless of
    which stage of the pipeline failed.
    """
    records = []

    for span in spans:
        status_code = span.get("status_code", "OK")
        row_count   = _get_attr(span, "sql.row_count")

        # Include if span has an ERROR status
        is_error = (status_code == "ERROR")

        # Also flag sql_generation spans with 0 rows as a soft failure
        is_empty_sql = (
            span_name == SPAN_SQL_GENERATION
            and row_count is not None
            and str(row_count) == "0"
        )

        if not is_error and not is_empty_sql:
            continue

        # Determine failure category
        if is_empty_sql and not is_error:
            failure_category = "empty_result"
        else:
            failure_category = "error"

        # Pull best-available error message
        error_message = (
            _get_attr(span, "sql.error_message")
            or _get_attr(span, "error.message")
            or _get_attr(span, "exception.message")
            or _get_attr(span, "status_message")
            or span.get("status_message")
            or ""
        )

        # Error kind — Bedrock throttle, syntax error, quota, etc.
        error_type = (
            _get_attr(span, "sql.error_type")
            or _get_attr(span, "error.type")
            or _get_attr(span, "exception.type")
            or ""
        )

        # Skip rate-limit / quota errors — expected throttle responses, not bugs
        if _is_rate_limit_error(str(error_message)) or _is_rate_limit_error(str(span.get("status_message", ""))):
            continue

        user_query = _extract_user_query(span)

        trace_id, span_id = _span_ids(span)
        records.append({
            "trace_id":         trace_id,
            "span_id":          span_id,
            "project_name":     project_name,
            "user_id":          _get_attr(span, "user.id"),
            "span_name":        span_name,
            "failure_category": failure_category,
            "error_type":       str(error_type)[:200] if error_type else "",
            "error_message":    str(error_message)[:500] if error_message else "",
            "user_query":       user_query or "",
            "generated_sql":    _extract_generated_sql(span) if span_name == SPAN_SQL_GENERATION else None,
            "row_count":        row_count,
            "llm_model":        _get_attr(span, "llm.model_name"),
            "timestamp":        span.get("start_time") or datetime.now(tz=timezone.utc).isoformat(),
            "collected_at":     datetime.now(tz=timezone.utc).isoformat(),
        })

    logger.info(
        "  [%s] %s: %d spans -> %d errors captured",
        project_name, span_name, len(spans), len(records),
    )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Collection orchestration
# ---------------------------------------------------------------------------

def collect_all_projects() -> Dict[str, pd.DataFrame]:
    """
    For every Phoenix project (= one client database_name):
      1. Fetch each of the 5 span types defined in llm_inference.py
      2. sql_failures  — detailed SQL-focused report (sql_generation only)
      3. all_errors    — unified error table across ALL span types
    """
    projects = get_phoenix_projects()
    logger.info("Processing %d project(s): %s", len(projects), projects)

    sql_frames:   List[pd.DataFrame] = []
    error_frames: List[pd.DataFrame] = []

    for project in projects:
        logger.info("-" * 55)
        logger.info("Project: %s", project)

        for span_name in ALL_SPAN_NAMES:
            spans = fetch_spans(project, span_name)
            if not spans:
                continue

            # Detailed SQL failures table
            if span_name == SPAN_SQL_GENERATION:
                sql_df = build_sql_df(spans, project)
                if not sql_df.empty:
                    sql_frames.append(sql_df)

            # Unified error table — ALL span types
            err_df = build_all_errors_df(span_name, spans, project)
            if not err_df.empty:
                error_frames.append(err_df)

    sql_combined   = pd.concat(sql_frames,   ignore_index=True) if sql_frames   else pd.DataFrame()
    error_combined = pd.concat(error_frames, ignore_index=True) if error_frames else pd.DataFrame()

    logger.info("-" * 55)
    logger.info(
        "Totals -> SQL failures: %d  |  All-span errors: %d",
        len(sql_combined), len(error_combined),
    )

    return {"sql_failures": sql_combined, "all_errors": error_combined}


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def save_to_csv(
    sql_df: pd.DataFrame,
    all_errors_df: pd.DataFrame,
    tag: str | None = None,
) -> List[str]:
    """
    Write output files:
      - sql_error_{project}_{tag}.csv  -- one file per project, SQL failures only
      - all_errors_{tag}.csv           -- unified errors across all span types
      - report_summary_{tag}.csv       -- run metadata + counts
    """
    tag = tag or datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    saved: List[str] = []

    # 1. Per-project SQL failure CSVs  (sql_error_{project}_{timestamp}.csv)
    sql_by_project: Dict = {}
    if not sql_df.empty:
        for project_name, proj_df in sql_df.groupby("project_name"):
            safe_proj = project_name.replace(" ", "_").replace("/", "-")
            path = os.path.join(OUTPUT_DIR, f"sql_error_{safe_proj}_{tag}.csv")
            proj_df.to_csv(path, index=False)
            saved.append(path)
            sql_by_project[f"sql_errors_{safe_proj}"] = len(proj_df)
            logger.info(
                "Saved SQL errors [%s] -> %s  (%d rows)",
                project_name, path, len(proj_df),
            )

    # 2. All-span unified error table
    if not all_errors_df.empty:
        path = os.path.join(OUTPUT_DIR, f"all_errors_{tag}.csv")
        all_errors_df.to_csv(path, index=False)
        saved.append(path)
        logger.info("Saved all-span errors -> %s  (%d rows)", path, len(all_errors_df))

    # 3. Summary
    err_by_span: Dict = {}
    if not all_errors_df.empty:
        for span_name, grp in all_errors_df.groupby("span_name"):
            safe_key = span_name.replace(" ", "_").replace(".", "")
            err_by_span[f"errors_{safe_key}"] = len(grp)

    summary = {
        "tag":                  tag,
        "phoenix_base_url":     PHOENIX_BASE_URL,
        "lookback_days":        LOOKBACK_DAYS,
        "lookback_hours":       LOOKBACK_HOURS,
        "all_errors_total":     len(all_errors_df),
        "sql_failures_total":   len(sql_df),
        "sql_execution_errors": len(sql_df[sql_df["status"] == "execution_error"]) if not sql_df.empty else 0,
        "sql_empty_results":    len(sql_df[sql_df["status"] == "empty_result"])    if not sql_df.empty else 0,
        "projects":             ", ".join(all_errors_df["project_name"].unique()) if not all_errors_df.empty else "",
        **sql_by_project,
        **err_by_span,
    }
    summary_path = os.path.join(OUTPUT_DIR, f"report_summary_{tag}.csv")
    pd.DataFrame([summary]).to_csv(summary_path, index=False)
    saved.append(summary_path)

    return saved


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------

def main():
    banner = "=" * 65
    print(f"\n{banner}")
    print("  MALFORMED QUERY REPORT — Convai / Bedrock / Arize-Phoenix")
    print(banner)
    print(f"  Phoenix URL  : {PHOENIX_BASE_URL}")
    print(f"  Lookback     : {LOOKBACK_DAYS:.0f} day(s)  ({LOOKBACK_HOURS} hours)")
    print(f"  Output dir   : {OUTPUT_DIR}")
    print(f"  Auth         : {'Bearer ****' if PHOENIX_API_KEY else 'none'}")
    print(f"{banner}\n")

    # Connectivity check — use raw HTTP probe, not JSON parsing
    if not _health_check():
        print("⚠️  Could not reach Phoenix.  Make sure the server is running.")
        print(f"   URL: {PHOENIX_BASE_URL}\n")
        return None

    logger.info("✅  Phoenix is reachable at %s", PHOENIX_BASE_URL)

    results = collect_all_projects()
    tag = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    files = save_to_csv(results["sql_failures"], results["all_errors"], tag)

    # ── Report summary ───────────────────────────────────────────
    sql_df    = results["sql_failures"]
    errors_df = results["all_errors"]

    print(f"\n{banner}")
    print("  REPORT SUMMARY")
    print(banner)

    # ── All-span error breakdown ─────────────────────────────────
    print(f"\n❌  Total errors across all pipeline stages : {len(errors_df)}")
    if not errors_df.empty:
        print(f"     Projects: {', '.join(errors_df['project_name'].unique())}")
        print()
        print(f"     {'Span':<35}  {'Errors':>6}")
        print(f"     {'-'*35}  {'-'*6}")
        for span_name, grp in errors_df.groupby("span_name"):
            print(f"     {span_name:<35}  {len(grp):>6}")

        # Error type breakdown (if populated)
        err_types = errors_df["error_type"].replace("", None).dropna()
        if not err_types.empty:
            print("\n  Error types:")
            for etype, cnt in err_types.value_counts().head(10).items():
                print(f"       {cnt:>4}  {etype}")

    # ── SQL-specific detail ──────────────────────────────────────
    print(f"\n📊  SQL Generation Failures : {len(sql_df)}")
    if not sql_df.empty:
        print(f"     |- Execution errors  : {(sql_df['status'] == 'execution_error').sum()}")
        print(f"     |- Empty results     : {(sql_df['status'] == 'empty_result').sum()}")
        print(f"     `- Projects affected : {', '.join(sql_df['project_name'].unique())}")

        top_failing = sql_df["user_query"].replace("", None).dropna().value_counts().head(5)
        if not top_failing.empty:
            print("\n  Most repeated failing queries:")
            for q, cnt in top_failing.items():
                snippet = q[:70].replace("\n", " ")
                print(f"       [{cnt:>3}x]  '{snippet}...'")

    print(f"\n📁  Files written ({len(files)}):")
    for f in files:
        print(f"     {f}")
    print(f"\n{banner}\n")

    return results


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        results = main()

        # Per-project per-span breakdown
        if results and not results["all_errors"].empty:
            df = results["all_errors"]
            print("Per-project breakdown:")
            pivot = df.groupby(["project_name", "span_name"]).size().reset_index(name="count")
            for proj, grp in pivot.groupby("project_name"):
                print(f"  {proj}:")
                for _, row in grp.iterrows():
                    print(f"    {row['span_name']:<35}  {row['count']:>4} errors")
            print()

    except Exception as exc:
        logger.exception("Report generation failed: %s", exc)