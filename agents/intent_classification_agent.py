import json

## Internal Packages
from models import ClassificationModel
from prompts import format_classification_prompt
from config import get_logger

## Initiate Logger
logging = get_logger(__name__)

# Classification type -> what the router should do with the request.
_TYPE_TO_ACTION = {
    "sql": "call_sql_model",
    "greeting": "return_greeting",
    "rejected": "return_rejection_response",
    "follow_up_pagination": "follow_up_pagination",
}


def parse_classification_output(intent_output: str, user_input: str) -> dict:
    """
    Turn the model's raw JSON into the intent dict the router consumes.

    Kept separate from the Bedrock call so the parsing and its fallbacks can be
    tested without a live model.

    Args:
        intent_output (str): raw text returned by the classification model.
        user_input (str): the user's message, used as the resolved_query
            fallback whenever the model omits or empties that field.

    Returns:
        dict: type, message, action, is_followup, resolved_query.

    Raises:
        json.JSONDecodeError: if the output is not valid JSON.
        ValueError: if the output is empty or carries an unknown type.
    """
    if not intent_output or not intent_output.strip():
        raise ValueError("Empty or invalid response from classification model.")

    result = json.loads(clean_json_output(intent_output))
    logging.info(f"Classification result: {result}")

    classification_type = result.get("type")
    if classification_type not in _TYPE_TO_ACTION:
        raise ValueError(f"Unexpected type: {classification_type}")

    # A greeting or rejection is answered directly and never routed onward, so
    # resolving it would have no consumer.
    is_followup = bool(result.get("is_followup", False))

    # Fall back to the raw message whenever the model leaves resolved_query out,
    # blank, or non-string. Downstream reads this field unconditionally, so it
    # must never be empty.
    resolved_query = result.get("resolved_query")
    if not isinstance(resolved_query, str) or not resolved_query.strip():
        if is_followup:
            logging.warning(
                "Classifier flagged a follow-up but returned no resolved_query; "
                "falling back to the raw user input."
            )
        resolved_query = user_input
    else:
        resolved_query = resolved_query.strip()

    return {
        "type": classification_type,
        "message": result.get("message", ""),
        "action": _TYPE_TO_ACTION[classification_type],
        "is_followup": is_followup,
        "resolved_query": resolved_query,
    }


def intent_classification(
    user_input, conversation_context, CLASSIFICATION_MODEL_ID, span
):
    """Classify the user input, and resolve a follow-up into a standalone question.

    Args:
        user_input (str): The user input to classify.
        conversation_context (str): Recent turns as a "User:/Assistant:" transcript,
            used to decide whether the message is a follow-up and to resolve it.
        CLASSIFICATION_MODEL_ID (str): model id, recorded on the span.
        span: OpenTelemetry span for this classification step.
    Returns:
        dict: classification type, message, action, is_followup and resolved_query.
    """

    try:
        logging.info("Starting intent classification process...")
        ## Classify the user input to determine the intent
        intent_classification_model = ClassificationModel()

        ## Prompt = Instructions + recent turns + user_input
        classification_prompt = format_classification_prompt(
            user_input, conversation_context
        )

        span.set_attributes(
            {
                "llm.system": "bedrock",
                "llm.model_name": str(CLASSIFICATION_MODEL_ID),
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": str(classification_prompt),
            }
        )

        intent_output = intent_classification_model.generate_classification(
            classification_prompt, span=span
        )

        result = parse_classification_output(intent_output, user_input)

        span.set_attributes(
            {
                "classification.type": result["type"],
                "classification.is_followup": result["is_followup"],
                "classification.resolved_query": result["resolved_query"],
            }
        )

        logging.info(
            "Classification: type=%s action=%s is_followup=%s resolved_query=%s",
            result["type"],
            result["action"],
            result["is_followup"],
            result["resolved_query"],
        )

        return result

    except json.JSONDecodeError as e:
        logging.error(f"JSON decoding error: {str(e)}")
        span.record_exception(e)
        return {
            "type": "error",
            "message": f"Failed to parse model output. Error: {str(e)}",
            "action": "log_and_notify",
            "is_followup": False,
            "resolved_query": user_input,
        }

    except Exception as e:
        logging.error(f"Unexpected error during classification: {str(e)}")
        span.record_exception(e)
        return {
            "type": "error",
            "message": f"Unexpected error during classification parsing. Error: {str(e)}",
            "action": "log_and_notify",
            "is_followup": False,
            "resolved_query": user_input,
        }


def clean_json_output(output: str) -> str:
    """Remove triple backticks and optional language tag like ```json
    Args:
        output (str): The raw output string from the model.
    Returns:
        str: Cleaned output string without triple backticks and language tags.
    """

    output = output.strip()
    if output.startswith("```") and output.endswith("```"):
        lines = output.splitlines()
        # Remove the first line (```json or ```) and the last line (```)
        return "\n".join(lines[1:-1])
    return output
