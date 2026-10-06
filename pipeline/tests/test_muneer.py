import pytest
from openai import OpenAI
from starlette.testclient import TestClient

from mock_muneer.app import create_app
from pipeline.config import ConfigError
from pipeline.muneer import HISTORY_CHARS, MuneerClient, MuneerUnavailable, trim_history
from pipeline.tests.fakes import FakeOpenAI

KEY = "test-key"


def with_mock():
    """A client talking to the mock model server, in-process."""
    http = TestClient(create_app(api_key=KEY))
    return MuneerClient(OpenAI(base_url="http://testserver/v1", api_key=KEY, max_retries=0, http_client=http))


def with_fake(*outcomes):
    fake = FakeOpenAI(*outcomes)
    return MuneerClient(fake.client()), fake


def user(text):
    return {"role": "user", "content": text}


def assistant(text):
    return {"role": "assistant", "content": text}


def test_returns_the_raw_reply():
    reply = with_mock().reply([user("Who are you?")])
    assert reply.text.startswith("I'm Daee, an AI Islamic Advisor")  # raw: not filled
    assert reply.source == "mock-muneer/identity"
    assert reply.model == "muneer" and reply.finish_reason == "stop"
    assert reply.output_tokens > 0 and reply.latency_ms >= 0


def test_sends_only_the_conversation():
    client, fake = with_fake("Hello!")
    history = [user("hi"), assistant("Hello, welcome to our live help."), user("Who are you?")]
    client.reply(history)
    # No system prompt and no sampling overrides: the server adds its own prompt, and its defaults apply.
    assert fake.requests[0] == {"model": "muneer", "messages": history}


def test_retries_once():
    client, fake = with_fake(500, "Hello!")
    assert client.reply([user("hi")]).text == "Hello!"
    assert len(fake.requests) == 2


def test_an_empty_reply_is_retried():
    client, fake = with_fake("   ", "Hello!")
    assert client.reply([user("hi")]).text == "Hello!"


def test_gives_up_after_the_retry():
    client, fake = with_fake(500, 503)
    with pytest.raises(MuneerUnavailable, match="after 2 attempts"):
        client.reply([user("hi")])


def test_a_long_history_is_trimmed_to_fit():
    client, fake = with_fake("Hello!")
    history = [m for n in range(40) for m in (user(f"question {n} " + "x" * 400), assistant("y" * 400))]
    history.append(user("latest question"))
    client.reply(history)
    sent = fake.requests[0]["messages"]
    assert sum(len(m["content"]) for m in sent) <= HISTORY_CHARS
    assert sent[0]["role"] == "user" and sent[-1] == user("latest question")


# ---- trim_history ------------------------------------------------------------


def test_trim_keeps_the_most_recent_messages_within_the_budget():
    history = [user("aaaa"), assistant("bbbb"), user("cccc"), assistant("dddd"), user("eeee")]
    assert trim_history(history, 12) == [user("cccc"), assistant("dddd"), user("eeee")]


def test_trim_starts_with_a_visitor_message():
    history = [user("aaaa"), assistant("bbbb"), user("cc")]
    assert trim_history(history, 6) == [user("cc")]


def test_trim_always_keeps_the_latest_message():
    assert trim_history([user("a" * 100)], 10) == [user("a" * 100)]


# ---- settings ------------------------------------------------------------------


def test_from_env():
    client = MuneerClient.from_env({"MUNEER_BASE_URL": "http://localhost:8000/v1", "MUNEER_API_KEY": "k"})
    assert client.model == "muneer"
    assert str(client.client.base_url) == "http://localhost:8000/v1/"
    assert client.client.api_key == "k" and client.client.timeout == 60 and client.client.max_retries == 0


def test_from_env_works_without_a_key_for_an_open_mock():
    assert MuneerClient.from_env({"MUNEER_BASE_URL": "http://localhost:8000/v1"}).client.api_key == "no-key"


def test_from_env_needs_the_server_address():
    with pytest.raises(ConfigError, match="MUNEER_BASE_URL"):
        MuneerClient.from_env({})
