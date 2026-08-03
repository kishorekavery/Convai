"""
Static validation of every SQL query in the knowledge base CSV.

The knowledge base is few-shot training material: whatever pattern appears here
is what the SQL model imitates. A malformed example does not just fail on its
own - it teaches the model to generate the same malformed shape. This script
checks each query for:

  1. Syntax        - does it parse as PostgreSQL (sqlglot)?
  2. Balance       - unbalanced parentheses / quotes
  3. Safety        - does it pass the app's own database.sql_safety.validate_sql?
  4. Prompt rules  - facility-code placeholder, LIMIT clause, single statement
  5. Metadata      - do the tables used match the "Table name" column?

Run:  python evals/check_kb_sql.py [--csv path] [--verbose]
"""

import argparse
import csv
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# validate_sql logs an INFO line per call; 280 of those would bury the report.
logging.disable(logging.INFO)

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402
from sqlglot.errors import ParseError, TokenError  # noqa: E402

from database.sql_safety import validate_sql  # noqa: E402

DEFAULT_CSV = (
    Path(__file__).resolve().parents[1]
    / "knowlege_base_query"
    / "knowledge_base_queries.csv"
)

# The SQL prompt (instruction 6) requires the placeholder, so that the router can
# substitute the caller's own facility codes. A hardcoded code in an example
# teaches the model to emit someone else's facility.
_PLACEHOLDER_RE = re.compile(r"'?<facilitycode>'?", re.IGNORECASE)
_FACILITY_COL_RE = re.compile(r"(\w*facility_?code\w*)\s+(in|=)\s*", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


def strip_sql(sql: str) -> str:
    """Normalise a KB cell into a single statement without a trailing semicolon."""
    sql = (sql or "").strip()
    sql = re.sub(r"^```(?:sql)?\s*|```$", "", sql, flags=re.IGNORECASE).strip()
    return sql.rstrip(";").strip()


def check_balance(sql: str):
    """Return a list of bracket/quote balance problems."""
    problems = []

    depth = 0
    for ch in sql:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                problems.append("unbalanced parentheses (extra closing)")
                break
    if depth > 0:
        problems.append(f"unbalanced parentheses ({depth} unclosed)")

    # Naive but effective for this corpus: an odd count of single quotes means a
    # string literal was never terminated.
    if sql.count("'") % 2 != 0:
        problems.append("odd number of single quotes (unterminated string literal)")
    if sql.count('"') % 2 != 0:
        problems.append("odd number of double quotes")

    return problems


def check_row(row: dict) -> dict:
    """Run every check against one KB row and return its findings."""
    raw = row.get("Query") or ""
    sql = strip_sql(raw)
    findings = defaultdict(list)

    if not sql:
        findings["empty"].append("no SQL in the Query column")
        return findings

    # Substitute the placeholder up front, exactly as the router does, so every
    # check below sees the statement as it will exist at runtime. Without this
    # sqlglot reads "<facilitycode>" as a less-than operator and reports a
    # syntax error on every correctly-authored row.
    runtime_sql = _PLACEHOLDER_RE.sub("'FAC-001'", sql)

    # --- 1. Syntax -------------------------------------------------------
    parsed = None
    try:
        statements = sqlglot.parse(runtime_sql, dialect="postgres")
        statements = [s for s in statements if s is not None]
        if not statements:
            findings["syntax"].append("parsed to nothing")
        elif len(statements) > 1:
            findings["multi_statement"].append(
                f"{len(statements)} statements in one example"
            )
            parsed = statements[0]
        else:
            parsed = statements[0]
    except (ParseError, TokenError) as e:
        findings["syntax"].append(str(e).splitlines()[0][:200])

    # --- 2. Balance ------------------------------------------------------
    for problem in check_balance(runtime_sql):
        findings["balance"].append(problem)

    # --- 3. The app's own safety validator -------------------------------
    try:
        validate_sql(runtime_sql)
    except ValueError as e:
        findings["rejected_by_validator"].append(str(e)[:200])

    # --- 4. Prompt-rule conformance --------------------------------------
    if not _PLACEHOLDER_RE.search(sql):
        m = _FACILITY_COL_RE.search(sql)
        if m:
            findings["hardcoded_facility"].append(
                f"filters on {m.group(1)} without the <facilitycode> placeholder"
            )
        else:
            findings["no_facility_filter"].append("no facility-code filter at all")
    elif re.search(r"<facilitycode>\s*'", sql) or re.search(r"'\s*<facilitycode>", sql):
        findings["placeholder_quoting"].append(
            "placeholder has unbalanced surrounding quotes"
        )

    if parsed is not None and isinstance(parsed, exp.Select):
        is_aggregate = bool(
            list(parsed.find_all(exp.AggFunc)) or parsed.args.get("group")
        )
        if not is_aggregate and not _LIMIT_RE.search(sql):
            findings["no_limit"].append("row-returning SELECT with no LIMIT")

    if parsed is not None and not isinstance(parsed, exp.Select):
        findings["not_select"].append(type(parsed).__name__)

    # --- 5. Metadata cross-check -----------------------------------------
    declared = {
        t.strip().lower()
        for t in (row.get("Table name") or "").replace(";", ",").split(",")
        if t.strip()
    }
    if parsed is not None:
        # CTE names are query-local aliases, not tables. Counting them would
        # report a mismatch on every correctly-authored WITH query.
        cte_names = {
            cte.alias_or_name.lower()
            for cte in parsed.find_all(exp.CTE)
            if cte.alias_or_name
        }
        used = {
            (t.name or "").lower()
            for t in parsed.find_all(exp.Table)
            if t.name and t.name.lower() not in cte_names
        }
        missing = used - declared
        if declared and missing:
            findings["metadata_mismatch"].append(
                f"uses {sorted(missing)} but 'Table name' says {sorted(declared)}"
            )
        if not declared:
            findings["no_table_metadata"].append("'Table name' column is empty")

    return findings


def load_from_csv(path: str) -> list:
    """Read the KB export and normalise it to the common row shape."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


DEFAULT_TABLE = "ai.knowledge_base_examples"


def load_from_db(table: str = DEFAULT_TABLE) -> list:
    """
    Read a knowledge-base table, normalised to the same row shape as the CSV so
    the checks below are identical for both sources.

    Read-only: a single SELECT, no writes.
    """
    import asyncio

    import asyncpg

    from config import (
        DB_HOST,
        DB_PASSWORD,
        DB_PORT,
        DB_USERNAME,
        KNOWLEDGEBASE_DATABASE_NAME,
    )

    async def fetch():
        conn = await asyncpg.connect(
            user=DB_USERNAME,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=KNOWLEDGEBASE_DATABASE_NAME,
        )
        try:
            # Table name is an identifier, so it cannot be a bind parameter.
            # It is validated against the catalog below rather than trusted.
            resolved = await conn.fetchval("SELECT to_regclass($1)::text", table)
            if resolved is None:
                raise SystemExit(f"No such table: {table}")
            return await conn.fetch(
                f"""
                SELECT kbe_id, kbe_reference_tables, kbe_user_input,
                       kbe_sql_query, kbe_user_response,
                       (kbe_user_input_embedding IS NULL) AS embedding_is_null
                FROM {resolved}
                ORDER BY kbe_id
                """
            )
        finally:
            await conn.close()

    rows = asyncio.run(fetch())
    return [
        {
            "S.no": str(r["kbe_id"]),
            "Questions": r["kbe_user_input"] or "",
            "Query": r["kbe_sql_query"] or "",
            "Table name": ",".join(r["kbe_reference_tables"] or []),
            "_reference_tables": r["kbe_reference_tables"],
            "_user_response": r["kbe_user_response"],
            "_embedding_is_null": r["embedding_is_null"],
        }
        for r in rows
    ]


def check_db_specific(rows: list) -> dict:
    """
    Checks that only apply to the live table - things the CSV cannot express.
    Returns category -> list of (id, question, message).
    """
    findings = defaultdict(list)

    for row in rows:
        rid, question = row["S.no"], (row["Questions"] or "")[:70]

        # A row with no embedding can never be retrieved by fetch_context's
        # ORDER BY kbe_user_input_embedding <=> $1 - it is dead weight.
        if row.get("_embedding_is_null"):
            findings["no_embedding"].append(
                (rid, question, "kbe_user_input_embedding IS NULL - never retrievable")
            )

        # fetch_context does set().union(*temp_table_names); a NULL here raises
        # TypeError, which then hits the RuntimeError(status_code=...) bug.
        refs = row.get("_reference_tables")
        if refs is None:
            findings["null_reference_tables"].append(
                (rid, question, "kbe_reference_tables IS NULL - crashes fetch_context")
            )
        elif len(refs) == 0:
            findings["empty_reference_tables"].append(
                (rid, question, "kbe_reference_tables is an empty array")
            )

        if not (row.get("_user_response") or "").strip():
            findings["no_user_response"].append(
                (rid, question, "kbe_user_response is empty - renders a blank example")
            )

        # The train/serve question: stored text carries a preamble that the
        # runtime query embedding never has.
        if "#User Facility#" in (row["Questions"] or ""):
            findings["boilerplate_in_embedded_text"].append(
                (rid, question, "kbe_user_input contains the #User Facility# preamble")
            )

    seen = Counter((r["Questions"] or "").strip().lower() for r in rows)
    for text, count in seen.items():
        if count > 1 and text:
            findings["duplicate_user_input"].append(
                ("-", text[:70], f"appears {count} times - competes with itself in top-k")
            )

    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["csv", "db"],
        default="csv",
        help="csv = the exported file; db = the live ai.knowledge_base_examples table",
    )
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"table to check when --source db (default: {DEFAULT_TABLE})",
    )
    parser.add_argument("--verbose", action="store_true", help="print every finding")
    parser.add_argument(
        "--only", help="show only this category (e.g. syntax, rejected_by_validator)"
    )
    args = parser.parse_args()

    if args.source == "db":
        rows = load_from_db(args.table)
        source_label = f"LIVE {args.table}"
    else:
        rows = load_from_csv(args.csv)
        source_label = args.csv

    totals = Counter()
    by_category = defaultdict(list)
    clean = 0

    for row in rows:
        findings = check_row(row)
        if not findings:
            clean += 1
        for category, messages in findings.items():
            totals[category] += 1
            for message in messages:
                by_category[category].append(
                    (row.get("S.no", "?"), (row.get("Questions") or "")[:70], message)
                )

    if args.source == "db":
        for category, entries in check_db_specific(rows).items():
            totals[category] += len(entries)
            by_category[category].extend(entries)

    print(f"Knowledge base: {source_label}")
    print(f"Rows checked:   {len(rows)}\n")
    print(f"{'CATEGORY':<26} {'ROWS':>6}   {'%':>6}")
    print("-" * 46)
    for category, count in totals.most_common():
        print(f"{category:<26} {count:>6}   {100 * count / len(rows):>5.1f}%")
    print("-" * 46)
    print(f"{'rows with no findings':<26} {clean:>6}   {100 * clean / len(rows):>5.1f}%")

    categories = [args.only] if args.only else list(totals)
    for category in categories:
        entries = by_category.get(category, [])
        if not entries:
            continue
        print(f"\n{'=' * 78}\n{category}  ({len(entries)})\n{'=' * 78}")
        shown = entries if (args.verbose or args.only) else entries[:6]
        for sno, question, message in shown:
            print(f"  #{sno:<5} {question}")
            print(f"         -> {message}")
        if len(entries) > len(shown):
            print(f"  ... {len(entries) - len(shown)} more (use --verbose)")


if __name__ == "__main__":
    main()
