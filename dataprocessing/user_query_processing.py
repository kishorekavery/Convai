import re

def process_user_query(chat_history : str, user_query : str ) -> str:
    ''' Match the first user message up to the next user: or ai: or end of string '''

    match = re.search(r'user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)', chat_history, re.DOTALL | re.IGNORECASE)

    if match:
        match.group(1).strip().strip(',')
        updated_user_query = match.group(1).strip().strip(',') + ". " + user_query.strip().strip(',')
        # print("\n User message found:", updated_user_query, "\n")
        return updated_user_query
    return user_query

if __name__ == "__main__":
    chat_text = "user: What is the PM Compliance for the given facility, ai: The PM Compliance is 10.98%"

    output = process_user_query(chat_text, "Give the constituting workorder count grouped by nature of issue")
    print(output)