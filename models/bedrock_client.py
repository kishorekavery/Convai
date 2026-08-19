import json
import os
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
from config import LOG_PROMPTS
from config import AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY
from config import (
    BEDROCK_CONNECT_TIMEOUT,
    BEDROCK_READ_TIMEOUT,
    BEDROCK_MAX_ATTEMPTS,
)

# Load logger
logging = get_logger(__name__)


@lru_cache(maxsize=2)
def get_bedrock_runtime_client(region: str = None):
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
    final_region = region or AWS_REGION
    logging.info(f"Creating the shared AWS Bedrock runtime client for region: {final_region}")
    client_kwargs = {}
    if final_region:
        client_kwargs["region_name"] = final_region
    
    if AWS_ACCESS_KEY and AWS_SECRET_KEY:
        client_kwargs["aws_access_key_id"] = AWS_ACCESS_KEY
        client_kwargs["aws_secret_access_key"] = AWS_SECRET_KEY

    return boto3.client(
        "bedrock-runtime",
        **client_kwargs,
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
        self.fallback_region = os.getenv("AWS_SECRET_REGION")
        self.model_id = model_id
        self.contentType = contentType
        self.accept = accept
        # Shared across every wrapper instance; constructing a wrapper is now
        # cheap, so the per-request instantiations cost nothing meaningful.
        self.client = get_bedrock_runtime_client(self.region)

    def invoke_model(self, payload: dict):
        """Generic method to invoke an AWS Bedrock model."""
        try:
            if "embed" in self.model_id:
                def _do_invoke(client_instance):
                    response = client_instance.invoke_model(
                        modelId=self.model_id,
                        contentType=self.contentType,
                        accept=self.accept,
                        body=json.dumps(payload),
                    )
                    return json.loads(response["body"].read()), response

                try:
                    content, response = _do_invoke(self.client)
                except Exception as e:
                    error_msg = str(e)
                    if "UnrecognizedClientException" in error_msg or "AccessDenied" in error_msg or "ValidationException" in error_msg:
                        logging.warning(f"Primary region {self.region} failed with {type(e).__name__}: {error_msg}. Retrying in fallback region {self.fallback_region}...")
                        fallback_client = get_bedrock_runtime_client(self.fallback_region)
                        content, response = _do_invoke(fallback_client)
                    else:
                        raise e

                headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                content["prompt_token_count"] = int(headers.get("x-amzn-bedrock-input-token-count", 0))
                content["generation_token_count"] = int(headers.get("x-amzn-bedrock-output-token-count", 0))
                return content
            
            else:
                # Use the Converse API for text generation models
                prompt = payload.get("prompt")
                messages = [{"role": "user", "content": [{"text": prompt}]}]
                inference_config = {}
                if "max_gen_len" in payload:
                    inference_config["maxTokens"] = payload["max_gen_len"]
                if "temperature" in payload:
                    inference_config["temperature"] = payload["temperature"]
                if "top_p" in payload:
                    inference_config["topP"] = payload["top_p"]

                def _do_converse(client_instance):
                    return client_instance.converse(
                        modelId=self.model_id,
                        messages=messages,
                        inferenceConfig=inference_config
                    )
                
                try:
                    response = _do_converse(self.client)
                except Exception as e:
                    error_msg = str(e)
                    if "UnrecognizedClientException" in error_msg or "AccessDenied" in error_msg or "ValidationException" in error_msg:
                        logging.warning(f"Primary region {self.region} failed with {type(e).__name__}: {error_msg}. Retrying in fallback region {self.fallback_region}...")
                        fallback_client = get_bedrock_runtime_client(self.fallback_region)
                        response = _do_converse(fallback_client)
                    else:
                        raise e
                
                # Map Converse API response back to the dictionary expected by text_generation_model.py
                content = {}
                output_message = response.get("output", {}).get("message", {})
                if output_message and output_message.get("content"):
                    content["generation"] = output_message["content"][0].get("text", "")
                else:
                    content["generation"] = ""
                
                content["stop_reason"] = response.get("stopReason")
                usage = response.get("usage", {})
                content["prompt_token_count"] = usage.get("inputTokens", 0)
                content["generation_token_count"] = usage.get("outputTokens", 0)
                
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

            if "embed" in self.model_id:
                raise NotImplementedError("Streaming is not supported for embedding models.")
            
            prompt = payload.get("prompt")
            messages = [{"role": "user", "content": [{"text": prompt}]}]
            inference_config = {}
            if "max_gen_len" in payload:
                inference_config["maxTokens"] = payload["max_gen_len"]
            if "temperature" in payload:
                inference_config["temperature"] = payload["temperature"]
            if "top_p" in payload:
                inference_config["topP"] = payload["top_p"]

            response = self.client.converse_stream(
                modelId=self.model_id,
                messages=messages,
                inferenceConfig=inference_config
            )
            
            stream = response.get("stream")
            streamed_response = ""
            inputTokenCount = 0
            outputTokenCount = 0
            invocationLatency = 0
            firstByteLatency = 0

            if not stream:
                logging.error("Failed to retrieve stream body from response.")
                raise RuntimeError("No stream body data returned by AWS Bedrock.")

            for event in stream:
                if "contentBlockDelta" in event:
                    streamed_chunk = event["contentBlockDelta"].get("delta", {}).get("text", "")
                    if streamed_chunk:
                        streamed_response += streamed_chunk
                        yield streamed_chunk
                elif "metadata" in event:
                    metrics = event["metadata"].get("usage", {})
                    inputTokenCount = metrics.get("inputTokens", 0)
                    outputTokenCount = metrics.get("outputTokens", 0)
                    
                    latency = event["metadata"].get("metrics", {})
                    invocationLatency = latency.get("latencyMs", 0)
                    
            if inputTokenCount or outputTokenCount:
                invocation_processing_time = time() - start_time
                total_token = inputTokenCount + outputTokenCount
                output_response = str(streamed_response)

                span.set_attributes(
                    {
                        "llm.token_count.prompt": inputTokenCount,
                        "llm.token_count.completion": outputTokenCount,
                        "llm.token_count.total": total_token,
                    }
                )

                # Metrics only. first-byte latency is the one worth watching -
                # it is what the user perceives as responsiveness.
                logging.info(
                    "Streaming response: %s prompt + %s completion tokens, "
                    "first byte %sms, total %sms, wall %.2fs",
                    inputTokenCount,
                    outputTokenCount,
                    firstByteLatency,
                    invocationLatency,
                    invocation_processing_time,
                )
                if LOG_PROMPTS:
                    logging.info(
                        "Streaming prompt:\n%s\nStreaming response:\n%s",
                        payload.get("prompt"),
                        output_response,
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
