import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from muneer_auth import ApiKeyMiddleware
from muneer_sampling import PenaltyDefaultsMiddleware, defaults_from_env, with_defaults

DEFAULTS = {"frequency_penalty": 0.2, "presence_penalty": 0.0}
KEY = "test-key-0123456789"


def echo_app(seen=None):
    """An app that answers with the body and length it was given, as vLLM would read them."""

    async def echo(request):
        body = await request.body()
        if seen is not None:
            seen.append(body)
        return JSONResponse({"body": body.decode("utf-8"), "length": request.headers.get("content-length")})

    paths = ["/v1/chat/completions", "/v1/chat/completions/batch", "/v1/completions", "/v1/responses",
             "/invocations", "/v1/embeddings", "/v1/messages", "/tokenize"]
    return Starlette(routes=[Route(path, echo, methods=["GET", "POST"]) for path in paths])


@pytest.fixture
def client():
    app = echo_app()
    app.add_middleware(PenaltyDefaultsMiddleware, defaults=DEFAULTS)
    return TestClient(app)


def sent(response):
    data = response.json()
    assert int(data["length"]) == len(data["body"].encode("utf-8"))
    return json.loads(data["body"]) if data["body"] else None


# ---- the body ----


def test_missing_penalties_are_added():
    body = json.dumps({"model": "muneer", "messages": [{"role": "user", "content": "Salam"}]}).encode()
    assert json.loads(with_defaults(body, DEFAULTS)) == {
        "model": "muneer", "messages": [{"role": "user", "content": "Salam"}],
        "frequency_penalty": 0.2, "presence_penalty": 0.0,
    }


def test_a_request_keeps_its_own_penalties():
    body = json.dumps({"model": "muneer", "frequency_penalty": 0.5, "presence_penalty": -1}).encode()
    assert with_defaults(body, DEFAULTS) == body


def test_a_null_penalty_gets_the_default():
    body = json.dumps({"model": "muneer", "frequency_penalty": None, "presence_penalty": 0.3}).encode()
    assert json.loads(with_defaults(body, DEFAULTS)) == {"model": "muneer", "frequency_penalty": 0.2, "presence_penalty": 0.3}


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_a_body_that_is_not_a_json_object_is_left_alone(body):
    assert with_defaults(body, DEFAULTS) == body


def test_defaults_come_from_the_environment():
    env = {"MUNEER_FREQUENCY_PENALTY": "0.2", "MUNEER_PRESENCE_PENALTY": "0.0"}
    assert defaults_from_env(env) == DEFAULTS
    assert defaults_from_env({"MUNEER_FREQUENCY_PENALTY": " "}) == {}


# ---- the middleware ----


@pytest.mark.parametrize(
    "path", ["/v1/chat/completions", "/v1/chat/completions/batch", "/v1/completions", "/v1/responses", "/invocations"]
)
def test_generation_requests_get_the_defaults(client, path):
    request = {"model": "muneer", "messages": [{"role": "user", "content": "مرحبا"}]}
    assert sent(client.post(path, json=request)) == {**request, **DEFAULTS}


def test_a_request_with_its_own_penalties_is_unchanged(client):
    request = {"model": "muneer", "frequency_penalty": 1.0, "presence_penalty": 0.5}
    assert sent(client.post("/v1/chat/completions", json=request)) == request


def test_a_body_sent_in_pieces_is_read_whole(client):
    pieces = [b'{"model": "muneer", ', b'"messages": []}']
    response = client.post("/v1/chat/completions", content=iter(pieces))
    assert sent(response) == {"model": "muneer", "messages": [], **DEFAULTS}


@pytest.mark.parametrize("path", ["/v1/embeddings", "/v1/messages", "/tokenize"])
def test_other_routes_are_untouched(client, path):
    assert sent(client.post(path, json={"model": "muneer"})) == {"model": "muneer"}


def test_other_methods_are_untouched(client):
    assert client.get("/v1/chat/completions").json() == {"body": "", "length": None}


def test_without_defaults_nothing_changes():
    app = echo_app()
    app.add_middleware(PenaltyDefaultsMiddleware, defaults={})
    response = TestClient(app).post("/v1/chat/completions", json={"model": "muneer"})
    assert sent(response) == {"model": "muneer"}


def test_the_key_is_checked_before_the_body_is_read():
    # As launch.py orders them: vLLM adds the middleware in turn, and the last one added runs first.
    seen = []
    app = echo_app(seen)
    app.add_middleware(PenaltyDefaultsMiddleware, defaults=DEFAULTS)
    app.add_middleware(ApiKeyMiddleware, api_key=KEY)
    client = TestClient(app)
    assert client.post("/v1/chat/completions", json={"model": "muneer"}).status_code == 401
    assert seen == []
    response = client.post("/v1/chat/completions", json={"model": "muneer"}, headers={"Authorization": f"Bearer {KEY}"})
    assert sent(response) == {"model": "muneer", **DEFAULTS}
