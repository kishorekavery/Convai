from fastapi import HTTPException, status

from config.logger_config import get_logger
from database.db_connection import connect_to_db
from database.db_queries import CHECK_IF_USER_QUOTA_LIMIT_EXISTS, CHECK_IF_USER_QUOTA_LEFT
from models.data_models import ChatCompletionRequest

logging = get_logger(__name__)

async def rate_limiter(request: ChatCompletionRequest):

    database_name = request.database_name
    user_id = int(request.user_id)

    pool = await connect_to_db(database_name)

    async with pool.acquire() as conn:
        user_id_quota_exists = await conn.fetchrow(CHECK_IF_USER_QUOTA_LIMIT_EXISTS, user_id)

        if not user_id_quota_exists:
            logging.exception(f"For the user: '{user_id}' in '{database_name}'. No quota is aasigned")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No quota assigned"
            )
        logging.info(f"Quota exists for user: {user_id}")

        row = await conn.fetchrow(CHECK_IF_USER_QUOTA_LEFT, user_id)

        if not row:
            logging.exception(f"For the user: '{user_id}' in '{database_name}'. Rate limit exceeded")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded"
            )
        
        user_quota = row['uaq_quota_limit']
        user_quota_used = row['uaq_used_count']

        logging.info(f"Quota: {user_quota}\nUsage: {user_quota_used}")

        return {"pool": pool, "request": request, "quota": user_quota, "current_usage": user_quota_used}