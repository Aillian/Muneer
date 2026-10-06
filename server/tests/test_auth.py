import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from muneer_auth import ApiKeyMiddleware

KEY = "test-key-0123456789"
PUBLIC = ["/health", "/ping"]
# /invocations and /tokenize are the routes vLLM's own --api-key leaves open.
GUARDED = ["/v1/chat/completions", "/v1/models", "/invocations", "/tokenize", "/metrics", "/docs"]


@pytest.fixture
def client():
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route(path, ok, methods=["GET", "POST"]) for path in PUBLIC + GUARDED])
    app.add_middleware(ApiKeyMiddleware, api_key=KEY)
    return TestClient(app)


@pytest.mark.parametrize("path", PUBLIC)
def test_health_checks_are_public(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", GUARDED)
def test_guarded_routes_refuse_missing_key(client, path):
    response = client.post(path)
    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [f"Bearer {KEY}x", "Bearer ", f"Basic {KEY}", KEY, f"Bearer{KEY}"],
)
def test_guarded_routes_refuse_wrong_credentials(client, header):
    assert client.post("/invocations", headers={"Authorization": header}).status_code == 401


@pytest.mark.parametrize("path", GUARDED)
def test_guarded_routes_accept_the_key(client, path):
    assert client.post(path, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_scheme_is_case_insensitive(client):
    assert client.post("/invocations", headers={"Authorization": f"bearer {KEY}"}).status_code == 200


def test_near_miss_paths_are_not_public(client):
    # Only the exact health paths are open.
    assert client.get("/health/../v1/models").status_code in (401, 404)
    assert client.get("/healthz").status_code == 401


def test_options_requests_need_the_key_too(client):
    # The model server has no browser clients, so no CORS preflight is exempt.
    assert client.options("/v1/chat/completions").status_code == 401
    preflight = {"Origin": "https://example.org", "Access-Control-Request-Method": "POST"}
    assert client.options("/v1/chat/completions", headers=preflight).status_code == 401


def test_metrics_stay_closed_to_any_method():
    # vLLM 0.29.0 serves /metrics twice: a GET route, and a Prometheus app
    # mounted with a catch-all path that answers any HTTP method. An OPTIONS
    # request used to slip past the key check and reach that app.
    import re

    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Mount

    async def metrics_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"vllm_requests_total 42.0\n"})

    async def ok(request):
        return PlainTextResponse("ok")

    mount = Mount("/metrics", app=metrics_app)
    mount.path_regex = re.compile("^/metrics(?P<path>.*)$")
    app = Starlette(routes=[Route("/metrics", ok, methods=["GET"]), mount])
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(ApiKeyMiddleware, api_key=KEY)  # outermost, as vLLM adds --middleware last
    client = TestClient(app)

    for method, path in [("OPTIONS", "/metrics"), ("OPTIONS", "/metrics/anything"), ("GET", "/metrics"), ("POST", "/metrics")]:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path)
        assert b"vllm_requests_total" not in response.content
    assert client.get("/metrics", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_reads_key_from_environment(monkeypatch):
    monkeypatch.setenv("MUNEER_API_KEY", KEY)
    ApiKeyMiddleware(app=None)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_refuses_to_start_without_a_key(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MUNEER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MUNEER_API_KEY", value)
    with pytest.raises(RuntimeError, match="MUNEER_API_KEY"):
        ApiKeyMiddleware(app=None)
