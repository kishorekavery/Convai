import os
from dotenv import load_dotenv

load_dotenv()

# Database Connection Details
DB_SECRET_NAME = os.getenv("DB_SECRET_NAME")
db_config = {}

if DB_SECRET_NAME:
    try:
        import boto3
        import json
        secrets_kwargs = {}
        
        # Read AWS configuration for Secrets Manager explicitly here
        aws_region = os.getenv("AWS_REGION")
        aws_access_key = os.getenv("AWS_ACCESS_KEY")
        aws_secret_key = os.getenv("AWS_SECRET_KEY")

        if aws_region:
            secrets_kwargs["region_name"] = aws_region
        if aws_access_key and aws_secret_key:
            secrets_kwargs["aws_access_key_id"] = aws_access_key
            secrets_kwargs["aws_secret_access_key"] = aws_secret_key

        client = boto3.client("secretsmanager", **secrets_kwargs)
        secret_value = client.get_secret_value(SecretId=DB_SECRET_NAME)
        if "SecretString" in secret_value:
            db_config = json.loads(secret_value["SecretString"])
    except Exception as e:
        raise RuntimeError(f"Failed to retrieve database secrets from Secrets Manager ({DB_SECRET_NAME}): {e}")

DB_USERNAME = os.getenv("DB_USERNAME") or db_config.get("username") or db_config.get("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD") or db_config.get("password") or db_config.get("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST") or db_config.get("host")
DB_PORT = os.getenv("DB_PORT") or db_config.get("port")
# Per-database connection pool sizing (one shared pool per database, per worker).
# Budget constraint to respect:
#   WEB_CONCURRENCY x (num_client_dbs + 1 knowledgebase) x DB_MAX_CONN
#       <= Postgres max_connections
# For many tenants / high worker counts, raise Postgres max_connections or use
# PgBouncer.
DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "0"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "4"))
KNOWLEDGEBASE_DATABASE_NAME = "maintwiz"
KNOWLEDGEBASE_SCHEMA_NAME = "ai"

# Table holding the few-shot examples. Configurable so the corrected copy can be
# rolled back with one environment variable rather than a deploy.
#   ai.knowlegebaseexamples_new - repaired: 5 broken queries fixed, LIMIT added
#                                 to 175 row-returning SELECTs, reference tables
#                                 corrected and case-normalised.
#   ai.knowledge_base_examples  - the original.
# Validated as an identifier before interpolation (it cannot be a bind
# parameter), so it must stay letters/digits/underscores/dots.
KNOWLEDGEBASE_TABLE = os.getenv("KNOWLEDGEBASE_TABLE", "ai.knowlegebaseexamples_new")

DATA_SCHEMA = "ai"
USER_DETAILS_SCHEMA = "public"

# How long a fetched table schema stays cached. Schemas change on migration, not
# per request, so this removes information_schema lookups from the hot path.
# Lower it if migrations ship more often than this; call
# database.schema_cache.table_schema_cache.invalidate() to drop entries early.
SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))

# Hard ceiling on how long an AI-generated statement may run before Postgres
# cancels it. A generation that drops a join condition produces a cartesian
# product, and because the SQL prompt mandates both ORDER BY and LIMIT, Postgres
# cannot terminate early - it must produce and sort every row before taking 50.
# Without this the connection is held until the query completes, and DB_MAX_CONN
# such queries exhaust a tenant's pool.
AI_SQL_STATEMENT_TIMEOUT_MS = int(os.getenv("AI_SQL_STATEMENT_TIMEOUT_MS", "30000"))

# The count issued by the truncation check has its LIMIT stripped, so it scans
# the whole result set by design. It gets a tighter budget: losing the "more
# pages" hint is far cheaper than holding a connection.
AI_SQL_COUNT_TIMEOUT_MS = int(os.getenv("AI_SQL_COUNT_TIMEOUT_MS", "10000"))

# LLM CONTEXT SETTINGS
KB_CONTEXT_LIMIT = 10
CONTEXT_LIMIT = 10
NUMBER_OF_CHAT_EXCHANGES = 10

