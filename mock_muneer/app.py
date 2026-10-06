"""A scripted stand-in for Muneer's OpenAI-compatible API.

It answers the visitor's last message with a scripted scenario (see
scenarios.py), so the pipeline and the app can run without a GPU. Each reply
carries `system_fingerprint: "mock-muneer/<scenario>"` to show which script
answered.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mock_muneer.scenarios import pick


def create_app(api_key: str | None = None, delay_ms: int = 0, model: str = "muneer") -> Starlette:
    """api_key: if set, every route except /health and /ping needs
    `Authorization: Bearer <api_key>`, like the real server."""

    async def health(request: Request) -> Response:
        return Response(status_code=200)

    async def models(request: Request) -> Response:
        return JSONResponse(
            {"object": "list", "data": [{"id": model, "object": "model", "created": 0, "owned_by": "mock-muneer"}]}
        )

    async def chat_completions(request: Request) -> Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "The request body is not valid JSON.")
        if not isinstance(body, dict):
            return _error(400, "The request body must be a JSON object.")
        if body.get("model") != model:
            return _error(404, f"The model `{body.get('model')}` does not exist.")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            return _error(400, "`messages` must be a non-empty list of messages.")

        visitor = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        scenario = pick(visitor)
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)

        reply = {
            "id": f"chatcmpl-mock-{uuid.uuid4().hex[:12]}",
            "created": int(time.time()),
            "model": model,
            "system_fingerprint": f"mock-muneer/{scenario.name}",
        }
        if body.get("stream"):
            return StreamingResponse(_stream(reply, scenario.reply), media_type="text/event-stream")
        prompt_tokens = sum(len(str(m.get("content") or "").split()) for m in messages)
        completion_tokens = len(scenario.reply.split())
        return JSONResponse({
            **reply,
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": scenario.reply}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        })

    app = Starlette(routes=[
        Route("/health", health),
        Route("/ping", health, methods=["GET", "POST"]),
        Route("/v1/models", models),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    ])
    if api_key:
        app.add_middleware(_RequireKey, api_key=api_key)
    return app


class _RequireKey:
    """Checks the key on every route but the health checks, as server/muneer_auth.py does."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.key = api_key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in ("/health", "/ping"):
            scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
            if not (scheme.lower() == "bearer" and secrets.compare_digest(token.strip().encode(), self.key)):
                await _unauthorized()(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _stream(reply: dict, text: str):
    def chunk(delta: dict, finish_reason: str | None = None) -> str:
        event = {**reply, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
        return f"data: {json.dumps(event)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    words = text.split(" ")
    for i, word in enumerate(words):
        yield chunk({"content": word if i == len(words) - 1 else word + " "})
    yield chunk({}, "stop")
    yield "data: [DONE]\n\n"


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _error(status: int, message: str) -> JSONResponse:
    kind = "NotFoundError" if status == 404 else "BadRequestError"
    return JSONResponse({"error": {"message": message, "type": kind, "param": None, "code": status}}, status_code=status)
