"""
Custom Starlette StreamingResponse wrapper with OpenTelemetry parent span tracking and post-stream database quota deduction.
"""

from __future__ import annotations

import typing

from starlette.background import BackgroundTask
from starlette.types import Send
from starlette.responses import StreamingResponse

# Custom Imports
from opentelemetry.trace import Status, StatusCode
from openinference.semconv.trace import SpanAttributes
import asyncio
from database import update_user_quota

Content = typing.Union[str, bytes, memoryview]
SyncContentStream = typing.Iterable[Content]
AsyncContentStream = typing.AsyncIterable[Content]
ContentStream = typing.Union[AsyncContentStream, SyncContentStream]


class CustomStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: ContentStream,
        db_pool,
        user_id,
        quota_usage_update_query,
        parent_span,
        buffer_container,
        logging,
        status_code: int = 200,
        headers: typing.Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
        token_usage: dict = None,
    ) -> None:
        super().__init__(
            content=content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
        self.pool = db_pool
        self.user_id = user_id
        self.quota_usage_update_query = quota_usage_update_query
        self.parent_span = parent_span
        self.buffer_container = buffer_container
        self.logging = logging
        self.token_usage = token_usage or {}

    async def _update_user_quota(self):
        """
        Updates the user AI quota in PostgreSQL after response stream completion.
        """
        try:
            async with self.pool.acquire() as conn:
                await update_user_quota(conn, self.user_id, self.token_usage)
                total_spent = (
                    sum(
                        m.get("total_tokens", 0)
                        for m in self.token_usage.values()
                        if isinstance(m, dict)
                    )
                    if isinstance(self.token_usage, dict)
                    else 0
                )
                self.logging.info(
                    f"User Quota Updated after final response generation. Spent: {total_spent} tokens across models."
                )
        except Exception as e:
            self.logging.error(f"Failed to update user quota in database: {e}")

    async def stream_response(self, send: Send) -> None:

        update_quota_after_final_response = False

        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": self.status_code,
                    "headers": self.raw_headers,
                }
            )
            async for chunk in self.body_iterator:
                if not isinstance(chunk, (bytes, memoryview)):
                    chunk = chunk.encode(self.charset)
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )

            await send({"type": "http.response.body", "body": b"", "more_body": False})

            # Enable if user quota usage has to be updated after final response
            update_quota_after_final_response = True
            await asyncio.shield(self._update_user_quota())

        except Exception as e:
            self.parent_span.record_exception(e)
            self.parent_span.set_status(Status(StatusCode.ERROR, description=str(e)))
            raise
        finally:
            full_output = "".join(
                c if isinstance(c, str) else c.decode("utf-8", errors="ignore")
                for c in self.buffer_container
            )
            self.parent_span.set_attribute(
                SpanAttributes.OUTPUT_VALUE, full_output
            )

            if update_quota_after_final_response:
                self.parent_span.set_attribute(
                    "metadata.user_quota_details.updated_after_final_response", True
                )
                self.parent_span.set_status(Status(StatusCode.OK))
            else:
                try:
                    await asyncio.shield(self._update_user_quota())
                    self.parent_span.set_attribute(
                        "metadata.user_quota_details.updated_on_stream_failure", True
                    )
                except Exception as e_quota:
                    self.logging.error(
                        f"Failed to update user quota on stream failure: {e_quota}"
                    )

            self.parent_span.end()

