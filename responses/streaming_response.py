'''
This is a reimplementation of the FastAPI Responses file with modifications in the StreamingResponse class.

[IMPORTANT] Things to Remember:
    **If the FastAPI version is changed, make sure the reimplement the changes in the new version's Responses file**

Original File Import Statement:
    from fastapi.responses import StreamingResponse

Modified File Import Statement:
    from responses.streaming_response import CustomStreamingResponse as StreamingResponse

Modifications Done:
    - Opentelemetry Tracing of Parent Span for StreamingResponse
    - Update User AI Quota after the streaming ends

FastAPI Version Details:
    Name: fastapi
    Version: 0.115.11
    Summary: FastAPI framework, high performance, easy to learn, fast to code, ready for production
    Home-page: https://github.com/fastapi/fastapi
    Author: 
    Author-email: =?utf-8?q?Sebasti=C3=A1n_Ram=C3=ADrez?= <tiangolo@gmail.com>
    License: 
    Location: /Users/presanth/Code/-Working/async-convai-bedrock-arize-quota/.venv/lib/python3.12/site-packages
    Requires: pydantic, starlette, typing-extensions
    Required-by: 
'''

from __future__ import annotations

import typing

from starlette._utils import collapse_excgroups
from starlette.background import BackgroundTask
from starlette.types import Send
from starlette.responses import StreamingResponse

# Custom Imports
from opentelemetry.trace import Status, StatusCode
from openinference.semconv.trace import SpanAttributes
import asyncio

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
        quoate_usage_update_query,
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
        self.quoate_usage_update_query = quoate_usage_update_query
        self.parent_span = parent_span
        self.buffer_container = buffer_container
        self.logging = logging
        self.token_usage = token_usage or {"total_tokens": 0}

    async def _update_user_quota(self):
        '''
            Updates the user AI quota after the final response
        '''
        
        async with self.pool.acquire() as conn:
            # await conn.execute(self.quoate_usage_update_query, int(self.user_id))
            # self.logging.info("User Quota Updated after final response generation")
            await conn.execute(self.quoate_usage_update_query, int(self.user_id), self.token_usage["total_tokens"])
            self.logging.info(f"User Quota Updated after final response generation. Spent: {self.token_usage['total_tokens']} tokens.")


    async def stream_response(self, send: Send) -> None:
        
        update_quota_after_final_response=False
        
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
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            
            await send({"type": "http.response.body", "body": b"", "more_body": False})

            #changed
            # Enable if user quota usage has to be updated after final response
            update_quota_after_final_response=True
            await asyncio.shield(self._update_user_quota())

        except Exception as e:
            self.parent_span.record_exception(e)
            self.parent_span.set_status(Status(StatusCode.ERROR, description=str(e)))
            raise
        finally:
            self.parent_span.set_attribute(SpanAttributes.OUTPUT_VALUE, "".join(self.buffer_container))
            
            if update_quota_after_final_response:
                self.parent_span.set_attribute("metadata.user_quota_details.updated_after_final_response", True)
                self.parent_span.set_status(Status(StatusCode.OK))
            else:
                try:
                    await asyncio.shield(self._update_user_quota())
                    self.parent_span.set_attribute("metadata.user_quota_details.updated_on_stream_failure", True)
                except Exception as e_quota:
                    self.logging.error(f"Failed to update user quota on stream failure: {e_quota}")
            
            self.parent_span.end()