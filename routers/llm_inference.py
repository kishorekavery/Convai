from fastapi import HTTPException, status, APIRouter, Depends
from fastapi.responses import Response
import time
from tabulate import tabulate
import asyncio
import functools
import threading

## Internal Packages
from config.logger_config import get_logger
from config.settings import EMBEDDING_MODEL_ID, CHAT_MODEL_ID, CLASSIFICATION_MODEL_ID, COLLECTOR_ENDPOINT, COLLECTOR_PROJECT_NAME
from models.data_models import ChatCompletionRequest
from dataprocessing.user_query_processing import process_user_query
from database.db_queries import fetch_context, fetch_user_details
from database.db_queries import UPDATE_USER_QUOTA_USAGE
from prompts.prompts_templates import format_sql_prompt, format_response_to_user_prompt
from responses.streaming_response import CustomStreamingResponse as StreamingResponse
## Initiate the models
from models.embedding_model import TitanEmbeddingModel
from models.text_generation_model import LlamaModel
from agents.sql_agent import sql_agent
from agents.intent_classification_agent import intent_classification
from routers.rate_limiters import rate_limiter
## Tracing
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from openinference.instrumentation.bedrock import BedrockInstrumentor
from openinference.semconv.trace import SpanAttributes, OpenInferenceSpanKindValues
from phoenix.otel import register

## Initiate Logger
logging = get_logger(__name__)

## ---- Arize Phoenix Tracer Setup  ------------------------------------------------------------------------------------------------------- #

# 1. Set tracer provider with service name
tracer_provider = register(project_name=COLLECTOR_PROJECT_NAME, batch=True, endpoint=COLLECTOR_ENDPOINT)

# 2. Auto-instrument OpenAI SDK
BedrockInstrumentor().instrument(tracer_provider=tracer_provider)

# 3. Get tracer for manual spans
tracer = tracer_provider.get_tracer(__name__)


async def _dep(request: ChatCompletionRequest):  # FastAPI will inject request here
    global tracer

    parent_span = tracer.start_span("chat_chain", kind=SpanKind.SERVER)
    parent_span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.CHAIN.value)

    return await rate_limiter(request, tracer, parent_span)


## Define the router config
router = APIRouter(
    prefix= "/AI",
    tags=["LLM Inference"]
    )

