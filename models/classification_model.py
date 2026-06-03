from time import time

from config import get_logger
from config import CLASSIFICATION_MODEL_ID, CLASSIFICATION_MODEL_CONTENT_TYPE, CLASSIFICATION_MODEL_ACCEPT, CLASSIFICATION_MODEL_MAX_GEN_LENGTH, CLASSIFICATION_MODEL_TEMPERATURE, CLASSIFICATION_MODEL_TOP_P
from models import BedrockClient


logging = get_logger(__name__)

class ClassificationModel(BedrockClient):
    def __init__(self):
        super().__init__(model_id=CLASSIFICATION_MODEL_ID, contentType=CLASSIFICATION_MODEL_CONTENT_TYPE, accept=CLASSIFICATION_MODEL_ACCEPT)
    
    def generate_classification(self, prompt):

        if not prompt or not isinstance(prompt, str):
            logging.error("Input text must be a non-empty string")
            raise ValueError("Input text must be a non-empty string.")
        
        payload = {
            "prompt": prompt,
            "max_gen_len": CLASSIFICATION_MODEL_MAX_GEN_LENGTH,
            "temperature": CLASSIFICATION_MODEL_TEMPERATURE,
            "top_p": CLASSIFICATION_MODEL_TOP_P
        }
        
        logging.info("Classification Model Invoked (Non Streaming Response)")

        start_time = time()

        response = self.invoke_model(payload)

        response_text = response.get("generation")

        invocation_processing_time = time() - start_time

        ## log as Text
        logging.info("Classification Model (Non Streaming) Inference Log:\nPrompt: %s\nAI Response : %s\n\nInvocation Metrics:\nPrompt Token Count: %s\nOuput Token Count: %s\nReasong for Stopping: %s\nInvocation Processing Time: %s",
                    str(prompt), str(response_text), str(response.get("prompt_token_count")),
                    str(response.get("generation_token_count")),str(response.get("stop_reason")), str(invocation_processing_time)
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