import json
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    NoCredentialsError,
    EndpointConnectionError,
)
from fastapi import HTTPException, status
from time import time

from config import get_logger
from config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY
from config import (
    BEDROCK_CONNECT_TIMEOUT,
    BEDROCK_READ_TIMEOUT,
    BEDROCK_MAX_ATTEMPTS,
)

# Load logger
logging = get_logger(__name__)


@lru_cache(maxsize=1)
def get_bedrock_runtime_client():
    """
    Return the process-wide bedrock-runtime client, creating it on first use.

    Building a boto3 client resolves credentials, loads and parses the service
    model and constructs the endpoint - tens to hundreds of milliseconds. The
    previous code did that inside every model wrapper, so a single request paid
    for it four times (embedding, SQL, chat, classification) on the synchronous
    path. The client itself is stateless with respect to the model being
    invoked, so one instance serves every model id.

    Cached lazily rather than built at import so that importing this module does
    not require AWS credentials - tests and tooling can import it freely.

    Thread-safety: boto3 *clients* are safe for concurrent method calls; boto3
    *sessions* are not safe for concurrent client creation. Creating one here is
    therefore strictly safer than the previous per-request construction across
    executor threads.
    """
    logging.info("Creating the shared AWS Bedrock runtime client.")
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        config=Config(
            connect_timeout=BEDROCK_CONNECT_TIMEOUT,
            read_timeout=BEDROCK_READ_TIMEOUT,
            # Adaptive mode adds client-side rate limiting, so a burst of
            # ThrottlingExceptions is retried and paced instead of surfacing
            # as a 500.
            retries={"max_attempts": BEDROCK_MAX_ATTEMPTS, "mode": "adaptive"},
        ),
    )


