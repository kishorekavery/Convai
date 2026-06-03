import re

def get_last_n_user_queries(chat_history: str, n: int = 3) -> list:
    '''
    Extract all user queries and return the last n (default 3).
    '''
    
    # Find all user queries
    user_queries = re.findall(r'user:\s*(.*?)(?=\s*(?:user:|ai:)|\Z)', chat_history, re.DOTALL | re.IGNORECASE)
    # print(user_queries)

    if user_queries:
        # Clean up whitespace/commas
        user_queries = [u.strip().strip(',') for u in user_queries]
        # print(f"Raw: {user_queries}")

        # Return the last n queries (or fewer if not enough exist)
        return user_queries[:n]
    
    return ''

def get_last_and_current_user_query(chat_history: str, user_query: str) -> str:
    '''
    Concatenate the last n user queries with the current one.
    '''
    last_n_queries = get_last_n_user_queries(chat_history, 1)
    
    if last_n_queries:
        combined = ". ".join(last_n_queries) + ". " + user_query.strip().strip(',')
        return combined
    
    return user_query


if __name__ == "__main__":
    chat_text = "user: What is the PM Compliance for the given facility, ai: The PM Compliance is 10.98%, user: user23432343, ai: ai23443234, user: What is the PM count?, ai: , user: jsds, user: user: a"

    last_and_current_user_query = get_last_and_current_user_query(
                                        chat_text, "Give the constituting workorder count grouped by nature of issue")
    
    last_n_user_query = get_last_n_user_queries(chat_text)



