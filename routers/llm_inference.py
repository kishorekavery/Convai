from fastapi import HTTPException, status, APIRouter, Depends
from fastapi import Response
from fastapi.responses import JSONResponse
import re
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
from config import new_error_reference, client_error_detail
from config import EMBEDDING_MODEL_ID, CHAT_MODEL_ID, CLASSIFICATION_MODEL_ID
from config import COLLECTOR_ENDPOINT, COLLECTOR_PROJECT_NAME, PHOENIX_API_KEY, PHOENIX_BATCH
from config import validate_collector_endpoint

from routers import user_quota_limiter
from routers.query_cache import last_query_cache, CachedQuery
from database import fetch_context, fetch_user_details
from database import UPDATE_USER_QUOTA_USAGE
from database import execute_ai_generated_sql, execute_count_query, format_sql_query
from database import next_page_sql, extract_limit, extract_offset, DEFAULT_PAGE_SIZE
from database.sql_safety import validate_sql

from dataprocessing import get_last_and_current_user_query, get_last_n_user_queries
from dataprocessing import get_last_n_exchanges
from dataprocessing import is_bare_pagination_request
from models import ChatCompletionRequest
from prompts import format_sql_prompt, format_response_to_user_prompt
from prompts import format_large_volume_refine_prompt

## Initiate the models
from models import TitanEmbeddingModel
from models import get_bedrock_executor
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
# from phoenix.otel.otel import SimpleSpanProcessor as PhoenixSimpleSpanProcessor
# from phoenix.otel.otel import BatchSpanProcessor as PhoenixBatchSpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
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

# 2. Add the gRPC Phoenix exporter SECOND.
_phoenix_exporter_headers = (
    {"authorization": f"Bearer {PHOENIX_API_KEY}"} if PHOENIX_API_KEY else {}
)

# A misconfigured endpoint fails silently: the exporter raises nothing at
# construction, BatchSpanProcessor swallows export errors, and the only symptom
# is an empty Phoenix UI. Check it explicitly and say so at startup.
for _problem in validate_collector_endpoint(COLLECTOR_ENDPOINT):
    logging.warning(
        "COLLECTOR_ENDPOINT (%s) %s Traces will be dropped silently.",
        COLLECTOR_ENDPOINT,
        _problem,
    )

# Use standard gRPC OTLPSpanExporter for low latency
grpc_exporter = OTLPSpanExporter(
    endpoint=COLLECTOR_ENDPOINT,
    headers=_phoenix_exporter_headers,
)
logging.info("Exporting spans over gRPC to %s", COLLECTOR_ENDPOINT)

if PHOENIX_BATCH:
    tracer_provider.add_span_processor(BatchSpanProcessor(grpc_exporter))
