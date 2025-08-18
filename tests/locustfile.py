from locust import HttpUser, task, between
import json
import random

class ChatCompletionUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = between(0.1, 1)  # seconds between requests

    @task
    def chat_completion(self):

        db_name = random.choice(['parry', 'yokohama', 'tpmdemo'])

        if db_name == 'parry':
            facm_code = ['DRYING SECTION']

        if db_name == 'yokohama':
            facm_code = ['ATC-GJ-CURG']

        if db_name == 'tpmdemo':
            facm_code = ['ATC-GJ-CURG']

        payload = {
            "database_name": db_name,
            "user_input": "List top 10 workorders",
            "user_id": "1",
            "facm_code": facm_code,
            "chat_history": ""
        }
        
        headers = {"Content-Type": "application/json"}
        
        # If using HTTP, include http://
        self.client.post(
            "/AI/chat-completion",
            data=json.dumps(payload),
            headers=headers
        )

# Run with:
# locust -f locustfile.py --host=http://maintverse.com:8000