@router.post("/chat-completion")
async def chat_completion(
    request_data= Depends(_dep)
    ):

    try:
        global tracer 

        loop = asyncio.get_running_loop()

        ## To return the response time
        start_time = time.time()

        pool = request_data['pool']
        request = request_data['request']
        ctx = request_data['ctx']
        parent_span = request_data['parent_span']
        
        ## Assign request parameters to variables
        chat_history = request.chat_history
        raw_user_input = request.user_input
        database_name = request.database_name
        user_id = request.user_id
        facm_code = request.facm_code
        # print('start')
        logging.info("Client Domain: %s", database_name)
        logging.info("Client User Id: %s", user_id)
        logging.info("Raw User Input: %s", raw_user_input)

        ## Process the user input to combine it with chat history
        processed_user_input = process_user_query(chat_history, raw_user_input)
        logging.info("Processed User Input: %s", processed_user_input)

        with tracer.start_as_current_span("1. intent_classification", context=ctx, kind=SpanKind.CLIENT) as span1:

            span1.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                "info": "Classify the user input to determine the intent and return the action to be taken",
                })
            
            # intent = intent_classification(processed_user_input, chat_history, span=span1)
            def _intent_classification(processed_user_input, chat_history, span):
                with trace.use_span(span):
                    return intent_classification(processed_user_input, chat_history, span)
                
            intent_results = await asyncio.gather(loop.run_in_executor(None, 
                                    functools.partial(_intent_classification, processed_user_input, chat_history, span=span1))
                                )
            
            # print('intent')
            intent = intent_results[0]

            span1.set_attributes({
                "llm.system": "bedrock",
                "llm.model_name": str(CLASSIFICATION_MODEL_ID),
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content":  str(intent),
            })

            span1.set_status(Status(StatusCode.OK))
        
        if intent["action"] == "return_greeting":
            # print("Intent Classification Result:", intent["action"])
            logging.info("Returning greeting response.")
            process_time = time.time() - start_time
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
        text_generation_model = LlamaModel()
        # print('models')
    
    ## ---- Generate Vector of the user input -------------------------------------------------------------------------------------- #
        with tracer.start_as_current_span("2. embedding_generation", context=ctx, kind=SpanKind.CLIENT) as span2:
            span2.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.EMBEDDING.value,
                "info": "Generates embedding of the processed user query",
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
                "llm.system": "bedrock",
                "llm.model_name": str(EMBEDDING_MODEL_ID),
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content":  str(embedded_user_input),
            })

            span2.set_status(Status(StatusCode.OK))
            
    ## ---- Generate SQL for the user input -------------------------------------------------------------------------------------- #

        ## Retrieve Context for the user input
        table_schema, context_for_sql_generation, context_for_user_response = await fetch_context(str(embedded_user_input), tableschema_dbconnection_pool=pool)
        
        ## Logging info when no context is retrieved
        if not table_schema or not context_for_sql_generation or not context_for_user_response:
            logging.warning("No context available for the given user input: %s", processed_user_input)
        
        user_details = await fetch_user_details(user_id, pool)
        
        with tracer.start_as_current_span("3. sql_generation", context=ctx, kind=SpanKind.CLIENT) as span3:
            span3.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                "info": "LLM call to generate SQL",
            })

            ## Prompt = Instructions + table schema + example + user_input
            sql_generation_prompt = format_sql_prompt(raw_user_input, user_details, facm_code , table_schema, context_for_sql_generation, chat_history)

            table_rows, sql = await sql_agent(start_time, sql_generation_prompt, pool, text_generation_model, span3, loop, trace)
            
            #user quota reduction after successful SQL Generation
            # async with pool.acquire() as conn:
            #     await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id))
            # logging.info("User Quota Updated")

            span3.set_attributes({
                "llm.system": "bedrock",
                "llm.model_name": str(CHAT_MODEL_ID),
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content":  str(sql_generation_prompt)
            })

            span3.set_status(Status(StatusCode.OK))

        num_rows = len(table_rows)
        num_cols = len(table_rows[0]) if num_rows > 0 else 0

        ## Log the fetched result details
        logging.info("Fetched result from %s database - Number of rows: %s", database_name, num_rows)
        logging.info("Fetched result from %s database - Number of columns: %s", database_name, num_cols)

        if table_rows:
            ## Convert each asyncpg Record to a dictionary
            data = [dict(row) for row in table_rows]
            table_rows = tabulate(data, headers="keys", tablefmt="grid")
    
    ## ---- Generate Final Response for the user input -------------------------------------------------------------------------------------- #        

        async def traced_stream(ctx, buffer_container):
            with tracer.start_as_current_span("4. final_response", context=ctx, kind=SpanKind.CLIENT) as span4:
                span4.set_attributes({
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                    "info": "LLM call to generate final response",
                })
                
                try:
                    response_to_user_prompt = format_response_to_user_prompt(raw_user_input, context_for_user_response, table_rows, chat_history)
                    
                    queue = asyncio.Queue()

                    # Background worker (runs in thread)
                    def producer():
                        with trace.use_span(span4):
                            try:
                                for chunk in text_generation_model.generate_stream_response(response_to_user_prompt, span4):
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
                        "llm.system": "bedrock",
                        "llm.model_name": str(CHAT_MODEL_ID),
                        "llm.input_messages.0.message.role": "system",
                        "llm.input_messages.0.message.content":  str(response_to_user_prompt),
                        "llm.output_messages.0.message.role": "assistant",
                        "llm.output_messages.0.message.content":  str(buffer),
                    })
                    span4.set_status(Status(StatusCode.OK))

                except Exception as e:
                    span4.record_exception(e)
                    raise
                
        # Returns a streaming response
        process_time = time.time() - start_time

        buffer_container=[]

        api_response = StreamingResponse(traced_stream(ctx, buffer_container), media_type="text/plain", 
                                            parent_span=parent_span, buffer_container=buffer_container, 
                                            db_pool=pool, user_id=user_id, quoate_usage_update_query=UPDATE_USER_QUOTA_USAGE)
        
        api_response.headers["X-Response-Time"] = f"{process_time:.6f} seconds"
        
        return api_response

    except HTTPException as http_exc:
        parent_span.record_exception(http_exc)
        parent_span.set_status(Status(StatusCode.ERROR, description=str(http_exc)))
        parent_span.end()
        raise http_exc  # Propagate FastAPI HTTPException as it is
    
    except Exception as e:
        logging.error("HTTP Exception. Status Code: %s Error: %s",status.HTTP_500_INTERNAL_SERVER_ERROR,{str(e)})
        parent_span.record_exception(e)
        parent_span.set_status(Status(StatusCode.ERROR, description=str(e)))
        parent_span.end()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )