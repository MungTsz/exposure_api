"""
Rejects request bodies larger than settings.max_ingest_body_bytes with 413,
before any route stores or processes them.

A request that declares its size (Content-Length) is rejected before its
body is read. A chunked request without one is counted while it streams in
and rejected as soon as it passes the limit, so at most the limit is ever
held in memory.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BODY_TOO_LARGE_STATUS = 413


def too_large_detail(max_bytes: int) -> str:
    return f"request body too large: limit is {max_bytes} bytes"


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            response = JSONResponse({"detail": too_large_detail(self.max_bytes)}, status_code=BODY_TOO_LARGE_STATUS)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # An HTTPException is passed through by FastAPI's body
                    # reader (any other error becomes a 400).
                    raise HTTPException(BODY_TOO_LARGE_STATUS, too_large_detail(self.max_bytes))
            return message

        await self.app(scope, limited_receive, send)
