from tabulate import tabulate
from time import time

from config import get_logger
from config import CHAT_MODEL_ID, CHAT_MODEL_CONTENT_TYPE, CHAT_MODEL_ACCEPT, CHAT_MODEL_MAX_GEN_LENGTH, CHAT_MODEL_TEMPERATURE, CHAT_MODEL_TOP_P
from models import BedrockClient

logging = get_logger(__name__)

class ChatModel(BedrockClient):
    def __init__(self):
        super().__init__(model_id=CHAT_MODEL_ID, contentType=CHAT_MODEL_CONTENT_TYPE, accept=CHAT_MODEL_ACCEPT)
    
    def generate_response(self, prompt):

        if not prompt or not isinstance(prompt, str):
            logging.error("Input text must be a non-empty string")
            raise ValueError("Input text must be a non-empty string.")
        
        payload = {
            "prompt": prompt,
            "max_gen_len": CHAT_MODEL_MAX_GEN_LENGTH,
            "temperature": CHAT_MODEL_TEMPERATURE,
            "top_p": CHAT_MODEL_TOP_P
        }
        
        logging.info("Llama Model Invoked (Non Streaming Response)")

        start_time = time()

        response = self.invoke_model(payload)

        response_text = response.get("generation")

        ## Log as table
        # log_json = [{
        #     "Prompt": str(prompt),
        #     "Response Text": str(response_text),
        #     "Prompt Token Count": str(response.get("prompt_token_count")),
        #     "Generation Token Count": str(response.get("generation_token_count")),
        #     "Reasong for Stopping":  str(response.get("stop_reason"))
        # }]

        # logging_table = tabulate(log_json, headers="keys", tablefmt="fancy_grid")

        # logging.info("Llama Model Inference Log:\n%s",logging_table)

        invocation_processing_time = time() - start_time

        ## log as Text
        logging.info("Llama Model (Non Streaming) Inference Log:\nPrompt: %s\nAI Response : %s\n\nInvocation Metrics:\nPrompt Token Count: %s\nOuput Token Count: %s\nReasong for Stopping: %s\nInvocation Processing Time: %s",
                    str(prompt), str(response_text), str(response.get("prompt_token_count")),
                    str(response.get("generation_token_count")),str(response.get("stop_reason")), str(invocation_processing_time)
                    )
        
        return response_text
    
    def generate_stream_response(self, prompt, span):

        if not prompt or not isinstance(prompt, str):
            logging.error("Input text must be a non-empty string")
            raise ValueError("Input text must be a non-empty string.")
        
        payload = {
            "prompt": prompt,
            "max_gen_len": CHAT_MODEL_MAX_GEN_LENGTH,
            "temperature": CHAT_MODEL_TEMPERATURE,
            "top_p": CHAT_MODEL_TOP_P
        }

        # "body": "{\"prompt\":\"this is where you place your input text\",\"max_gen_len\":512,\"temperature\":0.5,\"top_p\":0.9}"

        #log
        logging.info("Llama Model Invoked (Streaming Response)")
           
        for chunk in self.invoke_model_with_response_stream(payload, span):
            yield chunk


if __name__ == "__main__":
    def run_test():
        Chat_model = ChatModel()

        prompt = """
                <|begin_of_text|><|start_header_id|>system<|end_header_id|>
                You are a helpful AI assistant for Equipment Maintenance Expertise. Generate resopnse with 10 words<|eot_id|><|start_header_id|>user<|end_header_id|>
                What can you help me with?<|eot_id|><|start_header_id|>assistant<|end_header_id|>
                """

        for text in Chat_model.generate_stream_response(prompt):
            print(text, end="", flush=True)
            
    run_test()