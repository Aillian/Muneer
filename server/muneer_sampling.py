"""Muneer's default frequency and presence penalties, added to OpenAI-style generation requests.

vLLM takes server-wide defaults for the temperature, top-p, top-k, the
repetition penalty and the reply cap (launch.py passes them with
--override-generation-config), but not for the frequency and presence
penalties: its endpoints read those only from the request. This middleware
adds them to every request to an OpenAI-style generation route that leaves them
out, so the app and anyone calling the OpenAI API get the same sampling. A
request that sets them keeps its own values. vLLM's other API styles
(/v1/messages, /cohere/v2/chat, /inference/v1/generate) keep its default of 0.

vLLM loads it with `--middleware muneer_sampling.PenaltyDefaultsMiddleware`,
inside the API-key check, and it reads the values from
MUNEER_FREQUENCY_PENALTY and MUNEER_PRESENCE_PENALTY, which launch.py sets.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The OpenAI-style routes that generate text. /invocations takes the same chat or completion bodies.
PATHS = frozenset(
    {"/v1/chat/completions", "/v1/chat/completions/batch", "/v1/completions", "/v1/responses", "/invocations"}
)
ENV_NAMES = {"frequency_penalty": "MUNEER_FREQUENCY_PENALTY", "presence_penalty": "MUNEER_PRESENCE_PENALTY"}
# Rewritten bodies get a new length; any other framing of the old body no longer applies.
FRAMING_HEADERS = frozenset({b"content-length", b"transfer-encoding"})


def defaults_from_env(env: Mapping[str, str]) -> dict[str, float]:
    return {field: float(env[name]) for field, name in ENV_NAMES.items() if env.get(name, "").strip()}


def with_defaults(body: bytes, defaults: Mapping[str, float]) -> bytes:
    """The body with each default the request leaves out, or sets to null, added.

    A body that isn't a JSON object comes back unchanged, for vLLM to refuse as usual.
    """
    try:
        data = json.loads(body)
    except ValueError:  # UnicodeDecodeError is a ValueError too
        return body
    if not isinstance(data, dict):
        return body
    missing = {field: value for field, value in defaults.items() if data.get(field) is None}
    if not missing:
        return body
    return json.dumps({**data, **missing}).encode("utf-8")


class PenaltyDefaultsMiddleware:
    def __init__(self, app: ASGIApp, defaults: Mapping[str, float] | None = None) -> None:
        self.app = app
        self.defaults = dict(defaults_from_env(os.environ) if defaults is None else defaults)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].removeprefix(scope.get("root_path", "")) not in PATHS
            or not self.defaults
        ):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client left before sending the whole request
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = with_defaults(b"".join(chunks), self.defaults)
        headers = [(name, value) for name, value in scope["headers"] if name.lower() not in FRAMING_HEADERS]
        headers.append((b"content-length", str(len(body)).encode("latin-1")))

        replayed = False

        async def replay() -> Message:
            # The whole body once, then the real channel, which reports a disconnect.
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app({**scope, "headers": headers}, replay, send)
