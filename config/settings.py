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
        aws_region = os.getenv("AWS_SECRET_REGION")
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

DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "0"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "4"))

KNOWLEDGEBASE_DATABASE_NAME = os.getenv("KNOWLEDGEBASE_DATABASE_NAME", "maintwiz")
KNOWLEDGEBASE_SCHEMA_NAME = os.getenv("KNOWLEDGEBASE_SCHEMA_NAME", "ai")
_KBE_TABLE_RAW = os.getenv("KNOWLEDGEBASE_TABLE", "knowlegebaseexamples_new")
KNOWLEDGEBASE_TABLE = (
    _KBE_TABLE_RAW
    if "." in _KBE_TABLE_RAW
    else f"{KNOWLEDGEBASE_SCHEMA_NAME}.{_KBE_TABLE_RAW}"
)

DATA_SCHEMA = os.getenv("DATA_SCHEMA", "ai")
USER_DETAILS_SCHEMA = os.getenv("USER_DETAILS_SCHEMA", "public")

# Timeouts & Cache Config
SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))
AI_SQL_STATEMENT_TIMEOUT_MS = int(os.getenv("AI_SQL_STATEMENT_TIMEOUT_MS", "30000"))
AI_SQL_COUNT_TIMEOUT_MS = int(os.getenv("AI_SQL_COUNT_TIMEOUT_MS", "10000"))

# LLM CONTEXT SETTINGS
KB_CONTEXT_LIMIT = max(1, int(os.getenv("KB_CONTEXT_LIMIT", "10")))
CONTEXT_LIMIT = max(1, int(os.getenv("CONTEXT_LIMIT", "10")))
NUMBER_OF_CHAT_EXCHANGES = max(1, int(os.getenv("NUMBER_OF_CHAT_EXCHANGES", "3")))

# AWS Configurations
AWS_REGION = (os.getenv("AWS_REGION") or "").strip()
EMBEDDING_MODEL_ID = (os.getenv("BEDROCK_MODEL_ID_TITAN") or "").strip()
CHAT_MODEL_ID = (os.getenv("BEDROCK_MODEL_ID_CHAT", os.getenv("BEDROCK_MODEL_ID_LLAMA")) or "").strip()
SQL_MODEL_ID = (os.getenv("BEDROCK_MODEL_ID_SQL", os.getenv("BEDROCK_MODEL_ID_LLAMA")) or "").strip()
CLASSIFICATION_MODEL_ID = (os.getenv("BEDROCK_MODEL_ID_CLASSIFICATION") or "").strip()
JUDGE_MODEL_ID = (os.getenv("BEDROCK_MODEL_ID_JUDGE", CHAT_MODEL_ID) or "").strip()
AWS_ACCESS_KEY = (os.getenv("AWS_ACCESS_KEY") or "").strip()
AWS_SECRET_KEY = (os.getenv("AWS_SECRET_KEY") or "").strip()

BEDROCK_CONNECT_TIMEOUT = int(os.getenv("BEDROCK_CONNECT_TIMEOUT", "10"))
BEDROCK_READ_TIMEOUT = int(os.getenv("BEDROCK_READ_TIMEOUT", "120"))
BEDROCK_MAX_ATTEMPTS = int(os.getenv("BEDROCK_MAX_ATTEMPTS", "3"))
BEDROCK_EXECUTOR_THREADS = int(os.getenv("BEDROCK_EXECUTOR_THREADS", "16"))

# Embedding Model Parameters
EMBEDDING_MODEL_CONTENT_TYPE: str = "application/json"
EMBEDDING_MODEL_ACCEPT: str = "*/*"
EMBEDDING_MODEL_DIMENSIONS: int = 1024
EMBEDDING_MODEL_NORMALIZATION: bool = True
EMBEDDING_CACHE_MAX_ENTRIES = int(os.getenv("EMBEDDING_CACHE_MAX_ENTRIES", "1000"))

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
    "COLLECTOR_PROJECT_NAME", "default"
)

COLLECTOR_ENDPOINT = os.getenv(
    "COLLECTOR_ENDPOINT"
)
PHOENIX_API_KEY = os.getenv("PHOENIX_API_KEY")
PHOENIX_BATCH = os.getenv("PHOENIX_BATCH", "True").lower() == "true"
ENABLE_JUDGE = os.getenv("ENABLE_JUDGE", "false").lower() == "true"