# AWS Configurations
AWS_REGION = os.getenv("AWS_REGION")
EMBEDDING_MODEL_ID = os.getenv("BEDROCK_MODEL_ID_TITAN")
CHAT_MODEL_ID = os.getenv("BEDROCK_MODEL_ID_CHAT", os.getenv("BEDROCK_MODEL_ID_LLAMA"))
SQL_MODEL_ID = os.getenv("BEDROCK_MODEL_ID_SQL", os.getenv("BEDROCK_MODEL_ID_LLAMA"))
CLASSIFICATION_MODEL_ID = os.getenv("BEDROCK_MODEL_ID_CLASSIFICATION")
# Model used to *judge* groundedness/correctness in the evals/ suite. Should be a
# different model family than CHAT_MODEL_ID/SQL_MODEL_ID (e.g. a Claude Bedrock
# inference profile) to avoid self-grading bias. Falls back to CHAT_MODEL_ID so
# the eval suite still runs out of the box if it isn't configured.
JUDGE_MODEL_ID = os.getenv("BEDROCK_MODEL_ID_JUDGE", CHAT_MODEL_ID)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")

# Bedrock client tuning. Without an explicit botocore Config a hung Bedrock call
# holds an executor thread indefinitely, and a ThrottlingException surfaces as a
# hard 500 instead of being retried.
#   connect: how long to wait for the TCP/TLS handshake.
#   read:    for invoke_model this bounds the whole response; for the streaming
#            API it bounds the gap between chunks, so it need not cover the full
#            generation time.
#   attempts + adaptive mode: retries throttling with client-side rate limiting.
BEDROCK_CONNECT_TIMEOUT = int(os.getenv("BEDROCK_CONNECT_TIMEOUT", "10"))
BEDROCK_READ_TIMEOUT = int(os.getenv("BEDROCK_READ_TIMEOUT", "120"))
BEDROCK_MAX_ATTEMPTS = int(os.getenv("BEDROCK_MAX_ATTEMPTS", "3"))

# Threads available per worker for blocking Bedrock calls. Previously these ran
# on asyncio's default executor, sized min(32, cpu+4) - so the real in-flight
# request ceiling was set by the host's core count rather than by choice. These
# threads sit blocked on network I/O, not on CPU, so the pool can comfortably
# exceed the core count.
BEDROCK_EXECUTOR_THREADS = int(os.getenv("BEDROCK_EXECUTOR_THREADS", "32"))

# Embedding Model Parameters
EMBEDDING_MODEL_CONTENT_TYPE: str = "application/json"
EMBEDDING_MODEL_ACCEPT: str = "*/*"
EMBEDDING_MODEL_DIMENSIONS: int = 1024
EMBEDDING_MODEL_NORMALIZATION: bool = True
# Query embeddings are cached per worker. An embedding is a pure function of
# (model, text), so entries never go stale; this bound exists only to cap memory
# (roughly 24 KB per 1024-dim vector as a Python list).
EMBEDDING_CACHE_MAX_ENTRIES = int(os.getenv("EMBEDDING_CACHE_MAX_ENTRIES", "500"))

# Chat Model Parameters
CHAT_MODEL_CONTENT_TYPE: str = "application/json"
CHAT_MODEL_ACCEPT: str = "application/json"
CHAT_MODEL_MAX_GEN_LENGTH = 8192
CHAT_MODEL_TEMPERATURE = 0.1
CHAT_MODEL_TOP_P = 0.9

# Classification Model Parameters
CLASSIFICATION_MODEL_CONTENT_TYPE: str = "application/json"
CLASSIFICATION_MODEL_ACCEPT: str = "application/json"
# The classifier now also returns resolved_query (a rewritten question), so the
# JSON object no longer fits in 100 tokens. Too small a budget truncates the
# JSON mid-string and every classification fails to parse.
CLASSIFICATION_MODEL_MAX_GEN_LENGTH = 400
CLASSIFICATION_MODEL_TEMPERATURE = 0.1
CLASSIFICATION_MODEL_TOP_P = 0.9


# LLM Tracing Tool
COLLECTOR_PROJECT_NAME = os.getenv(
    "COLLECTOR_PROJECT_NAME", "async-mw-copilot-sql-bot-bedrock"
)
COLLECTOR_ENDPOINT = os.getenv(
    "COLLECTOR_ENDPOINT", "http://maintverse.com:6006/v1/traces"
)
PHOENIX_API_KEY = os.getenv("PHOENIX_API_KEY")
PHOENIX_BATCH = os.getenv("PHOENIX_BATCH", "True").lower() == "true"
PHOENIX_DEBUG = os.getenv("PHOENIX_DEBUG", "False").lower() == "true"
