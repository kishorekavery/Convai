"""
Build ai.knowlegebaseexamples_new - a corrected copy of ai.knowledge_base_examples.

The source table is never modified. The new table is created with
``LIKE ... INCLUDING ALL`` so the structure, the vector column and its
dimension, and the primary key are reproduced exactly, then every row is copied
(embeddings included - kbe_user_input is not changed, so the vectors stay valid
and nothing needs re-embedding).

Three classes of correction are applied:

  1. Syntax repairs   - unclosed subquery parenthesis, missing AND
  2. LIMIT            - append LIMIT 50 to row-returning SELECTs that lack one
  3. Reference tables - regenerate kbe_reference_tables from the SQL itself

Every repair is verified: the candidate SQL must parse under sqlglot AND pass
database.sql_safety.validate_sql. A repair that fails verification is discarded
and the original row is copied unchanged, so this can only ever improve a row.

Run:
    python evals/build_kb_new_table.py             # dry run, prints the diff
    python evals/build_kb_new_table.py --apply     # create and populate
"""

import argparse
import asyncio
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402
from sqlglot.errors import ParseError, TokenError  # noqa: E402

from config import (  # noqa: E402
    DB_HOST,
    DB_PASSWORD,
    DB_PORT,
    DB_USERNAME,
    KNOWLEDGEBASE_DATABASE_NAME,
)
from database.sql_safety import validate_sql  # noqa: E402

logging.disable(logging.INFO)

SOURCE_TABLE = "ai.knowledge_base_examples"
TARGET_TABLE = "ai.knowlegebaseexamples_new"
PAGE_SIZE = 50

