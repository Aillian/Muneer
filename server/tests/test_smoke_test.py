import pytest

import smoke_test
from smoke_test import GUARDED_ROUTES, USER_AGENT, http_request, server_root


class FakeResponse:
    status = 200

    def read(self, size=-1):
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", urlopen)
    return requests


def test_requests_carry_a_user_agent_cloudflare_lets_through(sent):
    # RunPod's Cloudflare proxy blocks urllib's default "Python-urllib" agent.
    http_request("GET", "https://abc-8000.proxy.runpod.net/health")
    agent = sent[0].get_header("User-agent")
    assert agent == USER_AGENT and not agent.startswith("Python-urllib")


def test_only_requests_with_a_body_are_json(sent):
    http_request("OPTIONS", "http://x/metrics")
    http_request("POST", "http://x/invocations", {"model": "muneer"})
    assert sent[0].get_header("Content-type") is None and sent[0].data is None
    assert sent[1].get_header("Content-type") == "application/json" and sent[1].data == b'{"model": "muneer"}'
    assert [r.get_method() for r in sent] == ["OPTIONS", "POST"]


@pytest.mark.parametrize(
    "url",
    [
        "https://abc-8000.proxy.runpod.net",
        "https://abc-8000.proxy.runpod.net/",
        "https://abc-8000.proxy.runpod.net/v1",  # the form MUNEER_BASE_URL uses
        "https://abc-8000.proxy.runpod.net/v1/",
    ],
)
def test_server_root_accepts_both_address_forms(url):
    assert server_root(url) == "https://abc-8000.proxy.runpod.net"


def test_checks_the_routes_vllm_leaves_open():
    assert {("POST", "/invocations"), ("POST", "/tokenize"), ("OPTIONS", "/metrics")} <= set(GUARDED_ROUTES)
