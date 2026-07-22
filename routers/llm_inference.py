from fastapi import HTTPException, status, APIRouter, Depends
from fastapi import Response
from fastapi.responses import JSONResponse
import time
from tabulate import tabulate
import asyncio
import functools
import threading

## Tracing
import contextvars
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.resources import Resource
from openinference.semconv.resource import ResourceAttributes
from openinference.instrumentation.bedrock import BedrockInstrumentor
from openinference.semconv.trace import SpanAttributes, DocumentAttributes, OpenInferenceSpanKindValues
from phoenix.otel import register

## Internal Packages
# from routers import user_quota_limiter

from config import get_logger
from config import EMBEDDING_MODEL_ID, CHAT_MODEL_ID, CLASSIFICATION_MODEL_ID
from config import COLLECTOR_ENDPOINT, COLLECTOR_PROJECT_NAME, PHOENIX_API_KEY, PHOENIX_BATCH

from routers import user_quota_limiter
from database import fetch_context, fetch_user_details
from database import UPDATE_USER_QUOTA_USAGE

from dataprocessing import get_last_and_current_user_query, get_last_n_user_queries
from models import ChatCompletionRequest
from prompts import format_sql_prompt, format_response_to_user_prompt
from prompts import format_large_volume_refine_prompt

## Initiate the models
from models import TitanEmbeddingModel
from models import ChatModel
from config import SQL_MODEL_ID, CHAT_MODEL_ID
from agents import sql_agent
from agents import intent_classification

# Custom Implementation of Starlette StreamingResponse Class
from responses import StreamingResponse


## Initiate Logger
logging = get_logger(__name__)


def _split_context_examples(context: str) -> list:
    """
    Split the concatenated 'Example N - ...' few-shot context (built in
    database/db_queries.py::fetch_context, blocks separated by a blank line)
    back into individual example strings for per-document retrieval tracing.
    """

    if not context:
        return []
    return [block.strip() for block in context.split("\n\n") if block.strip()]


## ---- Arize Phoenix Tracer Setup  ------------------------------------------------------------------------------------------------------- #

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from phoenix.otel.otel import SimpleSpanProcessor as PhoenixSimpleSpanProcessor
from phoenix.otel.otel import BatchSpanProcessor as PhoenixBatchSpanProcessor
from opentelemetry import trace as otel_trace

tracer_provider = TracerProvider(
    resource=Resource.create({ResourceAttributes.PROJECT_NAME: COLLECTOR_PROJECT_NAME})
)

# ContextVar to propagate the current database/project name across the request lifecycle
current_db_var = contextvars.ContextVar("current_db_var", default=None)

class DynamicProjectProcessor(SpanProcessor):
    """
    OTel SpanProcessor that dynamically intercepts ending spans and overrides their
    resource attributes to route them to the current client's Phoenix project.
    """
    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        db_name = current_db_var.get()
        if db_name:
            # Overwrite the project.name resource attribute dynamically
            dynamic_resource = Resource.create({ResourceAttributes.PROJECT_NAME: db_name})
            span._resource = span.resource.merge(dynamic_resource)

# 1. Add DynamicProjectProcessor FIRST so it runs before the exporter.
#    Its on_end mutates span._resource synchronously at span end, before the
#    exporter processor (below) reads/enqueues the span - so per-tenant project
#    routing is preserved under both Simple and Batch processors.
tracer_provider.add_span_processor(DynamicProjectProcessor())

# 2. Add the Phoenix exporter SECOND.
#    BatchSpanProcessor (default, gated by PHOENIX_BATCH) buffers spans and
#    exports them on a background thread, keeping the blocking HTTP export off
#    the request path / event loop. SimpleSpanProcessor exports synchronously on
#    every span.end() and is kept only as an opt-out for debugging.
_phoenix_exporter_headers = (
    {"Authorization": f"Bearer {PHOENIX_API_KEY}"} if PHOENIX_API_KEY else {}
)
if PHOENIX_BATCH:
    tracer_provider.add_span_processor(
        PhoenixBatchSpanProcessor(
            endpoint=COLLECTOR_ENDPOINT,
            headers=_phoenix_exporter_headers,
        )
    )
