import re

def get_last_and_current_user_query(chat_history : str, user_query : str ) -> str:
    ''' 
        Match the first user message up to the next user: or ai: or end of string 
        and concatenate with the current user query
    '''

    last_user_query_match = re.search(r'user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)', chat_history, re.DOTALL | re.IGNORECASE)

    if last_user_query_match:
        last_user_query = last_user_query_match.group(1).strip().strip(',') + ". "
        last_and_current_user_query = last_user_query + user_query.strip().strip(',')
        return last_and_current_user_query
    
    return user_query


def get_last_user_query(chat_history : str) -> str:
    ''' 
        Match the first user message up to the next user: or ai: or end of string 
    '''

    last_user_query_match = re.search(r'user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)', chat_history, re.DOTALL | re.IGNORECASE)

    if last_user_query_match:
        last_user_query = last_user_query_match.group(1).strip().strip(',')
        return last_user_query
    
    return ''

if __name__ == "__main__":
    chat_text = "user: What is the PM Compliance for the given facility, ai: The PM Compliance is 10.98%, user: user23432343, ai: ai23443234"

    last_and_current_user_query = get_last_and_current_user_query(chat_text, "Give the constituting workorder count grouped by nature of issue")
    last_user_query = get_last_user_query(chat_text)
    
    print(last_and_current_user_query)
    print(last_user_query)