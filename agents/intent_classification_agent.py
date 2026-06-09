import json

## Internal Packages
from models import ClassificationModel
from prompts import format_classification_prompt
from config import get_logger


## Initiate Logger
logging = get_logger(__name__)

def intent_classification(user_input, chat_history, CLASSIFICATION_MODEL_ID, span):
    '''Classify the user input to determine the intent and return the action to be taken.
    Args:
        start_time (float): The time when the request was received.
        user_input (str): The user input to classify.
        chat_history (str): The chat history to consider for classification.
    Returns:
        dict: A dictionary containing the classification type, message, reason, and action to be taken.
    '''

    try:
        logging.info("Starting intent classification process...")
        ## Classify the user input to determine the intent
        intent_classification_model = ClassificationModel()

        ## Prompt = Instructions + table schema + example + user_input 
        classification_prompt = format_classification_prompt(user_input, chat_history)
        
        span.set_attributes({
            "llm.system": "bedrock",
            "llm.model_name": str(CLASSIFICATION_MODEL_ID),
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content":  str(classification_prompt),
        })
        
        #changed
        intent_output = intent_classification_model.generate_classification(classification_prompt, span=span)
        # print("Intent Classification Result:", intent_output)
        
        if not intent_output or not intent_output.strip():
            logging.error("Empty or invalid response from classification model.")
            raise ValueError("Empty or invalid response from classification model.")
        
        intent_output_cleaned = clean_json_output(intent_output)
        logging.info(f"Classification cleaned output: {intent_output_cleaned}")

        result = json.loads(intent_output_cleaned)
        logging.info(f"Classification result: {result}")

        classification_type = result.get("type")
        logging.info(f"Classification type: {classification_type}")

        message = result.get("message", "")
        logging.info(f"Classification output: {message}")

        if classification_type == "sql":
            action = "call_sql_model"
        elif classification_type == "greeting":
            action = "return_greeting"
        elif classification_type == "rejected":
            action = "return_rejection_response"
        else:
            raise ValueError(f"Unexpected type: {classification_type}")
        
        logging.info(f"Classification result: {classification_type}, Message: {message}, Action: {action}")
        
        return {
            "type": classification_type,
            "message": message,
            "action": action
        }
    
    except json.JSONDecodeError as e:
        logging.error(f"JSON decoding error: {str(e)}")
        span.record_exception(e)
        return {
            "type": "error",
            "message": f"Failed to parse model output. Error: {str(e)}",
            "action": "log_and_notify"
        }
    
    except Exception as e:
        logging.error(f"Unexpected error during classification: {str(e)}")
        span.record_exception(e)
        return {
            "type": "error",
            "message": f"Unexpected error during classification parsing. Error: {str(e)}",
            "action": "log_and_notify"
        }
    

def clean_json_output(output: str) -> str:
    '''Remove triple backticks and optional language tag like ```json
    Args:
        output (str): The raw output string from the model.
    Returns:
        str: Cleaned output string without triple backticks and language tags.
    '''
    
    output = output.strip()
    if output.startswith("```") and output.endswith("```"):
        lines = output.splitlines()
        # Remove the first line (```json or ```) and the last line (```)
        return "\n".join(lines[1:-1])
    return output