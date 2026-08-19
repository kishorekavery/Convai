import re
from collections import defaultdict
from fastapi import HTTPException, status
from asyncpg import (
    exceptions,
    ConnectionFailureError,
    Pool,
    PostgresError,
)
from typing import List
from time import time
from tabulate import tabulate

## Internal Packages
from config import get_logger
from config import new_error_reference, client_error_detail
from config import (
    KB_CONTEXT_LIMIT,
    CONTEXT_LIMIT,
    DATA_SCHEMA,
    KNOWLEDGEBASE_DATABASE_NAME,
    USER_DETAILS_SCHEMA,
    AI_SQL_STATEMENT_TIMEOUT_MS,
    AI_SQL_COUNT_TIMEOUT_MS,
)
from database import get_pool
from database.schema_cache import table_schema_cache
from database.sql_safety import validate_identifier
from config import KNOWLEDGEBASE_TABLE

_KB_TABLE = validate_identifier(KNOWLEDGEBASE_TABLE, label="knowledge base table")
_DATA_SCHEMA = validate_identifier(DATA_SCHEMA, label="data schema")
_USER_DETAILS_SCHEMA = validate_identifier(
    USER_DETAILS_SCHEMA, label="user details schema"
)

logging = get_logger(__name__)

CHECK_IF_USER_QUOTA_LIMIT_EXISTS = """
SELECT user_id FROM public.ask_ai_master
WHERE user_id = $1
"""
 
CHECK_IF_USER_QUOTA_LEFT = """
SELECT token_used_count, monthly_token_limit FROM public.ask_ai_master am
join ask_ai_tokenquota at on am.user_id=at.user_id
WHERE am.user_id = $1
  AND token_used_count < monthly_token_limit;
"""

UPDATE_USER_QUOTA_USAGE = """
UPDATE public.ask_ai_tokenquota
SET
    token_used_count = token_used_count + $2,
 
    attributes = jsonb_set(
        CASE
            WHEN COALESCE(attributes, '{}'::jsonb) ? 'models'
            THEN COALESCE(attributes, '{}'::jsonb)
            ELSE jsonb_build_object('models', '{}'::jsonb)
        END,
 
        ARRAY['models', $3],
 
        jsonb_build_object(
            'input_tokens',
                COALESCE((attributes #>> ARRAY['models', $3, 'input_tokens'])::BIGINT, 0) + $4,
            'output_tokens',
                COALESCE((attributes #>> ARRAY['models', $3, 'output_tokens'])::BIGINT, 0) + $5,
            'total_tokens',
                COALESCE((attributes #>> ARRAY['models', $3, 'total_tokens'])::BIGINT, 0) + $2
        ),
 
        true
    )
 
WHERE user_id = $1;
"""

async def update_user_quota(conn, user_id: int, token_usage: dict):
    """
    Executes UPDATE_USER_QUOTA_USAGE for each model tracked in token_usage.
    If no row exists for user_id in public.user_ai_quota, inserts an initial row.
    """
    if not token_usage or not isinstance(token_usage, dict):
        return

    for model_id, counts in token_usage.items():
        if not isinstance(counts, dict):
            continue
        p_tokens = int(counts.get("prompt_tokens", 0) or 0)
        c_tokens = int(counts.get("completion_tokens", 0) or 0)
        t_tokens = int(counts.get("total_tokens", 0) or (p_tokens + c_tokens))
        model_key = str(model_id).strip()
        if t_tokens > 0 and model_key:
            res = await conn.execute(
                UPDATE_USER_QUOTA_USAGE,
                int(user_id),
                t_tokens,
                model_key,
                p_tokens,
                c_tokens,
            )
            if res == "UPDATE 0":
                await conn.execute(
                    """
                    INSERT INTO public.user_ai_quota (
                        uaq_user_id, uaq_used_count, uaq_metadata
                    ) VALUES (
                        $1, $2,
                        jsonb_build_object(
                            'models', jsonb_build_object(
                                $3, jsonb_build_object(
                                    'input_tokens', $4::BIGINT,
                                    'output_tokens', $5::BIGINT,
                                    'total_tokens', $2::BIGINT
                                )
                            )
                        )
                    );
                    """,
                    int(user_id),
                    t_tokens,
                    model_key,
                    p_tokens,
                    c_tokens,
                )


def format_schema(records):
    lines = []
    for r in records:
        lines.append(f"- {r['column_name']}: {r['data_type']}")
    return "\n".join(lines)


