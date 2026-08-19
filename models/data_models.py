from pydantic import BaseModel, field_validator
import re

# --- Security limits (module-level, not model fields) ---
MAX_USER_INPUT_LENGTH = 2000
MAX_CHAT_HISTORY_LENGTH = 10000
MAX_DATABASE_NAME_LENGTH = 63   # PostgreSQL identifier limit
MAX_USER_ID_LENGTH = 20
MAX_FACM_CODES = 2000
MAX_FACM_CODE_LENGTH = 100


# Request Schema
class ChatCompletionRequest(BaseModel):
    database_name: str
    user_input: str
    user_id: str
    facm_code: list[str]
    chat_history: str = ""
    eval_mode: bool = False

    @field_validator("database_name")
    @classmethod
    def database_name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Database name must not be empty")
        return value

    @field_validator("database_name")
    @classmethod
    def no_spaces(cls, value):
        if " " in value:
            raise ValueError("Database name should not contain spaces")
        return value

    @field_validator("database_name")
    @classmethod
    def validate_database_name(cls, value):
        value = value.strip()
        if len(value) > MAX_DATABASE_NAME_LENGTH:
            raise ValueError(
                f"Database name must be at most {MAX_DATABASE_NAME_LENGTH} characters."
            )
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", value):
            raise ValueError(
                "Database name may only contain letters, digits, underscores, "
                "and dots. No special characters allowed."
            )
        return value

    @field_validator("user_input")
    @classmethod
    def user_input_not_blank(cls, value):
        if not value.strip():
            raise ValueError("User input must not be empty")
        return value

    @field_validator("user_input")
    @classmethod
    def validate_user_input_length(cls, value):
        if len(value) > MAX_USER_INPUT_LENGTH:
            raise ValueError(
                f"User input must be at most {MAX_USER_INPUT_LENGTH} characters. "
                f"Received {len(value)} characters."
            )
        return value.strip()

    @field_validator("user_id")
    @classmethod
    def user_id_not_blank(cls, value):
        if not value.strip():
            raise ValueError("User ID must not be empty")
        return value

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value):
        value = value.strip()
        if len(value) > MAX_USER_ID_LENGTH:
            raise ValueError(
                f"User ID must be at most {MAX_USER_ID_LENGTH} characters."
            )
        if not re.match(r"^\d+$", value):
            raise ValueError(
                f"User ID must be numeric. Received: '{value}'."
            )
        return value

    @field_validator("facm_code")
    @classmethod
    def facm_code_no_value(cls, value):
        if len(value) == 0:
            raise ValueError("Facility code must not be empty")
        if len(value) > MAX_FACM_CODES:
            raise ValueError(
                f"At most {MAX_FACM_CODES} facility codes are allowed. "
                f"Received {len(value)}."
            )
        for code in value:
            if not code.strip():
                raise ValueError("Facility codes inside the list must not be blank")
            if len(code.strip()) > MAX_FACM_CODE_LENGTH:
                raise ValueError(
                    f"Each facility code must be at most {MAX_FACM_CODE_LENGTH} characters. "
                    f"Received '{code.strip()}' with {len(code.strip())} characters."
                )
        return value

    @field_validator("chat_history")
    @classmethod
    def validate_chat_history_length(cls, value):
        if value and len(value) > MAX_CHAT_HISTORY_LENGTH:
            raise ValueError(
                f"Chat history must be at most {MAX_CHAT_HISTORY_LENGTH} characters. "
                f"Received {len(value)} characters."
            )
        return value or ""
