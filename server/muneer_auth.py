"""API-key check for every route of Muneer's vLLM server.

vLLM's own --api-key guards only the /v1, /v2, /inference and /cohere
prefixes. Other routes stay open, including /invocations (full chat
completions), /tokenize and /metrics, so anyone who found the port could use
the model. This middleware requires the key everywhere except the two health
checks.

vLLM loads it with `--middleware muneer_auth.ApiKeyMiddleware` and it reads
the key from MUNEER_API_KEY. Clients send `Authorization: Bearer <key>`,
which is what the OpenAI SDKs do with `api_key=...`.

OPTIONS requests need the key too. The model server has no browser clients,
so it never needs CORS preflights. And vLLM serves /metrics through an app
that answers any method, so exempting OPTIONS would leak its metrics.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

PUBLIC_PATHS = frozenset({"/health", "/ping"})


def _digest(value: str) -> bytes:
    # Compare fixed-length hashes so the check doesn't leak the key's length.
    return hashlib.sha256(value.encode("utf-8")).digest()


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp, api_key: str | None = None) -> None:
        key = (api_key if api_key is not None else os.environ.get("MUNEER_API_KEY", "")).strip()
        if not key:
            raise RuntimeError("MUNEER_API_KEY is not set; refusing to serve without authentication.")
        self.app = app
        self._key_digest = _digest(key)

    def _authorized(self, scope: Scope) -> bool:
        scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(_digest(token.strip()), self._key_digest)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan events carry no request; everything else needs the key.
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"].removeprefix(scope.get("root_path", ""))
        if path in PUBLIC_PATHS or self._authorized(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await WebSocketClose(code=1008)(scope, receive, send)
            return
        response = JSONResponse({"error": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)
