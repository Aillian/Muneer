import re
from pathlib import Path

import pytest
from dotenv import dotenv_values
from openai import NotFoundError, OpenAI
from starlette.testclient import TestClient

from mock_muneer.app import create_app
from mock_muneer.scenarios import FALLBACK, SCENARIOS, pick
from pipeline.placeholders import PLACEHOLDER_NAME, PlaceholderFiller

KEY = "test-key"
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def http(**options):
    return TestClient(create_app(**options))


def sdk(**options):
    # The real OpenAI SDK, talking to the mock in-process.
    return OpenAI(base_url="http://testserver/v1", api_key=KEY, http_client=http(api_key=KEY, **options))


def ask(message):
    return {"model": "muneer", "messages": [{"role": "user", "content": message}]}


# ---- routes --------------------------------------------------------------


def test_health_checks_are_open():
    client = http(api_key=KEY)
    assert client.get("/health").status_code == 200
    assert client.get("/ping").status_code == 200


def test_other_routes_refuse_a_missing_key_like_the_real_server():
    # The real server checks the key before routing, so unknown paths answer 401 too.
    client = http(api_key=KEY)
    assert client.post("/invocations", json=ask("hi")).status_code == 401
    assert client.post("/invocations", json=ask("hi"), headers={"Authorization": f"Bearer {KEY}"}).status_code == 404


def test_lists_the_model():
    assert [m.id for m in sdk().models.list().data] == ["muneer"]


@pytest.mark.parametrize("path, method", [("/v1/models", "GET"), ("/v1/chat/completions", "POST")])
def test_requires_the_key_when_one_is_set(path, method):
    client = http(api_key=KEY)
    body = ask("hi") if method == "POST" else None
    assert client.request(method, path, json=body).status_code == 401
    assert client.request(method, path, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.request(method, path, json=body, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_open_when_no_key_is_set():
    assert http().post("/v1/chat/completions", json=ask("hi")).status_code == 200


def test_unknown_model_is_not_found():
    with pytest.raises(NotFoundError):
        sdk().chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("body", [{"model": "muneer"}, {"model": "muneer", "messages": []}, ["not", "an", "object"]])
def test_rejects_bad_requests(body):
    assert http().post("/v1/chat/completions", json=body).status_code == 400


# ---- replies -------------------------------------------------------------


@pytest.mark.parametrize(
    "message, scenario",
    [
        ("Hello!", "greeting"),
        ("Who are you?", "identity"),
        ("Hi, what is your name?", "identity"),
        ("Tell me about Islam", "about_islam"),
        ("How do I become a Muslim?", "convert"),
        ("I said it!", "said_shahada"),
        ("I said the shahada", "said_shahada"),
        ("How do I pray?", "prayer"),
        ("What's your email?", "contact"),
        ("Where can I subscribe to the newsletter?", "newsletter"),
        ("Where is God?", "creed_error"),
        ("I'm not sure yet", "pushy"),
        ("What do you think about politics?", "controversial"),
        ("Can I talk to a real person?", "transfer"),
        ("This is useless", "upset"),
        ("Stop messaging me", "stop"),
        ("Please unsubscribe me", "stop"),
        ("Is the Quran preserved?", "fallback"),
        ("This is fine", "fallback"),  # "hi" inside "this" is not a greeting
    ],
)
def test_picks_the_scenario(message, scenario):
    assert pick(message).name == scenario


def test_answers_the_visitors_last_message():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hello, welcome to our live help."},
        {"role": "user", "content": "Who are you?"},
    ]
    reply = sdk().chat.completions.create(model="muneer", messages=messages)
    assert reply.choices[0].message.content.startswith("I'm Daee, an AI Islamic Advisor")
    assert reply.choices[0].finish_reason == "stop"
    assert reply.system_fingerprint == "mock-muneer/identity"
    assert reply.usage.completion_tokens > 0


def test_streams_like_the_real_server():
    client = sdk()
    whole = client.chat.completions.create(**ask("How do I pray?")).choices[0].message.content
    chunks = client.chat.completions.create(**ask("How do I pray?"), stream=True)
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == whole


def test_every_scenario_is_reachable():
    for scenario in SCENARIOS:
        assert pick(scenario.phrases[0]).name == scenario.name
    assert pick("").name == FALLBACK.name


def test_placeholders_in_replies_are_in_the_example_profile_except_the_deliberately_missing_one():
    profile = PlaceholderFiller.from_env(dotenv_values(ENV_EXAMPLE)).profile
    used = {name for s in SCENARIOS for name in re.findall(r"\[(" + PLACEHOLDER_NAME.pattern + r")\]", s.reply)}
    assert used - set(profile) == {"newsletter_url"}


# ---- python -m mock_muneer ---------------------------------------------------


@pytest.fixture
def started(monkeypatch):
    """Records where the mock would listen, instead of starting it."""
    from mock_muneer import __main__ as cli

    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port: calls.append((host, port)))
    for name in ("MOCK_HOST", "MOCK_PORT"):
        monkeypatch.setenv(name, "")  # so whatever the settings file sets is removed afterwards
        monkeypatch.delenv(name)
    return cli.main, calls


def test_the_address_and_port_can_be_set_in_the_settings_file(tmp_path, started):
    main, calls = started
    (tmp_path / ".env").write_text("MOCK_HOST=0.0.0.0\nMOCK_PORT=8123\n")
    main(["--env-file", str(tmp_path / ".env")])
    assert calls == [("0.0.0.0", 8123)]


def test_command_line_options_win_over_the_settings_file(tmp_path, started):
    main, calls = started
    (tmp_path / ".env").write_text("MOCK_HOST=0.0.0.0\nMOCK_PORT=8123\n")
    main(["--env-file", str(tmp_path / ".env"), "--host", "127.0.0.2", "--port", "9000"])
    assert calls == [("127.0.0.2", 9000)]


def test_by_default_the_mock_listens_on_this_machine_only(tmp_path, started):
    main, calls = started
    main(["--env-file", str(tmp_path / "missing.env")])
    assert calls == [("127.0.0.1", 8000)]
