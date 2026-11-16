"""Bound request bodies before authentication, multipart parsing or database work."""
from tempfile import SpooledTemporaryFile

from starlette.responses import PlainTextResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await PlainTextResponse("Invalid Content-Length", status_code=400)(scope, receive, send)
            return
        if declared_size > self.max_bytes:
            await PlainTextResponse("Request body too large", status_code=413)(scope, receive, send)
            return

        # Replay only a completely checked body. Partial multipart files never reach
        # the parser, including when Content-Length is absent or understates size.
        with SpooledTemporaryFile(max_size=65_536) as body:
            total = 0
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > self.max_bytes:
                    await PlainTextResponse("Request body too large", status_code=413)(scope, receive, send)
                    return
                body.write(chunk)
                more_body = message.get("more_body", False)
            body.seek(0)
            replayed = False

            async def checked_receive():
                nonlocal replayed
                if replayed:
                    return await receive()
                chunk = body.read(65_536)
                more = body.tell() < total
                replayed = not more
                return {"type": "http.request", "body": chunk, "more_body": more}

            await self.app(scope, checked_receive, send)