async def fetch_table_schemas(
    database_name: str, table_names: List[str], pool: Pool
) -> dict:
    """
    Return {table_name: formatted schema} for ``table_names``.
    """
    if not table_names:
        return {}

    resolved = {}
    missing = []
    for name in table_names:
        cached = table_schema_cache.get(database_name, name)
        if cached is None:
            missing.append(name)
        else:
            resolved[name] = cached

    if missing:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT LOWER(table_name) AS table_name, column_name, data_type
                   FROM information_schema.columns
                   WHERE LOWER(table_schema) IN (LOWER($1), 'public', 'ai') AND LOWER(table_name) = ANY($2)
                   ORDER BY table_name, ordinal_position;
                """,
                DATA_SCHEMA,
                missing,
            )

        # ORDER BY ordinal_position above means columns are listed in their
        columns_by_table = defaultdict(list)
        for row in rows:
            columns_by_table[row["table_name"].lower()].append(row)

        for name in missing:
            columns = columns_by_table.get(name.lower(), [])
            if not columns:
                logging.warning(
                    "No columns found for table '%s' in schema '%s' of database '%s'.",
                    name,
                    DATA_SCHEMA,
                    database_name,
                )
            formatted = format_schema(columns)
            table_schema_cache.put(database_name, name, formatted)
            resolved[name] = formatted

        logging.info(
            "Table schemas: %s served from cache, %s fetched in 1 query.",
            len(table_names) - len(missing),
            len(missing),
        )
    else:
        logging.info(
            "Table schemas: all %s served from cache, 0 queries.", len(table_names)
        )

    return resolved


async def fetch_context(
    embedded_user_input: str,
    tableschema_dbconnection_pool: Pool,
    database_name: str = "",
) -> str:
    """
    Fetch context asynchronously from the database based on the embedded user input.
    """

    knowledgebase_dbconnection_pool = await get_pool(KNOWLEDGEBASE_DATABASE_NAME)

    try:
        start_time = time()
        async with knowledgebase_dbconnection_pool.acquire() as conn:
            # await conn.execute(f"SET search_path TO {KNOWLEDGEBASE_SCHEMA_NAME}")
            rows = await conn.fetch(
                f"""SELECT kbe_id, kbe_reference_tables, kbe_user_input, kbe_sql_query, kbe_user_response
                                    FROM {_KB_TABLE} kbe
                                    ORDER BY kbe_user_input_embedding <=> $1
                                    LIMIT $2;
                                    """,
                embedded_user_input,
                KB_CONTEXT_LIMIT,
            )

        # If no context is available return empty string
        if not rows:
            processing_time = time() - start_time
            logging.info("No Context Available. Processing Time: %s", processing_time)
            return "", "", ""

        context_for_sql_generation: str = ""
        context_for_user_response: str = ""
        temp_table_names: List[List] = []
        table_schema = ""
        n = 1

        for row in rows:
            (
                id,
                table_name,
                user_input_example,
                sql_query_example,
                user_response_example,
            ) = row

            temp_table_names.append(table_name)

            context_for_sql_generation += f"Example {n} - \nUser: {user_input_example}\nAssistant: {sql_query_example}\n\n"

            if n <= CONTEXT_LIMIT:
                context_for_user_response += f"Example {n} - \nUser: {user_input_example}\nAssistant: {user_response_example}\n\n"

            n += 1

        # sorted(), not list(set(...)): set iteration order for strings depends
        table_names = sorted(
            {
                name.lower()
                for names in temp_table_names
                if names
                for name in names
                if name
            }
        )

        schemas = await fetch_table_schemas(
            database_name, table_names, tableschema_dbconnection_pool
        )
        table_schema = "".join(
            f"Table Schema of {name}:\n\n{schemas.get(name, '')}\n\n"
            for name in table_names
        )

        processing_time = time() - start_time
        logging.info("Context Retrieval Time: %s", processing_time)

        return table_schema, context_for_sql_generation, context_for_user_response

    except ConnectionFailureError as conn_err:
        logging.error("Database connection pool error: %s", conn_err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to acquire database connection.",
        )

    except Exception as e:
        logging.error("An unexpected error occurred in fetch_context: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while fetching context.",
        )


def format_sql_query(sql, facm_code):
    """
    Remove triple backticks and optional 'sql' label from the query string.
    """
    sql = re.sub(r"^```(sql)?\s*|```$", "", sql.strip(), flags=re.IGNORECASE)

    facm_code_str = ",".join(f"'{code}'" for code in facm_code)

    # sql_with_facility_code = sql.replace("'<facilitycode>'", facm_code_str)
    sql_with_facility_code = re.sub(
        r"'<facilitycode>'|<facilitycode>", facm_code_str, sql
    )

    return sql_with_facility_code


def clean_sql_query(sql):
    """
    Remove triple backticks and optional 'sql' label from the query string.
    """
    return re.sub(r"^```(sql)?\s*|```$", "", sql.strip(), flags=re.IGNORECASE)


async def execute_ai_generated_sql(sql: str, pool: Pool):
    """
    Execute SQL query asynchronously and return rows.

    Args:
        pool (asyncpg.Pool): Database connection pool.
        sql (str): SQL query to execute.

    Returns:
        List[Tuple]: A list of tuples containing query results.
    """
    try:
        start_time = time()

        async with pool.acquire() as conn:
            # Read-only transaction: the SQL was generated by a language model
            async with conn.transaction(readonly=True):
                # SET LOCAL, not SET: a plain SET persists on the pooled
                await conn.execute(f"SET LOCAL search_path TO {_DATA_SCHEMA}")
                await conn.execute(
                    f"SET LOCAL statement_timeout = {AI_SQL_STATEMENT_TIMEOUT_MS}"
                )
                rows = await conn.fetch(sql)

        processing_time = time() - start_time
        logging.info("AI Query Execution Time: %s", processing_time)

        return rows

    except exceptions.QueryCanceledError:
        # The statement_timeout fired. Typically a dropped join condition: the
        logging.error(
            "AI generated SQL exceeded the %s ms statement timeout. SQL: %s",
            AI_SQL_STATEMENT_TIMEOUT_MS,
            sql,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="That query took too long to run. Please narrow your request "
            "(for example a shorter time range, a specific facility, or a count "
            "instead of a full list).",
        )

    except exceptions.ReadOnlySQLTransactionError:
        # The regex validator missed a write and Postgres caught it.
        logging.error(
            "AI generated SQL attempted a write inside the read-only transaction. SQL: %s",
            sql,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The AI generated a potentially unsafe SQL query.",
        )

    except exceptions.UndefinedColumnError as e:
        ref = new_error_reference()
        logging.error(
            "[%s] Undefined column '%s' in AI-generated SQL: %s", ref, e.column_name, sql
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=client_error_detail(
                "The generated query referenced a column that does not exist.", ref
            ),
        )

    except PostgresError as e:
        ref = new_error_reference()
        logging.error("[%s] SQL execution error: %s. SQL: %s", ref, e, sql)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=client_error_detail(
                "The generated query could not be executed.", ref
            ),
        )

    except Exception:
        ref = new_error_reference()
        logging.error(
            "[%s] Unexpected error executing AI-generated SQL", ref, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=client_error_detail("Could not run that query.", ref),
        )


async def execute_count_query(count_sql: str, pool: Pool):
    """
    Run a COUNT(*) wrapper over an AI-generated query and return the scalar.

    Kept separate from execute_ai_generated_sql because the caller treats a
    failure here as non-fatal - losing the count only costs the "more pages"
    hint, not the answer - so this returns None instead of raising an
    HTTPException. It sets the same search_path as the query being counted;
    without that the count can resolve different tables than the main query.

    This query has had its LIMIT stripped, so it scans the entire result set by
    design - which makes it the worst amplifier of a bad generation. Hence the
    tighter timeout: if counting a cartesian product is going to be slow, give
    up quickly and drop the hint rather than hold a connection.
    """
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL search_path TO {_DATA_SCHEMA}")
            await conn.execute(
                f"SET LOCAL statement_timeout = {AI_SQL_COUNT_TIMEOUT_MS}"
            )
            return await conn.fetchval(count_sql)


async def fetch_user_details(user_id: str, pool: Pool):

    try:
        usr_id = int(user_id) if user_id.isdigit() else None

        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL search_path TO {_USER_DETAILS_SCHEMA}")
                raw_user_details = await conn.fetch(
                    "SELECT usr_id, usr_name, usr_personalcode  FROM users_m WHERE usr_id = $1;",
                    usr_id,
                )

        if raw_user_details:
            ## Convert each asyncpg Record to a dictionary
            user_details_rows = [dict(row) for row in raw_user_details]
            formated_user_details = tabulate(
                user_details_rows, headers="keys", tablefmt="simple"
            )
        else:
            formated_user_details = "No user details found for the given user ID."

        # print(formated_user_details)
        return formated_user_details

    except exceptions.UndefinedColumnError as e:
        # This query is written by us, not the model, so a missing column means
        ref = new_error_reference()
        logging.error(
            "[%s] Undefined column '%s' reading user details - has users_m changed?",
            ref,
            e.column_name,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=client_error_detail("Could not load user details.", ref),
        )

    except PostgresError as e:
        ref = new_error_reference()
        logging.error("[%s] Error reading user details: %s", ref, e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=client_error_detail("Could not load user details.", ref),
        )

    except Exception:
        logging.error(
            "An error occured when executing the AI Generated SQL", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error executing the SQL",
        )