else:
    tracer_provider.add_span_processor(
        PhoenixSimpleSpanProcessor(
            endpoint=COLLECTOR_ENDPOINT,
            headers=_phoenix_exporter_headers,
        )
    )

# Set the global tracer provider manually since we bypass `register`
otel_trace.set_tracer_provider(tracer_provider)

# Instrument Bedrock SDK once globally
# BedrockInstrumentor().instrument(tracer_provider=tracer_provider)

# Global tracer for manual spans
tracer = tracer_provider.get_tracer(__name__)


async def _dep(request: ChatCompletionRequest):  # FastAPI will inject request here
    global tracer

    # Set the dynamic project name for all traces/spans created during this request task
    current_db_var.set(request.database_name)

    parent_span = tracer.start_span("chat_chain", kind=SpanKind.SERVER)
    parent_span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.CHAIN.value)
    # Route this trace into the client's own Phoenix project by database name
    parent_span.set_attribute("openinference.project.name", request.database_name)
    parent_span.set_attribute(SpanAttributes.USER_ID, str(request.user_id))

    
    res = await user_quota_limiter(request, tracer, parent_span)
    res["tracer"] = tracer
    return res


## Define the router config
router = APIRouter(
    prefix= "/AI",
    tags=["LLM Inference"]
    )

