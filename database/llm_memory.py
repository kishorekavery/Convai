# from langchain_community.chat_message_histories.file import FileChatMessageHistory
# from pathlib import Path
# from ..config.settings import NUMBER_OF_CHAT_EXCHANGES

# #LLM Memory file path
# file_path = Path(__file__).parent.parent / "logs" / "chat_history.json"

# # Initialize the llm_memory history with the desired file path
# llm_memory = FileChatMessageHistory(file_path=file_path)

# def add_to_llm_memory(user_input: str, ai_response: str):
#     llm_memory.add_user_message(user_input)
#     llm_memory.add_ai_message(ai_response)


# def fetch_from_llm_memory(number_of_conversations: int=NUMBER_OF_CHAT_EXCHANGES):
#     # Retrieve all messages
#     messages = llm_memory.messages[-number_of_conversations:]
#     chat_history = ""

#     for msg in messages:
#         chat_history += f"{msg.type}: {msg.content}\n"

#     return chat_history


# if __name__ == "__main__":

#     # for i in range(5):
#     #     i += 5
#     #     human = f"humn{i}"
#     #     ai = f"a{i}"
#     #     add_to_llm_memory(human, ai)
    
#     print(fetch_from_llm_memory(10))