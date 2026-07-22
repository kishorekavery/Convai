import time
import json
import boto3
import logging
import concurrent.futures
from botocore.config import Config

from config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY, CHAT_MODEL_ID

# Setup basic logging to see when requests start and finish
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
# Reduce verbosity of underlying HTTP libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)

def invoke_model(client, task_id: int):
    """A single task that sends a prompt to Bedrock"""
    logging.info(f"[Task {task_id}] -> Starting request...")
    start_time = time.time()
    
    try:
        # Constructing a payload with a long prompt to force a long streaming response
        body = {
            "prompt": f"Write a detailed, 5-paragraph essay about the history, cultural significance, and mathematical properties of the number {task_id}. Make sure it is very long and comprehensive.",
            "max_gen_len": 512,
            "temperature": 0.7
        }
        
        # Using the streaming API to see how holding connections open affects the pool
        response = client.invoke_model_with_response_stream(
            modelId=CHAT_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body)
        )
        
        # Consume the stream
        stream = response.get('body')
        first_chunk_received = False
        
        for event in stream:
            chunk = event.get('chunk')
            if chunk:
                if not first_chunk_received:
                    time_to_first_chunk = time.time() - start_time
                    logging.info(f"[Task {task_id}] --- Stream Started (First Chunk in {time_to_first_chunk:.2f}s)")
                    first_chunk_received = True
                pass
        
        duration = time.time() - start_time
        logging.info(f"[Task {task_id}] <- Stream Completed in {duration:.2f}s")
        return duration
    except Exception as e:
        logging.error(f"[Task {task_id}] Error - {str(e)}")
        return None

def run_pool_test(pool_size: int, concurrent_requests: int):
    """Run a test with a specific connection pool size."""
    logging.info(f"\n{'='*50}")
    logging.info(f"TESTING: max_pool_connections = {pool_size}")
    logging.info(f"Triggering {concurrent_requests} concurrent requests...")
    logging.info(f"{'='*50}")
    
    # Configure the connection pool size
    boto_config = Config(max_pool_connections=pool_size)
    
    # Initialize the client ONCE
    shared_client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        config=boto_config
    )
    
    test_start = time.time()
    
    # Use a thread pool to simulate simultaneous requests from multiple users/workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_requests) as executor:
        futures = []
        for i in range(1, concurrent_requests + 1):
            futures.append(executor.submit(invoke_model, shared_client, i))
            
        # Wait for all requests to finish
        concurrent.futures.wait(futures)
        
    total_duration = time.time() - test_start
    logging.info(f"\n>>> Total time for all {concurrent_requests} requests: {total_duration:.2f}s <<<")

if __name__ == "__main__":
    # Test 1: Pool size of 1. 
    # Because there is only 1 HTTP connection available, botocore will force 
    # the 5 concurrent requests to run sequentially (one after another).
    # You will see the tasks start at the same time, but finish one by one.
    run_pool_test(pool_size=1, concurrent_requests=5)
    
    # Test 2: Pool size of 5.
    # Now there are 5 HTTP connections available. All 5 requests can be 
    # sent to AWS truly in parallel. Total time should be significantly shorter.
    run_pool_test(pool_size=5, concurrent_requests=5)
    
    # Test 3: Pool size of 20 with 25 concurrent requests.
    # This simulates a high-traffic scenario. 20 requests will stream immediately.
    # The last 5 requests will queue up and wait for the first connections to become available.
    run_pool_test(pool_size=20, concurrent_requests=25)

    #Test 4: Pool size for 50 concurrent requests
    run_pool_test(pool_size=50, concurrent_requests=60)