_PLACEHOLDER_RE = re.compile(r"'?<facilitycode>'?", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_FACILITY_IN_RE = re.compile(r"in\s*\(\s*<facilitycode>\s*\)", re.IGNORECASE)

# Keywords that may legitimately follow a WHERE predicate. Anything else
# following the facility clause means a connector (AND) was dropped.
_FOLLOWERS = {
    "and", "or", "group", "order", "limit", "offset", "having",
    "union", "intersect", "except", "fetch", "window", ")", ";",
}


def runtime_form(sql: str) -> str:
    """The statement as it exists after the router substitutes facility codes."""
    return _PLACEHOLDER_RE.sub("'FAC-001'", sql)


def parses(sql: str):
    """Return the parsed statement, or None if it will not parse as PostgreSQL."""
    try:
        statements = [s for s in sqlglot.parse(runtime_form(sql), dialect="postgres") if s]
    except (ParseError, TokenError):
        return None
    return statements[0] if len(statements) == 1 else None


def is_safe(sql: str) -> bool:
    """Whether the statement clears the app's own safety validator."""
    try:
        validate_sql(runtime_form(sql))
        return True
    except ValueError:
        return False


def verified(sql: str) -> bool:
    return parses(sql) is not None and is_safe(sql)


def split_trailing_semicolon(sql: str):
    """Return (body, suffix) so a repair can be inserted before the semicolon."""
    stripped = sql.rstrip()
    if stripped.endswith(";"):
        return stripped[:-1].rstrip(), ";"
    return stripped, ""


# --------------------------------------------------------------------------
# Repair 1: syntax
# --------------------------------------------------------------------------
def repair_syntax(sql: str):
    """
    Try to repair a query that does not parse. Returns (fixed_sql, note) or None.

    Two defects appear in this corpus, both around the facility clause:
      a) a subquery opened with `in (SELECT ...` is never closed
      b) the connector between the facility clause and the next predicate is missing
    """
    body, suffix = split_trailing_semicolon(sql)

    matches = list(_FACILITY_IN_RE.finditer(body))
    if not matches:
        return None
    last = matches[-1]

    # (a) close the unclosed subquery immediately after the facility clause
    candidate = f"{body[: last.end()]}){body[last.end():]}{suffix}"
    if verified(candidate):
        return candidate, "added the missing closing parenthesis"

    # (b) insert the dropped connector before the next predicate
    tail = body[last.end():].lstrip()
    next_word = re.match(r"[\w;)]+", tail)
    if next_word and next_word.group(0).lower() not in _FOLLOWERS:
        candidate = f"{body[: last.end()]} AND {tail}{suffix}"
        if verified(candidate):
            return candidate, "inserted the missing AND connector"

    return None


# --------------------------------------------------------------------------
# Repair 2: LIMIT
# --------------------------------------------------------------------------
def needs_limit(parsed) -> bool:
    """
    True for a SELECT that returns a row list and has no LIMIT.

    Aggregates without GROUP BY return a single row, so a LIMIT would be noise.
    GROUP BY queries are left alone here too - they are a judgement call about
    how many groups are acceptable, not a mechanical fix.
    """
    if not isinstance(parsed, exp.Select):
        return False
    if parsed.args.get("group") or list(parsed.find_all(exp.AggFunc)):
        return False
    return True


def repair_limit(sql: str, parsed):
    """Append LIMIT 50 to a row-returning SELECT. Returns (fixed_sql, note) or None."""
    if _LIMIT_RE.search(sql) or not needs_limit(parsed):
        return None

    body, suffix = split_trailing_semicolon(sql)
    candidate = f"{body} LIMIT {PAGE_SIZE}{suffix}"
    if verified(candidate):
        return candidate, f"appended LIMIT {PAGE_SIZE}"
    return None


# --------------------------------------------------------------------------
# Repair 3: reference tables
# --------------------------------------------------------------------------
def extract_tables(parsed) -> list:
    """
    Real tables referenced by the statement, schema prefix stripped.

    CTE names are excluded - they are query-local aliases, not tables, and
    feeding one to fetch_context would send it looking for a schema that does
    not exist.
    """
    if parsed is None:
        return []

    cte_names = {
        cte.alias_or_name.lower()
        for cte in parsed.find_all(exp.CTE)
        if cte.alias_or_name
    }

    tables = {
        t.name.lower()
        for t in parsed.find_all(exp.Table)
        if t.name and t.name.lower() not in cte_names
    }
    return sorted(tables)


# --------------------------------------------------------------------------
def plan_row(row: dict) -> dict:
    """Compute every change for one row without touching the database."""
    original = row["kbe_sql_query"] or ""
    sql = original
    notes = []

    parsed = parses(sql)

    if parsed is None:
        repaired = repair_syntax(sql)
        if repaired:
            sql, note = repaired
            notes.append(note)
            parsed = parses(sql)
        else:
            notes.append("UNREPAIRED: still does not parse - copied unchanged")

    if parsed is not None:
        repaired = repair_limit(sql, parsed)
        if repaired:
            sql, note = repaired
            notes.append(note)
            parsed = parses(sql)

    # Reference tables are corrected ADDITIVELY: any table the SQL actually
    # uses is added, but nothing an author listed is removed. A missing entry
    # is a real defect - fetch_context then never loads that table's schema and
    # the model invents its columns. An extra entry only costs prompt size, and
    # may be deliberate context, so removing it is not a call to make
    # automatically. `would_remove` reports what a strict rebuild would drop.
    raw_tables = list(row["kbe_reference_tables"] or [])
    # Case-normalise first. fetch_context looks tables up with an exact
    # `table_name = $1` match, and PostgreSQL folds unquoted identifiers to
    # lowercase - so a stored 'Calibrationmasterdetails' matches nothing and
    # that table's schema is silently never fetched.
    old_tables = sorted({t.lower() for t in raw_tables})
    case_fixed = sorted(t for t in raw_tables if t != t.lower())

    used_tables = extract_tables(parsed)
    new_tables = sorted(set(old_tables) | set(used_tables)) if used_tables else old_tables
    would_remove = sorted(set(old_tables) - set(used_tables)) if used_tables else []

    if case_fixed:
        notes.append(f"case-normalised {case_fixed} (matched no table as stored)")
    added = sorted(set(new_tables) - set(old_tables))
    if added:
        notes.append(f"reference_tables += {added}  (was {sorted(raw_tables)})")

    return {
        "kbe_id": row["kbe_id"],
        "question": (row["kbe_user_input"] or "")[:66],
        "sql": sql,
        "sql_changed": sql != original,
        "tables": new_tables,
        "tables_changed": new_tables != sorted(raw_tables),
        "would_remove": would_remove,
        "notes": notes,
        "still_broken": parses(sql) is None,
        "unsafe": not is_safe(sql),
    }


async def run(apply: bool):
    conn = await asyncpg.connect(
        user=DB_USERNAME,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=KNOWLEDGEBASE_DATABASE_NAME,
    )
    try:
        rows = await conn.fetch(
            f"""SELECT kbe_id, kbe_reference_tables, kbe_user_input,
                       kbe_sql_query, kbe_user_response
                FROM {SOURCE_TABLE} ORDER BY kbe_id"""
        )
        plans = [plan_row(dict(r)) for r in rows]

        counts = Counter()
        for p in plans:
            counts["sql rewritten"] += p["sql_changed"]
            counts["reference_tables rewritten"] += p["tables_changed"]
            counts["still unparseable"] += p["still_broken"]
            counts["rejected by validate_sql"] += p["unsafe"]
            for note in p["notes"]:
                if note.startswith("appended LIMIT"):
                    counts["  - LIMIT appended"] += 1
                elif note.startswith("added the missing"):
                    counts["  - parenthesis repaired"] += 1
                elif note.startswith("inserted the missing"):
                    counts["  - AND inserted"] += 1

        print(f"source: {SOURCE_TABLE}   rows: {len(rows)}")
        print(f"target: {TARGET_TABLE}\n")
        for label, n in counts.items():
            print(f"  {label:<34} {n}")

        print("\n--- syntax repairs ---")
        for p in plans:
            for note in p["notes"]:
                if "parenthesis" in note or "AND connector" in note or "UNREPAIRED" in note:
                    print(f"  #{p['kbe_id']}  {p['question']}")
                    print(f"          {note}")

        changed = [p for p in plans if p["tables_changed"]]
        print(f"\n--- reference_tables additions ({len(changed)} rows) ---")
        for p in changed[:12]:
            print(f"  #{p['kbe_id']}  {p['question']}")
            for note in p["notes"]:
                if note.startswith("reference_tables") or note.startswith("case-normalised"):
                    print(f"          {note}")
        if len(changed) > 12:
            print(f"  ... {len(changed) - 12} more")

        removable = [p for p in plans if p["would_remove"]]
        print(
            f"\n--- not applied: {len(removable)} rows list tables their SQL never uses ---"
        )
        print("    (left in place; they only cost prompt size. Review manually.)")
        for p in removable[:5]:
            print(f"  #{p['kbe_id']}  unused: {p['would_remove']}")
        if len(removable) > 5:
            print(f"  ... {len(removable) - 5} more")

        if not apply:
            print("\nDRY RUN - nothing written. Re-run with --apply to create the table.")
            return

        exists = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", TARGET_TABLE
        )
        if exists:
            print(f"\nABORTED: {TARGET_TABLE} already exists. Drop it first if you meant to rebuild.")
            return

        async with conn.transaction():
            # LIKE ... INCLUDING ALL reproduces the vector column and its
            # dimension exactly, so embeddings copy across unchanged.
            await conn.execute(
                f"CREATE TABLE {TARGET_TABLE} (LIKE {SOURCE_TABLE} INCLUDING ALL)"
            )
            await conn.execute(
                f"INSERT INTO {TARGET_TABLE} SELECT * FROM {SOURCE_TABLE}"
            )
            for p in plans:
                if p["sql_changed"] or p["tables_changed"]:
                    await conn.execute(
                        f"""UPDATE {TARGET_TABLE}
                            SET kbe_sql_query = $1,
                                kbe_reference_tables = $2,
                                kbe_modified_time = now()
                            WHERE kbe_id = $3""",
                        p["sql"],
                        p["tables"],
                        p["kbe_id"],
                    )

        n = await conn.fetchval(f"SELECT count(*) FROM {TARGET_TABLE}")
        embedded = await conn.fetchval(
            f"SELECT count(*) FROM {TARGET_TABLE} WHERE kbe_user_input_embedding IS NOT NULL"
        )
        print(f"\nCREATED {TARGET_TABLE}: {n} rows, {embedded} with embeddings intact.")

    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually create the table")
    args = parser.parse_args()
    asyncio.run(run(args.apply))