@router.post("/chat-completion")
async def chat_completion(
        request_data= Depends(_dep)
    ):
    span1 = None
    span2 = None
    span3 = None

    try:
    ## --------------------------------------------------------------------------------------------------- #
    ##    Intialization
    ## --------------------------------------------------------------------------------------------------- #
        global tracer

        loop = asyncio.get_running_loop()

        ## To return the response time
        start_time = time.time()

        pool = request_data['pool']
        request = request_data['request']

        ctx = request_data['ctx']
        parent_span = request_data['parent_span']
        
        token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }

        ## Assign request parameters to variables
        chat_history = request.chat_history
        raw_user_input = request.user_input
        database_name = request.database_name
        user_id = request.user_id
        parent_span.set_attribute(SpanAttributes.USER_ID, str(user_id))
        facm_code = request.facm_code
        
        logging.info("Client Domain: %s", database_name)
        logging.info("Client User Id: %s", user_id)
        logging.info("Raw User Input: %s", raw_user_input)

    ## --------------------------------------------------------------------------------------------------- #
    ##    User Input Processing
    ## --------------------------------------------------------------------------------------------------- #

        # Process the user input to combine it with last user query from the chat history
        processed_user_input = get_last_and_current_user_query(chat_history, raw_user_input)
        
        last_n_user_queries = f"Last User Queries: {get_last_n_user_queries(chat_history)}"
        
        logging.info("Processed User Input: %s", processed_user_input)

    ## --------------------------------------------------------------------------------------------------- #
    ##    Intent Classification
    ## --------------------------------------------------------------------------------------------------- #

        with tracer.start_as_current_span("1. intent_classification", context=ctx, kind=SpanKind.CLIENT) as span1:

            span1.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                "info": "Classify the user input to determine the intent and return the action to be taken",
                SpanAttributes.USER_ID: str(user_id)
                })
            
            # intent = intent_classification(raw_user_input, last_n_user_queries, CLASSIFICATION_MODEL_ID, span=span1)
            def _intent_classification(raw_user_input, last_n_user_queries, CLASSIFICATION_MODEL_ID, span):
                with trace.use_span(span):
                    return intent_classification(raw_user_input, last_n_user_queries, CLASSIFICATION_MODEL_ID, span)
                
            intent_results = await asyncio.gather(loop.run_in_executor(None,
                                    functools.partial(_intent_classification, raw_user_input, last_n_user_queries, CLASSIFICATION_MODEL_ID, span=span1))
                                )
            
            intent = intent_results[0]

            span1.set_attributes({
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content":  str(intent),
            })

            span1.set_status(Status(StatusCode.OK))
        
        # Collect tokens from span1
        s1_prompt = int(span1.attributes.get("llm.token_count.prompt") or 0)
        s1_completion = int(span1.attributes.get("llm.token_count.completion") or 0)
        token_usage["prompt_tokens"] += s1_prompt
        token_usage["completion_tokens"] += s1_completion
        token_usage["total_tokens"] += (s1_prompt + s1_completion)
        
        if intent["action"] == "return_greeting":
            # print("Intent Classification Result:", intent["action"])
            logging.info("Returning greeting response.")
            process_time = time.time() - start_time
            async with pool.acquire() as conn:
                await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), token_usage["total_tokens"])
                logging.info(f"User Quota Updated for greeting response. Spent: {token_usage['total_tokens']} tokens.")
            parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, intent["message"])
            parent_span.set_status(Status(StatusCode.OK))
            parent_span.end()
            return Response(
                status_code=status.HTTP_200_OK,
                content=intent["message"],
                headers={"X-Response-Time": f"{process_time:.6f} seconds"},
                media_type="text/plain"
            )
        elif intent["action"] == "return_rejection_response":
            # print("Intent Classification Result:", intent["action"])
            logging.info("Returning rejection response.")
            process_time = time.time() - start_time
            async with pool.acquire() as conn:
                await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), token_usage["total_tokens"])
                logging.info(f"User Quota Updated for rejection response. Spent: {token_usage['total_tokens']} tokens.")
            parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, intent["message"])
            parent_span.set_status(Status(StatusCode.OK))
            parent_span.end()
            return Response(
                status_code=status.HTTP_200_OK,
                content=intent["message"],
                headers={"X-Response-Time": f"{process_time:.6f} seconds"},
                media_type="text/plain"
            )
        
        # print("Intent Classification Result:", intent["action"])
        logging.info("Proceeding with SQL model call.")

        embedding_model = TitanEmbeddingModel()
        sql_generation_model = ChatModel(model_id=SQL_MODEL_ID)
        chat_generation_model = ChatModel(model_id=CHAT_MODEL_ID)
    
    ## --------------------------------------------------------------------------------------------------- #
    ##    Generate Vector of the user input
    ## --------------------------------------------------------------------------------------------------- #

        with tracer.start_as_current_span("2. embedding_generation", context=ctx, kind=SpanKind.CLIENT) as span2:
            # changed
            # span2.set_attributes({
            #     SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.EMBEDDING.value,
            #     "info": "Generates embedding of the processed user query",
            # })
            span2.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.EMBEDDING.value,
                "info": "Generates embedding of the processed user query",
                SpanAttributes.USER_ID: str(user_id)
            })

            span2.set_attributes({
                "llm.system": "bedrock",
                "llm.model_name": str(EMBEDDING_MODEL_ID),
            })
            
            # embedded_user_input = embedding_model.generate_embedding(processed_user_input, span=span2)
            def _embedding_generation(processed_user_input, span):
                with trace.use_span(span):
                    return embedding_model.generate_embedding(processed_user_input, span)
                
            embedding_result = await asyncio.gather(loop.run_in_executor(None, 
                                    functools.partial(_embedding_generation, processed_user_input, span=span2)
                                    )
                                )
            # print('embedding')
            embedded_user_input = embedding_result[0]

            span2.set_attributes({
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content":  str(embedded_user_input),
            })

            span2.set_status(Status(StatusCode.OK))
        
        # Collect tokens from span2
        s2_prompt = int(span2.attributes.get("llm.token_count.prompt") or 0)
        token_usage["prompt_tokens"] += s2_prompt
        token_usage["total_tokens"] += s2_prompt
    
    ## --------------------------------------------------------------------------------------------------- #
    ##    Generate SQL for the user input
    ## --------------------------------------------------------------------------------------------------- #

        ## Retrieve Context for the user input
        with tracer.start_as_current_span("2b. context_retrieval", context=ctx, kind=SpanKind.CLIENT) as span2b:
            span2b.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.RETRIEVER.value,
                "info": "Vector similarity retrieval of table schema + few-shot examples from the knowledge base",
                SpanAttributes.USER_ID: str(user_id),
                SpanAttributes.INPUT_VALUE: str(processed_user_input),
            })

            table_schema, context_for_sql_generation, context_for_user_response = await fetch_context(str(embedded_user_input), tableschema_dbconnection_pool=pool)

            ## Logging info when no context is retrieved
            if not table_schema or not context_for_sql_generation or not context_for_user_response:
                logging.warning("No context available for the given user input: %s", processed_user_input)

            span2b.set_attribute("retrieval.table_schema", str(table_schema))

            for doc_index, example_doc in enumerate(_split_context_examples(context_for_sql_generation)):
                span2b.set_attribute(
                    f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.{doc_index}.{DocumentAttributes.DOCUMENT_CONTENT}",
                    example_doc,
                )

            span2b.set_status(Status(StatusCode.OK))
        
        user_details = await fetch_user_details(user_id, pool)
        
        with tracer.start_as_current_span("3. sql_generation", context=ctx, kind=SpanKind.CLIENT) as span3:
            span3.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                "info": "LLM call to generate SQL",
                SpanAttributes.USER_ID: str(user_id)
            })

            ## Prompt = Instructions + table schema + example + user_input
            sql_generation_prompt = format_sql_prompt(
                user_input=raw_user_input,
                user_details=user_details,
                facm_code=facm_code,
                table_schema=table_schema,
                context_for_sql_generation=context_for_sql_generation,
                chat_history=last_n_user_queries,
            )
            
            span3.set_attributes({
                "llm.system": "bedrock",
                "llm.model_name": str(CHAT_MODEL_ID),
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content":  str(sql_generation_prompt)
            })

            table_rows = await sql_agent(start_time, sql_generation_prompt, pool, sql_generation_model, span3, loop, trace, facm_code)
            
            span3.set_status(Status(StatusCode.OK))

            num_rows = len(table_rows)
            num_cols = len(table_rows[0]) if num_rows > 0 else 0

            ## Log the fetched result details
            logging.info("Fetched result from %s database - Number of rows: %s", database_name, num_rows)
            logging.info("Fetched result from %s database - Number of columns: %s", database_name, num_cols)

            span3.set_attributes({
                    "sql.row_count": num_rows,
                    "sql.columns_count": num_cols,
                })
            
            # Return if large data > 500 values is passed
            if num_cols * num_rows > 500:
                logging.info(
                    "Result set too large (%s rows x %s cols). Generating refinement guidance.",
                    num_rows, num_cols,
                )

                # SQL generated for this request (set on span3 by sql_agent); used only for reasoning
                generated_sql = str(span3.attributes.get("llm.output_messages.0.message.content") or "")

                # Static fallback used if the refinement LLM call fails
                large_volume_response = "The data set for your request is too large to process in one go. Please refine your query (e.g., by selecting a specific facility, time range, equipment, or limiting the record count)."

                with tracer.start_as_current_span("3b. large_volume_refine", context=ctx, kind=SpanKind.CLIENT) as span3b:
                    span3b.set_attributes({
                        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                        "info": "LLM call to generate refinement guidance for an over-limit result set",
                        SpanAttributes.USER_ID: str(user_id),
                    })

                    refine_prompt = format_large_volume_refine_prompt(
                        raw_user_input, generated_sql, num_rows, num_cols,
                    )

                    span3b.set_attributes({
                        "llm.system": "bedrock",
                        "llm.model_name": str(CHAT_MODEL_ID),
                        "llm.input_messages.0.message.role": "system",
                        "llm.input_messages.0.message.content": str(refine_prompt),
                    })

                    try:
                        def _refine_generation(prompt, span):
                            with trace.use_span(span):
                                return chat_generation_model.generate_response(prompt, span=span)

                        refine_result = await asyncio.gather(
                            loop.run_in_executor(
                                None,
                                functools.partial(_refine_generation, refine_prompt, span=span3b),
                            )
                        )
                        refined_text = refine_result[0]
                        if refined_text and isinstance(refined_text, str) and refined_text.strip():
                            large_volume_response = refined_text.strip()

                        span3b.set_attributes({
                            "llm.output_messages.0.message.role": "assistant",
                            "llm.output_messages.0.message.content": str(large_volume_response),
                        })
                        span3b.set_status(Status(StatusCode.OK))
                    except Exception as refine_exc:
                        logging.error("Refinement generation failed, using static message: %s", refine_exc)
                        span3b.record_exception(refine_exc)
                        span3b.set_status(Status(StatusCode.ERROR, description=str(refine_exc)))

                    # Collect tokens from span3b
                    s3b_prompt = int(span3b.attributes.get("llm.token_count.prompt") or 0)
                    s3b_completion = int(span3b.attributes.get("llm.token_count.completion") or 0)
                    token_usage["prompt_tokens"] += s3b_prompt
                    token_usage["completion_tokens"] += s3b_completion
                    token_usage["total_tokens"] += (s3b_prompt + s3b_completion)

                logging.info("Context length exceeded. Return Response: %s", large_volume_response)
                process_time = time.time() - start_time
                parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, large_volume_response)
                parent_span.set_status(Status(StatusCode.OK))
                parent_span.end()

                # Collect tokens from span3
                s3_prompt = int(span3.attributes.get("llm.token_count.prompt") or 0)
                s3_completion = int(span3.attributes.get("llm.token_count.completion") or 0)
                token_usage["prompt_tokens"] += s3_prompt
                token_usage["completion_tokens"] += s3_completion
                token_usage["total_tokens"] += (s3_prompt + s3_completion)
                
                async with pool.acquire() as conn:
                    await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), token_usage["total_tokens"])
                    logging.info(f"User Quota Updated for large volume response. Spent: {token_usage['total_tokens']} tokens.")
                    
                return Response(
                    status_code=status.HTTP_200_OK,
                    content=large_volume_response,
                    headers={"X-Response-Time": f"{process_time:.6f} seconds"},
                    media_type="text/plain"
                )

        # Collect tokens from span3
        s3_prompt = int(span3.attributes.get("llm.token_count.prompt") or 0)
        s3_completion = int(span3.attributes.get("llm.token_count.completion") or 0)
        token_usage["prompt_tokens"] += s3_prompt
        token_usage["completion_tokens"] += s3_completion
        token_usage["total_tokens"] += (s3_prompt + s3_completion)

        if table_rows:
            ## Convert each asyncpg Record to a dictionary
            data = [dict(row) for row in table_rows]
            table_rows = tabulate(data, headers="keys", tablefmt="simple")
    
    ## --------------------------------------------------------------------------------------------------- #
    ##    Generate Final Response for the user input
    ## --------------------------------------------------------------------------------------------------- #

        async def traced_stream(ctx, buffer_container):
            span4 = None
            try:
                with tracer.start_as_current_span("4. final_response", context=ctx, kind=SpanKind.CLIENT) as span4:
                    span4.set_attributes({
                        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                        "info": "LLM call to generate final response",
                        SpanAttributes.USER_ID: str(user_id)
                    })
                    
                    response_to_user_prompt = format_response_to_user_prompt(raw_user_input, context_for_user_response, table_rows,
                                                                             chat_history=last_n_user_queries)

                    span4.set_attributes({
                        "llm.system": "bedrock",
                        "llm.model_name": str(CHAT_MODEL_ID),
                        "llm.input_messages.0.message.role": "system",
                        "llm.input_messages.0.message.content":  str(response_to_user_prompt),
                        # The exact SQL-result context the answer must be grounded in, and the
                        # raw user question - kept as their own attributes (rather than only
                        # embedded in the prompt above) so groundedness evals can read them
                        # without parsing the full prompt string.
                        "metadata.grounding_context": str(table_rows),
                        "metadata.user_question": str(raw_user_input),
                    })

                    queue = asyncio.Queue()

                    # Background worker (runs in thread)
                    def producer():
                        with trace.use_span(span4):
                            try:
                                for chunk in chat_generation_model.generate_stream_response(response_to_user_prompt, span4):
                                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                            finally:
                                loop.call_soon_threadsafe(queue.put_nowait, None)  # signal end of stream

                    threading.Thread(target=producer, daemon=True).start()

                    # Consume from queue as items arrive
                    while True:
                        chunk = await queue.get()
                        if chunk is None:  # end of stream
                            break
                        buffer_container.append(chunk)
                        yield chunk
                    
                    buffer = "".join(buffer_container)
                    
                    span4.set_attributes({
                        "llm.output_messages.0.message.role": "assistant",
                        "llm.output_messages.0.message.content":  str(buffer),
                    })
                    span4.set_status(Status(StatusCode.OK))

            except Exception as e:
                if span4:
                    span4.record_exception(e)
                    span4.set_status(Status(StatusCode.ERROR, description=str(e)))
                raise
            finally:
                if span4:
                    # Collect tokens from span4
                    s4_prompt = int(span4.attributes.get("llm.token_count.prompt") or 0)
                    s4_completion = int(span4.attributes.get("llm.token_count.completion") or 0)
                    token_usage["prompt_tokens"] += s4_prompt
                    token_usage["completion_tokens"] += s4_completion
                    token_usage["total_tokens"] += (s4_prompt + s4_completion)
                
        # Returns a streaming response
        process_time = time.time() - start_time

        buffer_container=[]

        if getattr(request, "eval_mode", False):
            # Consume stream immediately
            async for chunk in traced_stream(ctx, buffer_container):
                pass
            
            # buffer_container is now populated.
            generated_sql = str(span3.attributes.get("llm.output_messages.0.message.content") if span3 else "")
            
            payload = {
                "assistant_response": "".join(buffer_container),
                "generated_sql": generated_sql,
                "table_schema": str(table_schema) if 'table_schema' in locals() else "",
                "table_rows": table_rows if isinstance(table_rows, str) else "",
                "context_for_sql_generation": context_for_sql_generation if 'context_for_sql_generation' in locals() else "",
                "context_for_user_response": context_for_user_response if 'context_for_user_response' in locals() else "",
            }
            
            parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, payload["assistant_response"])
            parent_span.set_status(Status(StatusCode.OK))
            parent_span.end()
            
            async with pool.acquire() as conn:
                await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), token_usage["total_tokens"])
                logging.info(f"User Quota Updated for eval mode. Spent: {token_usage['total_tokens']} tokens.")
            
            return JSONResponse(
                content=payload,
                headers={"X-Response-Time": f"{process_time:.6f} seconds"}
            )

        api_response = StreamingResponse(traced_stream(ctx, buffer_container), media_type="text/plain", 
                                            parent_span=parent_span, buffer_container=buffer_container, 
                                            db_pool=pool, user_id=user_id, quoate_usage_update_query=UPDATE_USER_QUOTA_USAGE,
                                            logging=logging, token_usage=token_usage)
        
        api_response.headers["X-Response-Time"] = f"{process_time:.6f} seconds"
        
        return api_response

    except HTTPException as http_exc:
        try:
            total_tokens_spent = 0
            for span_var in [span1, span2, span3]:
                if span_var and hasattr(span_var, "attributes"):
                    p_tokens = int(span_var.attributes.get("llm.token_count.prompt") or 0)
                    c_tokens = int(span_var.attributes.get("llm.token_count.completion") or 0)
                    total_tokens_spent += (p_tokens + c_tokens)
            if total_tokens_spent > 0 and 'pool' in locals() and 'user_id' in locals():
                async with pool.acquire() as conn:
                    await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), total_tokens_spent)
                    logging.info(f"User Quota Updated on HTTPException. Spent: {total_tokens_spent} tokens.")
        except Exception as e_quota:
            logging.error(f"Failed to update user quota on HTTPException: {e_quota}")

        parent_span.record_exception(http_exc)
        parent_span.set_status(Status(StatusCode.ERROR, description=str(http_exc)))
        parent_span.end()
        raise http_exc  # Propagate FastAPI HTTPException as it is
    
    except Exception as e:
        try:
            total_tokens_spent = 0
            for span_var in [span1, span2, span3]:
                if span_var and hasattr(span_var, "attributes"):
                    p_tokens = int(span_var.attributes.get("llm.token_count.prompt") or 0)
                    c_tokens = int(span_var.attributes.get("llm.token_count.completion") or 0)
                    total_tokens_spent += (p_tokens + c_tokens)
            if total_tokens_spent > 0 and 'pool' in locals() and 'user_id' in locals():
                async with pool.acquire() as conn:
                    await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), total_tokens_spent)
                    logging.info(f"User Quota Updated on Exception. Spent: {total_tokens_spent} tokens.")
        except Exception as e_quota:
            logging.error(f"Failed to update user quota on Exception: {e_quota}")

        logging.error("HTTP Exception. Status Code: %s Error: %s",status.HTTP_500_INTERNAL_SERVER_ERROR,{str(e)})
        parent_span.record_exception(e)
        parent_span.set_status(Status(StatusCode.ERROR, description=str(e)))
        parent_span.end()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )