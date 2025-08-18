from pydantic import BaseModel, field_validator

# Request Schema
class ChatCompletionRequest(BaseModel):
    database_name: str
    user_input: str
    user_id: str
    facm_code: list[str]
    chat_history: str

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
    
    @field_validator("user_input")
    @classmethod
    def user_input_not_blank(cls, value):
        if not value.strip():
            raise ValueError("User input must not be empty")
        return value
    
    @field_validator("user_id")
    @classmethod
    def user_id_not_blank(cls, value):
        if not value.strip():
            raise ValueError("User ID must not be empty")
        return value

    @field_validator("facm_code")
    @classmethod
    def facm_code_no_value(cls, value):
        if len(value) == 0:
            raise ValueError("Facility code must not be empty")
        return value

if __name__ == "__main__":
    # Test Cases
    print(ChatCompletionRequest(database_name="valid_db", user_id="1", user_input="test", facm_code=["code1"], chat_history="history"))
    print(ChatCompletionRequest(database_name="invalid db", user_id="1", user_input="test", facm_code=["code1"], chat_history="history"))
    print(ChatCompletionRequest(database_name="valid_db", user_id="1", user_input="", facm_code=["code1"], chat_history="history"))
    print(ChatCompletionRequest(database_name="valid_db", user_id="1", user_input="test", facm_code=[], chat_history="history"))
    print(ChatCompletionRequest(database_name="valid_db", user_id="1", user_input="test", facm_code=["code1"], chat_history=""))
    print(ChatCompletionRequest(database_name="valid_db", user_id="", user_input="test", facm_code=["code1"], chat_history=""))