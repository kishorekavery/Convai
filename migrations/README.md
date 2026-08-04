# Migrations

Database changes that cannot be made from application code. Both require
privileges the app role does not have, and both have consequences worth reading
before running.

Neither has been applied.

| File | What | Blast radius |
|---|---|---|
| `001_readonly_app_role.sql` | Grant the app role SELECT only | Per database; needs superuser |
| `002_dedupe_user_ai_quota.sql` | Merge duplicate quota rows, add a unique constraint | 5 databases; **blocks one over-limit user** |

## 001 - read-only app role

`conn.transaction(readonly=True)` in `execute_ai_generated_sql` guards the path
that runs model-generated SQL. It does not constrain the credentials, so any
other use of `DB_USERNAME` can still write. This makes the guarantee
unconditional.

Check first that nothing else needs write access with those credentials.
`dataprocessing/kbe_table_embedding_generation.py` writes embeddings to the
knowledge base, and the app updates `public.user_ai_quota` on every request -
the script keeps the quota grant.

## 002 - deduplicate the quota table

Nine user ids have more than one row, across five databases, and there is no
unique constraint. The check finds any row under the limit, so those users
cannot be rate-limited; the update matches every duplicate, so each request
charges all of them.

**The merge keeps the highest usage count**, which is the accurate figure - and
means user 1278 in `coromandel` (3,721,516 against a 1,000,000 limit) is blocked
the moment it runs. Step 0 is a dry run listing exactly who that affects. Raise
their limit first, or switch `max()` to `min()`, if that is not wanted.

## Running

```sh
psql -h "$DB_HOST" -U postgres -d <database> \
     -v app_role="$DB_USERNAME" -f migrations/001_readonly_app_role.sql

# 002: run step 0 alone first, review, then the rest.
psql -h "$DB_HOST" -U postgres -d <database> -f migrations/002_dedupe_user_ai_quota.sql
```

Databases with a `user_ai_quota` table as of 2026-08-04: `coromandel`,
`asianpaints`, `parry`, `yokohama`, `apollo`.
