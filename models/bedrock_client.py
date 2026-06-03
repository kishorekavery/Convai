import json
import boto3
from botocore.exceptions import BotoCoreError, NoCredentialsError, EndpointConnectionError
from fastapi import HTTPException, status
from time import time

from config import get_logger
from config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY

# Load logger
logging = get_logger(__name__)


class BedrockClient:
    def __init__(self, model_id: str, contentType: str, accept: str):
        self.region = AWS_REGION
        self.model_id = model_id
        self.contentType = contentType
        self.accept = accept
        self.client = boto3.client("bedrock-runtime", region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    
    
    def invoke_model(self, payload: dict):
        """Generic method to invoke an AWS Bedrock model."""
        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                contentType=self.contentType,
                accept=self.accept,
                body=json.dumps(payload)
            )
            content = json.loads(response["body"].read())

            if not content:
                logging.info(f"AWS Bedrock Non Streaming Response: {response}")
                logging.error("Failed to retrieve body from response.")
                raise RuntimeError("No body data returned by AWS Bedrock.")
            
            return content

        
        except NoCredentialsError:
            logging.error("AWS credentials not found. Ensure they are configured correctly.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="AWS credentials not found. Ensure they are configured correctly.")
        except EndpointConnectionError:
            logging.error("Failed to connect to AWS Bedrock endpoint.\nModel Id: %s\nRequest Body: %s",self.model_id, json.dumps(payload))
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to connect to AWS Bedrock endpoint.")
        except BotoCoreError as e:
            logging.error(f"AWS SDK error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"AWS SDK error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {e}")
    

    def invoke_model_with_response_stream(self, payload: dict, span):
        """Generic method to invoke an AWS Bedrock model."""
        try:
            start_time = time()

            response = self.client.invoke_model_with_response_stream(
                modelId=self.model_id,
                contentType=self.contentType,
                accept=self.accept,
                body=json.dumps(payload)
            )
            stream = response.get('body')
            streamed_response = ""

            if stream:
                for event in stream:
                    # logging.info("Event: %s", event)
                    chunk = event.get('chunk')
                    # logging.info("Chunk: %s", chunk)
                    if chunk:
                        chunk_json = json.loads(chunk.get('bytes').decode())
                        # logging.info("Chunk JSON: %s", chunk_json)
                        invocation_metrics = chunk_json.get("amazon-bedrock-invocationMetrics", None)
                        streamed_chunk =  chunk_json.get("generation")
                        streamed_response += streamed_chunk 
                        yield streamed_chunk
            else:
                logging.info(f"AWS Bedrock Streaming Response: {response}")
                logging.error("Failed to retrieve stream body from response.")
                raise RuntimeError("No stream body data returned by AWS Bedrock.")

            if invocation_metrics:
                invocation_processing_time = time() - start_time
                inputTokenCount=str(invocation_metrics.get("inputTokenCount"))
                outputTokenCount=str(invocation_metrics.get("outputTokenCount"))
                invocationLatency=str(invocation_metrics.get("invocationLatency"))
                firstByteLatency=str(invocation_metrics.get("firstByteLatency"))
                total_token = int(inputTokenCount) + int(outputTokenCount)
                output_response=str(streamed_response)

                span.set_attributes({
                    "llm.token_count.prompt": inputTokenCount,
                    "llm.token_count.completion": outputTokenCount,
                    "llm.token_count.total": total_token,
                })

                logging.info("Model Invoke (Streaming) Inference Log:\nPrompt: %s\nAI Response : %s\n\nInvocation Metrics:\nPrompt Token Count: %s\nOuput Token Count: %s\nReasong for Stopping: %s\nInvocation Latency: %s\nFirst Byte Latency: %s\nInvocation Processing Time: %s",
                    str(payload.get("prompt")), output_response, inputTokenCount,
                    outputTokenCount, str(chunk_json.get("stop_reason")), 
                    invocationLatency, firstByteLatency,
                    str(invocation_processing_time)
                )
        
        except NoCredentialsError:
            logging.error("AWS credentials not found. Ensure they are configured correctly.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="AWS credentials not found. Ensure they are configured correctly.")
        except EndpointConnectionError:
            logging.error("Failed to connect to AWS Bedrock endpoint.\nModel Id: %s\nRequest Body: %s",self.model_id, json.dumps(payload))
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to connect to AWS Bedrock endpoint.")
        except BotoCoreError as e:
            logging.error(f"AWS SDK error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"AWS SDK error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {e}")