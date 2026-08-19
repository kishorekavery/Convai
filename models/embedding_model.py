import json
from config import get_logger
from config import (
    EMBEDDING_MODEL_ACCEPT,
    EMBEDDING_MODEL_CONTENT_TYPE,
    EMBEDDING_MODEL_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_NORMALIZATION,
)
from models import BedrockClient
from models.embedding_cache import embedding_cache

logging = get_logger(__name__)


class TitanEmbeddingModel(BedrockClient):
    def __init__(self):
        super().__init__(
            model_id=EMBEDDING_MODEL_ID,
            contentType=EMBEDDING_MODEL_CONTENT_TYPE,
            accept=EMBEDDING_MODEL_ACCEPT,
        )

    def generate_embedding(self, text: str, span=None, use_cache: bool = True):
        try:
            if not text or not isinstance(text, str):
                raise ValueError("Input text must be a non-empty string.")

            # embedding cache block
            if use_cache:
                cached = embedding_cache.get(self.model_id, text)
                if cached is not None:
                    logging.debug("Embedding served from cache.")
                    if span:
                        span.set_attributes(
                            {
                                "llm.input_messages.0.message.role": "system",
                                "llm.input_messages.0.message.content": str(text),
                                "embedding.cache_hit": True,
                                # No tokens are charged on a cache hit, so the
                                # counts are left at zero deliberately.
                                "llm.token_count.prompt": 0,
                                "llm.token_count.total": 0,
                            }
                        )
                    return cached

            payload = {
                "inputText": text,
                "dimensions": EMBEDDING_MODEL_DIMENSIONS,
                "normalize": EMBEDDING_MODEL_NORMALIZATION,
            }

            response = self.invoke_model(payload)

            if span:
                span.set_attributes(
                    {
                        "llm.input_messages.0.message.role": "system",
                        "llm.input_messages.0.message.content": str(payload),
                        "embedding.cache_hit": False,
                    }
                )

            embedding = response.get("embedding")
            inputtext_token = response.get("inputTextTokenCount")

            logging.info("Embedding generated: %s input tokens.", inputtext_token)

            if span and inputtext_token is not None:
                span.set_attributes(
                    {
                        "llm.token_count.prompt": inputtext_token,
                        "llm.token_count.completion": 0,
                        "llm.token_count.total": inputtext_token,
                    }
                )

            if not embedding:
                logging.info(
                    f"AWS Bedrock Response Body: {json.dumps(response, indent=2)}"
                )
                logging.error("Failed to retrieve embedding for input:", exc_info=True)
                raise RuntimeError("No embedding data returned by AWS Bedrock.")
            
            if use_cache:
                embedding_cache.put(self.model_id, text, embedding)

            return embedding
        except Exception as e:
            logging.error("Failed to generate embedding: %s", str(e), exc_info=True)
            if span:
                span.record_exception(e)
            raise RuntimeError(f"Embedding generation failed: {e}")
