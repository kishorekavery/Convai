from config.logger_config import get_logger
from config.settings import EMBEDDING_MODEL_ACCEPT, EMBEDDING_MODEL_CONTENT_TYPE, EMBEDDING_MODEL_DIMENSIONS, EMBEDDING_MODEL_ID, EMBEDDING_MODEL_NORMALIZATION
from models.bedrock_client import BedrockClient, json

logging = get_logger(__name__)

class TitanEmbeddingModel(BedrockClient):
    def __init__(self):
        super().__init__(model_id=EMBEDDING_MODEL_ID, contentType=EMBEDDING_MODEL_CONTENT_TYPE, accept=EMBEDDING_MODEL_ACCEPT)
        
    def generate_embedding(self, text: str, span):
        try:
            if not text or not isinstance(text, str):
                raise ValueError("Input text must be a non-empty string.")
            
            payload = {
                "inputText": text,
                "dimensions": EMBEDDING_MODEL_DIMENSIONS,
                "normalize": EMBEDDING_MODEL_NORMALIZATION
            }
            
            response = self.invoke_model(payload)

            logging.info("Embedding Model Invoked")

            span.set_attributes({
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content":  str(payload),
            })

            embedding= response.get("embedding")
            inputtext_token = response.get("inputTextTokenCount")

            logging.info("Input Text: %s", text)
            # logging.info("Generated Vector: %s", embedding)
            logging.info("Input Text Token Size :%s", inputtext_token)

            if not embedding:
                logging.info(f"AWS Bedrock Response Body: {json.dumps(response, indent=2)}")
                logging.error("Failed to retrieve embedding for input:", exc_info=True)
                raise RuntimeError("No embedding data returned by AWS Bedrock.")
                
            return embedding
        except Exception as e:
            logging.error("Failed to generate embedding: %s", str(e), exc_info=True)
            span.record_exception(e)
            raise RuntimeError(f"Embedding generation failed: {e}")
        

if __name__ == "__main__":

    # Initialize models
    embedding_model = TitanEmbeddingModel()
    
    def run_test():
        # Example Usage
        try:
            # Generate Embedding
            text = "How many parts are in the workorder WO0019282?"
            embedding = embedding_model.generate_embedding(text)
            print(embedding)

        except Exception as e:
            logging.debug("TEST\nError: %s", e)

    run_test()