else:
    tracer_provider.add_span_processor(SimpleSpanProcessor(grpc_exporter))

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

        # Process the user input to combine it with last user query from the chat history.
        # Used only as the fallback if the classifier cannot resolve the message.
        processed_user_input = get_last_and_current_user_query(chat_history, raw_user_input)

        last_n_user_queries = f"Last User Queries: {get_last_n_user_queries(chat_history)}"

        # Recent turns, with the assistant's replies kept, so the classifier can
        # tell what "that" or "what about last quarter" refers to.
        conversation_context = get_last_n_exchanges(chat_history, n=3)

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
            
            def _intent_classification(raw_user_input, conversation_context, CLASSIFICATION_MODEL_ID, span):
                with trace.use_span(span):
                    return intent_classification(raw_user_input, conversation_context, CLASSIFICATION_MODEL_ID, span)

            intent_results = await asyncio.gather(loop.run_in_executor(get_bedrock_executor(),
                                    functools.partial(_intent_classification, raw_user_input, conversation_context, CLASSIFICATION_MODEL_ID, span=span1))
                                )

            intent = intent_results[0]

            span1.set_attributes({
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content":  str(intent),
            })

            span1.set_status(Status(StatusCode.OK))

        # The classifier rewrites a follow-up into a standalone question. Every
        # step after this point consumes the resolved form, so "what about last
        # quarter?" is retrieved and generated against as the full question.
        resolved_query = intent.get("resolved_query") or raw_user_input
        processed_user_input = resolved_query

        if intent.get("is_followup"):
            logging.info(
                "Follow-up detected. Raw: %r -> Resolved: %r", raw_user_input, resolved_query
            )

        parent_span.set_attributes({
            "classification.is_followup": bool(intent.get("is_followup")),
            "classification.resolved_query": str(resolved_query),
        })


        # Collect tokens from span1
        s1_prompt = int(span1.attributes.get("llm.token_count.prompt") or 0)
        s1_completion = int(span1.attributes.get("llm.token_count.completion") or 0)
        token_usage["prompt_tokens"] += s1_prompt
        token_usage["completion_tokens"] += s1_completion
        token_usage["total_tokens"] += (s1_prompt + s1_completion)
        
        # Guard rail: pagination needs the previous query to still be cached.
        # A miss is expected when the follow-up is handled by a different
        # gunicorn worker than the original query, or after the entry expired -
        # in both cases asking the user to restate beats guessing.
        cached_page = None
        if intent["action"] == "follow_up_pagination":
            cached_page = last_query_cache.get(database_name, user_id)
            if cached_page is None:
                if is_bare_pagination_request(raw_user_input):
                    # A genuine "more" with nothing left to page. Asking the
                    # user to restate is the only honest answer.
                    logging.info(
                        "Pagination requested but no cached query for user %s in %s.",
                        user_id,
                        database_name,
                    )
                    intent["action"] = "return_rejection_response"
                    intent["message"] = (
                        "I no longer have the previous results to page through. "
                        "Please ask your question again."
                    )
                else:
                    # The classifier labelled a self-contained question as
                    # pagination - it does this after a run of "more" replies.
                    # Answering the question is far better than telling the user
                    # their results expired when they asked something new.
                    logging.warning(
                        "Classifier said pagination for a substantive message; "
                        "treating it as a new question instead. Message: %r",
                        raw_user_input,
                    )
                    intent["action"] = "call_sql_model"
                    intent["type"] = "sql"
                    intent["is_followup"] = False


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

        # Initialize context variables
        embedded_user_input = ""
        table_schema = ""
        context_for_sql_generation = ""
        context_for_user_response = ""

        # State describing the page that this request ends up serving. For a new
        # question these are derived from the generated SQL; for a follow-up they
        # are advanced from the cached entry. Either way they are what gets
        # cached at the end, so the *next* "show more" can continue from here.
        sql_template = ""
        page_offset = 0
        page_size = DEFAULT_PAGE_SIZE
        # The question these rows answer. Stored in resolved form so a later
        # pagination request cites a standalone question rather than a fragment
        # like "only the critical ones". For a pagination follow-up it is
        # restored from the cache instead.
        original_user_input = resolved_query
        total_count = None

        is_pagination = intent["action"] == "follow_up_pagination"

        if is_pagination:
            logging.info(
                "Follow-up pagination intent detected. Reusing the cached query "
                "and advancing the OFFSET; no retrieval or SQL generation needed."
            )
            # Carry the original retrieval forward so the final-response prompt
            # keeps the schema and formatting examples it had on page 1.
            table_schema = cached_page.table_schema
            context_for_user_response = cached_page.context_for_user_response
            # "show me more" describes nothing; the answer must be written
            # against the question that produced page 1.
            original_user_input = cached_page.original_user_input

        else:
            with tracer.start_as_current_span("2. embedding_generation", context=ctx, kind=SpanKind.CLIENT) as span2:
                span2.set_attributes({
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.EMBEDDING.value,
                    "info": "Generates embedding of the processed user query",
                    SpanAttributes.USER_ID: str(user_id)
                })

                span2.set_attributes({
                    "llm.system": "bedrock",
                    "llm.model_name": str(EMBEDDING_MODEL_ID),
                })
            
                def _embedding_generation(processed_user_input, span):
                    with trace.use_span(span):
                        return embedding_model.generate_embedding(processed_user_input, span)
                
                embedding_result = await asyncio.gather(loop.run_in_executor(get_bedrock_executor(), 
                                        functools.partial(_embedding_generation, processed_user_input, span=span2)
                                        )
                                    )
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

                table_schema, context_for_sql_generation, context_for_user_response = await fetch_context(str(embedded_user_input), tableschema_dbconnection_pool=pool, database_name=database_name)

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
        

        if is_pagination:
            ## ----------------------------------------------------------------- #
            ##    Advance the cached query to the next page (no LLM call)
            ## ----------------------------------------------------------------- #
            with tracer.start_as_current_span("3. sql_pagination", context=ctx, kind=SpanKind.CLIENT) as span3:
                span3.set_attributes({
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
                    "info": "Advance the previous query's OFFSET to serve the next page",
                    SpanAttributes.USER_ID: str(user_id),
                    "pagination.previous_offset": cached_page.offset,
                })

                next_page = next_page_sql(
                    cached_page.sql_template, cached_page.offset, cached_page.page_size
                )

                if next_page is None:
                    # Aggregates and other unpaginatable queries land here.
                    logging.info("Cached query cannot be paginated: %s", cached_page.sql_template)
                    span3.set_status(Status(StatusCode.OK))
                    process_time = time.time() - start_time
                    no_more_pages = (
                        "That result cannot be paged any further. "
                        "Please ask a new question."
                    )
                    async with pool.acquire() as conn:
                        await conn.execute(UPDATE_USER_QUOTA_USAGE, int(user_id), token_usage["total_tokens"])
                    parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, no_more_pages)
                    parent_span.set_status(Status(StatusCode.OK))
                    parent_span.end()
                    return Response(
                        status_code=status.HTTP_200_OK,
                        content=no_more_pages,
                        headers={"X-Response-Time": f"{process_time:.6f} seconds"},
                        media_type="text/plain"
                    )

                sql_template, page_offset, page_size = next_page

                # Re-substitute the facility codes from THIS request, so a user
                # whose facility access changed between pages cannot keep
                # reading the scope they had on page 1. Re-validate too: the
                # rewritten statement must clear the same safety bar as a
                # freshly generated one.
                paginated_sql = format_sql_query(sql_template, facm_code)
                try:
                    paginated_sql = validate_sql(paginated_sql)
                except ValueError as safety_error:
                    logging.error(f"Paginated SQL failed safety validation: {safety_error}")
                    span3.record_exception(safety_error)
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="The AI generated a potentially unsafe SQL query.",
                    )

                span3.set_attributes({
                    "sql.query": str(paginated_sql),
                    "pagination.offset": page_offset,
                    "pagination.page_size": page_size,
                    # Mirrors the attribute sql_agent sets, so the truncation
                    # check and eval payload below read the same key on both paths.
                    "llm.output_messages.0.message.content": str(paginated_sql),
                })

                table_rows = await execute_ai_generated_sql(paginated_sql, pool)

                span3.set_status(Status(StatusCode.OK))

                num_rows = len(table_rows)
                num_cols = len(table_rows[0]) if num_rows > 0 else 0

                logging.info(
                    "Paginated result from %s database - offset %s, %s rows x %s cols",
                    database_name, page_offset, num_rows, num_cols,
                )

                span3.set_attributes({
                    "sql.row_count": num_rows,
                    "sql.columns_count": num_cols,
                })

        else:
            user_details = await fetch_user_details(user_id, pool)

            with tracer.start_as_current_span("3. sql_generation", context=ctx, kind=SpanKind.CLIENT) as span3:
                span3.set_attributes({
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                    "info": "LLM call to generate SQL",
                    SpanAttributes.USER_ID: str(user_id)
                })

                ## Prompt = Instructions + table schema + example + user_input
                # resolved_query, not raw_user_input: a follow-up like "only the
                # critical ones" carries no subject for the SQL model to work from.
                sql_generation_prompt = format_sql_prompt(
                    user_input=resolved_query,
                    user_details=user_details,
                    facm_code=facm_code,
                    table_schema=table_schema,
                    context_for_sql_generation=context_for_sql_generation,
                    chat_history=last_n_user_queries,
                )

                span3.set_attributes({
                    "llm.system": "bedrock",
                    "llm.model_name": str(SQL_MODEL_ID),
                    "llm.input_messages.0.message.role": "system",
                    "llm.input_messages.0.message.content":  str(sql_generation_prompt)
                })

                sql_result = await sql_agent(start_time, sql_generation_prompt, pool, sql_generation_model, span3, loop, trace, facm_code)
                table_rows = sql_result.rows

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

                # Page state for this fresh query, read back off the SQL that ran.
                sql_template = sql_result.sql_template
                page_offset = extract_offset(sql_result.executed_sql)
                page_size = extract_limit(sql_result.executed_sql) or DEFAULT_PAGE_SIZE

        # Remember this page so the next "show more" can continue from it. Keyed
        # by (database_name, user_id) - user ids are only unique within a tenant.
        if sql_template:
            last_query_cache.put(
                database_name,
                user_id,
                CachedQuery(
                    sql_template=sql_template,
                    offset=page_offset,
                    page_size=page_size,
                    table_schema=table_schema,
                    context_for_user_response=context_for_user_response,
                    original_user_input=original_user_input,
                    created_at=time.time(),
                ),
            )


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
                    "llm.model_name": str(SQL_MODEL_ID),
                    "llm.input_messages.0.message.role": "system",
                    "llm.input_messages.0.message.content": str(refine_prompt),
                })

                try:
                    def _refine_generation(prompt, span):
                        with trace.use_span(span):
                            return sql_generation_model.generate_response(prompt, span=span)

                    refine_result = await asyncio.gather(
                        loop.run_in_executor(
                            get_bedrock_executor(),
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
            table_rows_str = tabulate(data, headers="keys", tablefmt="simple")
        
            # --- Two-pass check: is this page one of several? ---
            # A full page is the signal that more rows may exist. Counting the
            # unpaginated query tells us whether to offer "more", and on page 2+
            # lets us report which slice the user is looking at.
            if num_rows == page_size and sql_template:
                with tracer.start_as_current_span("3c. truncation_check", context=ctx, kind=SpanKind.CLIENT) as span3c:
                    span3c.set_attributes({
                        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.RETRIEVER.value,
                        "info": "COUNT query to detect whether more pages are available",
                        SpanAttributes.USER_ID: str(user_id),
                    })

                    # sql_template is already fence-free and semicolon-free.
                    # Drop the outer paging clauses, then count what remains.
                    count_inner = re.sub(r"(?i)\bOFFSET\s+\d+", "", sql_template)
                    count_inner = re.sub(r"(?i)\bLIMIT\s+\d+", "", count_inner).strip()
                    count_sql = f"SELECT COUNT(*) FROM ({count_inner}) AS _count_sub"
                    count_sql = format_sql_query(count_sql, facm_code)

                    try:
                        # Same safety bar as any other statement we execute.
                        count_sql = validate_sql(count_sql)

                        span3c.set_attribute("sql.query", count_sql)

                        total_count = await execute_count_query(count_sql, pool)

                        span3c.set_attribute("sql.total_count", total_count or 0)

                        shown_through = page_offset + num_rows
                        if total_count and total_count > shown_through:
                            truncation_note = (
                                f"\n\n[SYSTEM NOTE: This is records {page_offset + 1}-{shown_through} "
                                f"of {total_count} matching records. Tell the user which range they are "
                                f"seeing out of the total, and that they can reply 'more' for the next page.]"
                            )
                            table_rows_str += truncation_note
                            span3c.set_attribute("truncation.detected", True)
                        else:
                            span3c.set_attribute("truncation.detected", False)

                        span3c.set_status(Status(StatusCode.OK))
                    except Exception as e:
                        # A failed count only costs the "more" hint, so log and
                        # carry on with the rows we already have.
                        logging.warning(f"Failed to fetch total count for truncated results: {e}")
                        span3c.record_exception(e)
                        span3c.set_status(Status(StatusCode.ERROR, description=str(e)))
        
            table_rows = table_rows_str

    ## --------------------------------------------------------------------------------------------------- #
    ##    Generate Final Response for the user input
    ## --------------------------------------------------------------------------------------------------- #

        # On a follow-up the user's message ("more") describes neither the data
        # nor the position in the result set, so both are stated explicitly.
        pagination_context = ""
        if is_pagination:
            context_parts = [
                f'The user\'s original question was: "{original_user_input}".'
            ]
            if num_rows:
                shown_from = page_offset + 1
                shown_to = page_offset + num_rows
                if total_count:
                    context_parts.append(
                        f"The fetched data below is records {shown_from}-{shown_to} "
                        f"of {total_count} matching records in total."
                    )
                else:
                    context_parts.append(
                        f"The fetched data below is records {shown_from}-{shown_to}, "
                        f"continuing from the previous page. This is the final page."
                    )
            else:
                context_parts.append(
                    "There are no further records beyond the ones already shown."
                )
            pagination_context = " ".join(context_parts)
            logging.info("Pagination context for final response: %s", pagination_context)

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
                                                                             chat_history=last_n_user_queries,
                                                                             pagination_context=pagination_context)

                    span4.set_attributes({
                        "llm.system": "bedrock",
                        "llm.model_name": str(CHAT_MODEL_ID),
                        "llm.input_messages.0.message.role": "system",
                        "llm.input_messages.0.message.content":  str(response_to_user_prompt),
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
                                            db_pool=pool, user_id=user_id, quota_usage_update_query=UPDATE_USER_QUOTA_USAGE,
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

        # The reference ties this log line, the span and the client's message
        # together, so the full traceback stays server-side without losing the
        # ability to investigate a specific report.
        ref = new_error_reference()
        logging.error("[%s] Unhandled error in chat_completion", ref, exc_info=True)
        parent_span.set_attribute("error.reference", ref)
        parent_span.record_exception(e)
        parent_span.set_status(Status(StatusCode.ERROR, description=str(e)))
        parent_span.end()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=client_error_detail(
                "Something went wrong while answering that question.", ref
            ),
        )