class BedrockClient:
    def __init__(self, model_id: str, contentType: str, accept: str):
        self.region = AWS_REGION
        self.model_id = model_id
        self.contentType = contentType
        self.accept = accept
        # Shared across every wrapper instance; constructing a wrapper is now
        # cheap, so the per-request instantiations cost nothing meaningful.
        self.client = get_bedrock_runtime_client()

    def invoke_model(self, payload: dict):
        """Generic method to invoke an AWS Bedrock model."""
        try:
            # 1. Format payload according to specific model provider requirements
            if "embed" in self.model_id:
                body = payload
            else:
                prompt = payload.get("prompt")
                if "anthropic" in self.model_id:
                    body = {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": payload.get("max_gen_len", 100),
                        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
                    }
                elif "meta" in self.model_id:
                    body = payload
                elif "amazon" in self.model_id:
                    body = {
                        "inferenceConfig": {"max_new_tokens": payload.get("max_gen_len", 100)},
                        "messages": [{"role": "user", "content": [{"text": prompt}]}]
                    }
                elif "google" in self.model_id:
                    body = {
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": payload.get("max_gen_len", 100),
                        "temperature": payload.get("temperature", 0.5)
                    }
                else:
                    body = payload

            response = self.client.invoke_model(
                modelId=self.model_id,
                contentType=self.contentType,
                accept=self.accept,
                body=json.dumps(body),
            )
            content = json.loads(response["body"].read())

            if not content:
                logging.info(f"AWS Bedrock Non Streaming Response: {response}")
                logging.error("Failed to retrieve body from response.")
                raise RuntimeError("No body data returned by AWS Bedrock.")

            # 3. Parse and extract output payload 
            # Injecting into the "generation" key to maintain compatibility with existing app models
            if "embed" not in self.model_id:
                if "anthropic" in self.model_id:
                    content["generation"] = content["content"][0]["text"]
                elif "amazon" in self.model_id:
                    content["generation"] = content["output"]["message"]["content"][0]["text"]
                elif "google" in self.model_id:
                    content["generation"] = content["choices"][0]["message"]["content"]
                elif "meta" in self.model_id:
                    # Llama models typically return the text directly in the "generation" key natively
                    content["generation"] = content.get("generation", "")

            # 4. Extract Token counts from AWS Bedrock headers
            headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            content["prompt_token_count"] = int(headers.get("x-amzn-bedrock-input-token-count", 0))
            content["generation_token_count"] = int(headers.get("x-amzn-bedrock-output-token-count", 0))

            return content

        except NoCredentialsError:
            logging.error(
                "AWS credentials not found. Ensure they are configured correctly."
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AWS credentials not found. Ensure they are configured correctly.",
            )
        except EndpointConnectionError:
            logging.error(
                "Failed to connect to AWS Bedrock endpoint.\nModel Id: %s\nRequest Body: %s",
                self.model_id,
                json.dumps(payload),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to AWS Bedrock endpoint.",
            )
        except BotoCoreError as e:
            logging.error(
                f"AWS SDK error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AWS SDK error: {e}",
            )
        except Exception as e:
            logging.error(
                f"Unexpected error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {e}",
            )

    def invoke_model_with_response_stream(self, payload: dict, span):
        """Generic method to invoke an AWS Bedrock model."""
        try:
            start_time = time()

            # 1. Format payload according to specific model provider requirements
            if "embed" in self.model_id:
                body = payload
            else:
                prompt = payload.get("prompt")
                if "anthropic" in self.model_id:
                    body = {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": payload.get("max_gen_len", 100),
                        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
                    }
                elif "meta" in self.model_id:
                    body = payload
                elif "amazon" in self.model_id:
                    body = {
                        "inferenceConfig": {"max_new_tokens": payload.get("max_gen_len", 100)},
                        "messages": [{"role": "user", "content": [{"text": prompt}]}]
                    }
                elif "google" in self.model_id:
                    body = {
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": payload.get("max_gen_len", 100),
                        "temperature": payload.get("temperature", 0.5)
                    }
                else:
                    body = payload

            response = self.client.invoke_model_with_response_stream(
                modelId=self.model_id,
                contentType=self.contentType,
                accept=self.accept,
                body=json.dumps(body),
            )
            stream = response.get("body")
            streamed_response = ""
            final_invocation_metrics = None

            if stream:
                for event in stream:
                    chunk = event.get("chunk")
                    if chunk:
                        chunk_json = json.loads(chunk.get("bytes").decode())
                        
                        # Fix: Safely extract metrics without overwriting previous chunks
                        metrics = chunk_json.get("amazon-bedrock-invocationMetrics")
                        if metrics:
                            final_invocation_metrics = metrics

                        # Parse streamed chunk based on provider
                        streamed_chunk = ""
                        if "anthropic" in self.model_id:
                            if chunk_json.get("type") == "content_block_delta":
                                streamed_chunk = chunk_json.get("delta", {}).get("text", "")
                        elif "amazon" in self.model_id:
                            if "contentBlockDelta" in chunk_json:
                                streamed_chunk = chunk_json["contentBlockDelta"].get("delta", {}).get("text", "")
                        elif "google" in self.model_id:
                            choices = chunk_json.get("choices", [])
                            if choices and "delta" in choices[0]:
                                streamed_chunk = choices[0]["delta"].get("content", "")
                        else:
                            # Fallback (e.g., Meta Llama)
                            streamed_chunk = chunk_json.get("generation", "")

                        if streamed_chunk:
                            streamed_response += streamed_chunk
                            yield streamed_chunk
            else:
                logging.info(f"AWS Bedrock Streaming Response: {response}")
                logging.error("Failed to retrieve stream body from response.")
                raise RuntimeError("No stream body data returned by AWS Bedrock.")

            if final_invocation_metrics:
                invocation_processing_time = time() - start_time
                inputTokenCount = str(final_invocation_metrics.get("inputTokenCount", 0))
                outputTokenCount = str(final_invocation_metrics.get("outputTokenCount", 0))
                invocationLatency = str(final_invocation_metrics.get("invocationLatency", 0))
                firstByteLatency = str(final_invocation_metrics.get("firstByteLatency", 0))
                total_token = int(inputTokenCount) + int(outputTokenCount)
                output_response = str(streamed_response)

                span.set_attributes(
                    {
                        "llm.token_count.prompt": inputTokenCount,
                        "llm.token_count.completion": outputTokenCount,
                        "llm.token_count.total": total_token,
                    }
                )

                logging.info(
                    "Model Invoke (Streaming) Inference Log:\nPrompt: %s\nAI Response : %s\n\nInvocation Metrics:\nPrompt Token Count: %s\nOuput Token Count: %s\n Reason for Stopping: %s\nInvocation Latency: %s\nFirst Byte Latency: %s\nInvocation Processing Time: %s",
                    str(payload.get("prompt")),
                    output_response,
                    inputTokenCount,
                    outputTokenCount,
                    str(chunk_json.get("stop_reason")),
                    invocationLatency,
                    firstByteLatency,
                    str(invocation_processing_time),
                )

        except NoCredentialsError:
            logging.error(
                "AWS credentials not found. Ensure they are configured correctly."
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AWS credentials not found. Ensure they are configured correctly.",
            )
        except EndpointConnectionError:
            logging.error(
                "Failed to connect to AWS Bedrock endpoint.\nModel Id: %s\nRequest Body: %s",
                self.model_id,
                json.dumps(payload),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to connect to AWS Bedrock endpoint.",
            )
        except BotoCoreError as e:
            logging.error(
                f"AWS SDK error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AWS SDK error: {e}",
            )
        except Exception as e:
            logging.error(
                f"Unexpected error: {e}\nModel Id: {self.model_id}\nRequest Body: {json.dumps(payload)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {e}",
            )
