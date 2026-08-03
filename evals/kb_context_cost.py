"""
What does KB_CONTEXT_LIMIT actually cost, and what does each extra example buy?

Two halves:

  1. COST (offline) - measures the real token weight that k few-shot examples
     add to the SQL-generation and final-response prompts, using the project's
     own tokenizer.json rather than a chars/4 estimate.

  2. VALUE (needs the database) - a leave-one-out neighbour study over the
     stored embeddings. For every example we ask: if this had been the user's
     question, how relevant is the example returned at rank 1, 2, ... k?
     Relevance is proxied by whether the neighbour references any of the same
     tables, since an example over unrelated tables cannot help the model write
     this query. No Bedrock calls - existing vectors are reused.

Run:  python evals/kb_context_cost.py [--k 1 3 5 10 15] [--skip-db]
"""

import argparse
import asyncio
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE = "ai.knowlegebaseexamples_new"


def load_tokenizer():
    """The Llama tokenizer shipped with the repo, or None if unavailable."""
    try:
        from tokenizers import Tokenizer

        path = ROOT / "tokenizer.json"
        if not path.exists():
            return None
        return Tokenizer.from_file(str(path))
    except Exception:
        return None


def count_tokens(tok, text: str) -> int:
    if tok is None:
        return len(text) // 4  # rough fallback
    return len(tok.encode(text).ids)


def measure_cost(rows, ks, tok):
    """
    Token cost of the two few-shot blocks that fetch_context builds, at each k.

    Mirrors the exact strings assembled in database/db_queries.py::fetch_context.
    """
    print("=" * 74)
    print("COST - tokens added to each request by k few-shot examples")
    print("=" * 74)

    sql_blocks, resp_blocks = [], []
    for i, r in enumerate(rows, 1):
        q = r["question"]
        sql_blocks.append(f"Example {i} - \nUser: {q}\nAssistant: {r['sql']}\n\n")
        resp_blocks.append(f"Example {i} - \nUser: {q}\nAssistant: {r['response']}\n\n")

    # Average over the corpus: cost of a randomly-drawn set of k examples.
    per_sql = statistics.mean(count_tokens(tok, b) for b in sql_blocks)
    per_resp = statistics.mean(count_tokens(tok, b) for b in resp_blocks)

    print(f"\navg tokens per SQL example:            {per_sql:7.0f}")
    print(f"avg tokens per response example:       {per_resp:7.0f}")
    print(f"\n{'k':>3}  {'sql block':>10}  {'resp block':>11}  {'both':>8}  {'vs k=10':>9}")
    print("-" * 52)
    base = None
    for k in ks:
        s, rr = per_sql * k, per_resp * k
        total = s + rr
        if k == 10:
            base = total
        print(f"{k:>3}  {s:>10.0f}  {rr:>11.0f}  {total:>8.0f}", end="")
        print(f"  {(total - base) / base * 100:>+8.0f}%" if base else "")
    if base:
        print(f"\n(k=10 is the current KB_CONTEXT_LIMIT: ~{base:.0f} tokens of few-shot per request,")
        print(" on top of the table schemas, which are usually the larger block.)")


async def measure_value(table, ks):
    """
    Leave-one-out: for each row, use its stored embedding as the query and look
    at what comes back at each rank. Reports how fast topical relevance decays.
    """
    import asyncpg

    from config import (
        DB_HOST,
        DB_PASSWORD,
        DB_PORT,
        DB_USERNAME,
        KNOWLEDGEBASE_DATABASE_NAME,
    )

    conn = await asyncpg.connect(
        user=DB_USERNAME,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=KNOWLEDGEBASE_DATABASE_NAME,
    )
    try:
        resolved = await conn.fetchval("SELECT to_regclass($1)::text", table)
        if resolved is None:
            raise SystemExit(f"No such table: {table}")

        rows = await conn.fetch(
            f"SELECT kbe_id, kbe_reference_tables FROM {resolved} ORDER BY kbe_id"
        )
        tables_by_id = {r["kbe_id"]: set(r["kbe_reference_tables"] or []) for r in rows}
        max_k = max(ks)

        # distance[rank] and overlap[rank], accumulated across every row
        dist = {r: [] for r in range(1, max_k + 1)}
        overlap = {r: [] for r in range(1, max_k + 1)}

        for r in rows:
            neighbours = await conn.fetch(
                f"""
                SELECT kbe_id,
                       kbe_user_input_embedding <=> (
                           SELECT kbe_user_input_embedding FROM {resolved} WHERE kbe_id = $1
                       ) AS distance
                FROM {resolved}
                WHERE kbe_id <> $1
                ORDER BY 2
                LIMIT $2
                """,
                r["kbe_id"],
                max_k,
            )
            mine = tables_by_id[r["kbe_id"]]
            for rank, n in enumerate(neighbours, 1):
                dist[rank].append(float(n["distance"]))
                dist_tables = tables_by_id.get(n["kbe_id"], set())
                overlap[rank].append(1.0 if (mine & dist_tables) else 0.0)

        print("\n" + "=" * 74)
        print(f"VALUE - what rank k actually returns   ({resolved}, {len(rows)} rows)")
        print("=" * 74)
        print(f"\n{'rank':>5}  {'cosine dist':>12}  {'shares a table':>15}")
        print("-" * 38)
        for rank in range(1, max_k + 1):
            print(
                f"{rank:>5}  {statistics.mean(dist[rank]):>12.4f}"
                f"  {100 * statistics.mean(overlap[rank]):>14.0f}%"
            )

        print(f"\n{'k':>3}  {'mean overlap over top-k':>24}")
        print("-" * 30)
        for k in ks:
            vals = [v for rank in range(1, k + 1) for v in overlap[rank]]
            print(f"{k:>3}  {100 * statistics.mean(vals):>23.0f}%")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10, 15])
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    csv_path = ROOT / "knowlege_base_query" / "knowledge_base_queries.csv"
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        rows = [
            {
                "question": (r.get("Questions") or "").strip(),
                "sql": (r.get("Query") or "").strip(),
                "response": (r.get("Result & Answer Format") or "").strip(),
            }
            for r in csv.DictReader(f)
        ]

    tok = load_tokenizer()
    print(f"tokenizer: {'tokenizer.json (exact)' if tok else 'NOT FOUND - using chars/4'}\n")
    measure_cost(rows, sorted(args.k), tok)

    if not args.skip_db:
        try:
            asyncio.run(measure_value(args.table, sorted(args.k)))
        except Exception as e:
            print(f"\n(VALUE half skipped - database unavailable: {type(e).__name__})")


if __name__ == "__main__":
    main()
