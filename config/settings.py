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
DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "2"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "10"))
KNOWLEDGEBASE_DATABASE_NAME = "maintwiz"
KNOWLEDGEBASE_SCHEMA_NAME = "ai"

DATA_SCHEMA = "ai"
USER_DETAILS_SCHEMA = "public"

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

# Embedding Model Parameters
EMBEDDING_MODEL_CONTENT_TYPE: str = "application/json"
EMBEDDING_MODEL_ACCEPT: str = "*/*"
EMBEDDING_MODEL_DIMENSIONS: int = 1024
EMBEDDING_MODEL_NORMALIZATION: bool = True

# Chat Model Parameters
CHAT_MODEL_CONTENT_TYPE: str = "application/json"
CHAT_MODEL_ACCEPT: str = "application/json"
CHAT_MODEL_MAX_GEN_LENGTH = 8192
CHAT_MODEL_TEMPERATURE = 0.1
CHAT_MODEL_TOP_P = 0.9

# Classification Model Parameters
CLASSIFICATION_MODEL_CONTENT_TYPE: str = "application/json"
CLASSIFICATION_MODEL_ACCEPT: str = "application/json"
CLASSIFICATION_MODEL_MAX_GEN_LENGTH = 100
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
