-- Deduplicate public.user_ai_quota and prevent recurrence
-- =======================================================
--
-- WHY
-- The table has no unique constraint and no index on uaq_user_id. Measured
-- 2026-08-04 across all tenant databases: 9 user ids have more than one row.
--
--   coromandel   3      asianpaints  2      parry  2
--   yokohama     1      apollo       1
--
-- Two consequences, both live:
--
--   1. CHECK_IF_USER_QUOTA_LEFT selects rows WHERE uaq_used_count <
--      uaq_quota_limit and takes the first. A user with any duplicate row
--      under the limit can NEVER be rate-limited, whatever the other row says.
--
--   2. UPDATE_USER_QUOTA_USAGE is `WHERE uaq_user_id = $1`, so it updates
--      EVERY duplicate. Each request charges all of them.
--
-- Worked example - user 1278 (Kalaimamani) in coromandel:
--
--   uaq_id  created      uaq_used_count  uaq_quota_limit
--   3       2026-06-11        3,721,516        1,000,000
--   32      2026-07-31          170,906        1,000,000
--
-- Row 3 holds the full history since June and is 3.7x over the limit; row 32
-- was created in July and keeps the user unblocked.
--
-- ---------------------------------------------------------------------------
-- READ THIS BEFORE RUNNING
--
-- The merge rule below keeps the HIGHEST uaq_used_count, because the oldest
-- row holds the complete cumulative history. That is accurate accounting, and
-- it means any user whose true usage exceeds their limit is blocked the moment
-- this runs - user 1278 would start receiving 429s.
--
-- If that is not wanted, either
--   (a) raise uaq_quota_limit for the affected users first (see step 1), or
--   (b) change max() to min() in step 2, accepting that recorded usage is
--       under-reported.
--
-- Run per database. Dry run first: step 0 changes nothing.
-- ---------------------------------------------------------------------------

\set ON_ERROR_STOP on

-- STEP 0 - DRY RUN. Inspect before changing anything.
SELECT
    uaq_user_id,
    count(*)                        AS duplicate_rows,
    max(uaq_used_count)             AS will_keep,
    min(uaq_used_count)             AS will_discard,
    max(uaq_quota_limit)            AS quota_limit,
    max(uaq_used_count) >= max(uaq_quota_limit) AS blocked_after_merge
FROM public.user_ai_quota
GROUP BY uaq_user_id
HAVING count(*) > 1
ORDER BY max(uaq_used_count) DESC;

-- Stop here and review. Anything with blocked_after_merge = true will start
-- getting 429s. Raise those limits first if that is not acceptable:
--
--   UPDATE public.user_ai_quota SET uaq_quota_limit = <new limit>
--   WHERE uaq_user_id = <id>;

BEGIN;

-- STEP 1 - collapse duplicates onto the surviving row (lowest uaq_id).
WITH ranked AS (
    SELECT
        uaq_id,
        uaq_user_id,
        max(uaq_used_count) OVER (PARTITION BY uaq_user_id)  AS merged_used,
        max(uaq_quota_limit) OVER (PARTITION BY uaq_user_id) AS merged_limit,
        row_number()        OVER (PARTITION BY uaq_user_id ORDER BY uaq_id) AS rn
    FROM public.user_ai_quota
)
UPDATE public.user_ai_quota q
SET uaq_used_count  = r.merged_used,
    uaq_quota_limit = r.merged_limit,
    uaq_modified_time = now()
FROM ranked r
WHERE q.uaq_id = r.uaq_id
  AND r.rn = 1
  AND (q.uaq_used_count <> r.merged_used OR q.uaq_quota_limit <> r.merged_limit);

-- STEP 2 - delete the now-redundant duplicates.
DELETE FROM public.user_ai_quota q
USING (
    SELECT uaq_id,
           row_number() OVER (PARTITION BY uaq_user_id ORDER BY uaq_id) AS rn
    FROM public.user_ai_quota
) d
WHERE q.uaq_id = d.uaq_id
  AND d.rn > 1;

-- STEP 3 - make it impossible to recur. This also gives the table its first
-- index: today every quota check is a sequential scan, twice per request.
ALTER TABLE public.user_ai_quota
    ADD CONSTRAINT user_ai_quota_user_id_key UNIQUE (uaq_user_id);

COMMIT;

-- VERIFY - both should return zero rows.
SELECT uaq_user_id, count(*)
FROM public.user_ai_quota GROUP BY 1 HAVING count(*) > 1;

SELECT 'constraint missing' WHERE NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_ai_quota_user_id_key'
);
