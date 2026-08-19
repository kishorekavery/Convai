from locust import HttpUser, task, between
import random

class ConvAIUser(HttpUser):
    # Simulate a user waiting between 1 to 5 seconds before sending their next message
    wait_time = between(1, 5)

    @task
    def test_chat_completion(self):
        # We can randomize user IDs to simulate different users hitting the DB
        simulated_user_id = str(random.randint(1, 100))
        
        payload = {
            "database_name": "test_db",          # Replace with a valid test DB name
            "user_id": simulated_user_id,
            "user_input": "Show me the latest production data", 
            "facm_code": ["facility_1"],         # Replace with valid facility codes
            "chat_history": ""
        }
        
        # Hit your inference endpoint
        # Because your API streams the response back, we set stream=True
        with self.client.post("/AI/chat-completion", json=payload, stream=True, catch_response=True) as response:
            if response.status_code == 200:
                # Optional: Read the streaming chunks
                for chunk in response.iter_content(chunk_size=None):
                    pass
                response.success()
            else:
                response.failure(f"Failed with status code: {response.status_code}")

# locust -f locustfile.py --run in cmd (use at your own risk, use in a private network or with permission)
# 
# locust --host=http://localhost:8000