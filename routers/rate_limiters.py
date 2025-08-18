from fastapi import HTTPException, status
from opentelemetry.trace import Status, StatusCode, set_span_in_context, SpanKind
from openinference.semconv.trace import SpanAttributes, OpenInferenceSpanKindValues
import json

from config.logger_config import get_logger
from database.db_connection import connect_to_db
from database.db_queries import CHECK_IF_USER_QUOTA_LIMIT_EXISTS, CHECK_IF_USER_QUOTA_LEFT
from models.data_models import ChatCompletionRequest

logging = get_logger(__name__)

async def rate_limiter(request: ChatCompletionRequest, tracer, parent_span):

    database_name = request.database_name
    user_id = int(request.user_id)
    user_input = request.user_input

    pool = await connect_to_db(database_name)

    parent_span.set_attributes({SpanAttributes.INPUT_VALUE: user_input,})
    ctx = set_span_in_context(parent_span)

    with tracer.start_as_current_span("Rate Limiter", context=ctx, kind=SpanKind.INTERNAL) as rate_limiter:
        rate_limiter.set_attributes({
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
                "info": "Rate Limiter"
            })

        async with pool.acquire() as conn:
            user_id_quota_exists = await conn.fetchrow(CHECK_IF_USER_QUOTA_LIMIT_EXISTS, user_id)

            if not user_id_quota_exists:
                error=f"For the user: '{user_id}' in '{database_name}'. No quota is aasigned"
                logging.exception(error)
                rate_limiter.set_status(Status(StatusCode.ERROR, description=str(error)))
                parent_span.set_status(Status(StatusCode.ERROR))

                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No quota assigned"
                )
            logging.info(f"Quota exists for user: {user_id}")

            row = await conn.fetchrow(CHECK_IF_USER_QUOTA_LEFT, user_id)

            if not row:
                error=f"For the user: '{user_id}' in '{database_name}'. Rate limit exceeded"
                logging.exception(error)
                rate_limiter.set_status(Status(StatusCode.ERROR, description=str(error)))
                parent_span.set_status(Status(StatusCode.ERROR))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded"
                )
            
            user_quota = row['uaq_quota_limit']
            user_quota_used = row['uaq_used_count']

            rate_limiter.set_attributes({
                SpanAttributes.METADATA: json.dumps({
                    "user_quota_details": {
                        "quota" : user_quota,
                        "current_usage" : user_quota_used
                    }
                })
            })

            rate_limiter.set_status(Status(StatusCode.OK))

            logging.info(f"Quota: {user_quota}\nUsage: {user_quota_used}")

        return {"pool": pool, "request": request, "quota": user_quota, "current_usage": user_quota_used, "ctx": ctx, "parent_span": parent_span}