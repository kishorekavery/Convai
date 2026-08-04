from time import time

from config import get_logger
from config import LOG_PROMPTS
from config import (
    CLASSIFICATION_MODEL_ID,
    CLASSIFICATION_MODEL_CONTENT_TYPE,
    CLASSIFICATION_MODEL_ACCEPT,
    CLASSIFICATION_MODEL_MAX_GEN_LENGTH,
    CLASSIFICATION_MODEL_TEMPERATURE,
    CLASSIFICATION_MODEL_TOP_P,
)
from models import BedrockClient

logging = get_logger(__name__)


class ClassificationModel(BedrockClient):
    def __init__(self):
        super().__init__(
            model_id=CLASSIFICATION_MODEL_ID,
            contentType=CLASSIFICATION_MODEL_CONTENT_TYPE,
            accept=CLASSIFICATION_MODEL_ACCEPT,
        )

    def generate_classification(self, prompt, span=None):

        if not prompt or not isinstance(prompt, str):
            logging.error("Input text must be a non-empty string")
            raise ValueError("Input text must be a non-empty string.")

        payload = {
            "prompt": prompt,
            "max_gen_len": CLASSIFICATION_MODEL_MAX_GEN_LENGTH,
            "temperature": CLASSIFICATION_MODEL_TEMPERATURE,
            "top_p": CLASSIFICATION_MODEL_TOP_P,
        }

        start_time = time()

        response = self.invoke_model(payload)

        response_text = response.get("generation")

        invocation_processing_time = time() - start_time

        prompt_tokens = response.get("prompt_token_count", 0)
        completion_tokens = response.get("generation_token_count", 0)
        total_tokens = prompt_tokens + completion_tokens

        if span:
            span.set_attributes(
                {
                    "llm.token_count.prompt": prompt_tokens,
                    "llm.token_count.completion": completion_tokens,
                    "llm.token_count.total": total_tokens,
                }
            )

        # Metrics only - see the note in text_generation_model. The
        # classification prompt is large because it carries ten worked
        # examples, and it is identical on every request.
        logging.info(
            "Classification model: %s prompt + %s completion tokens, %.2fs",
            prompt_tokens,
            completion_tokens,
            invocation_processing_time,
        )
        if LOG_PROMPTS:
            logging.info(
                "Classification prompt:\n%s\nClassification response:\n%s",
                prompt,
                response_text,
            )

        return response_text


if __name__ == "__main__":

    def run_test():
        classification_model = ClassificationModel()

        prompt = """                <|begin_of_text|><|start_header_id|>system<|end_header_id|>
                You are a helpful AI assistant for Equipment Maintenance Expertise. Generate resopnse with 10 words<|eot_id|><|start_header_id|>user<|end_header_id|>
                What can you help me with?<|eot_id|><|start_header_id|>assistant<|end_header_id|>
                """

        for text in classification_model.generate_classification(prompt):
            print(text, end="", flush=True)

    run_test()
