import json
import re
from collections import Counter

import httpx
import pytest

from pipeline.config import ConfigError
from pipeline.conversation import Message
from pipeline.guardrail import (
    ATTEMPTS,
    HISTORY_LIMIT,
    PROMPT_FILE,
    Guardrail,
    GuardrailUnavailable,
    Label,
)
from pipeline.tests.fakes import FakeOpenAI

CONVERSATION = [Message("visitor", "Where is God?")]
REPLY = "God is present everywhere in His essence."


def verdict(label="OK", explanation="Nothing wrong."):
    return {"explanation": explanation, "label": label}


def guardrail(*outcomes, **options):
    fake = FakeOpenAI(*outcomes)
    return Guardrail(fake.client(), **options), fake


# ---- verdicts --------------------------------------------------------------


def test_returns_the_verdict():
    g, fake = guardrail(verdict("OK", "A correct, friendly answer."))
    result = g.review(CONVERSATION, "Allah is above the Throne, and His knowledge encompasses all things.")
    assert result.label is Label.OK
    assert result.explanation == "A correct, friendly answer."
    assert result.model == "gpt-4.1-2025-04-14"
    assert (result.input_tokens, result.output_tokens) == (1234, 56)
    assert result.latency_ms >= 0
    assert len(fake.requests) == 1


def test_it_only_classifies():
    # The answer holds a label and its reason, with no room for a rewritten reply.
    g, fake = guardrail(verdict("wrong_from_islamic_point_of_view", "Wrong about Allah's presence."))
    result = g.review(CONVERSATION, REPLY)
    assert result.label is Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW
    assert result.explanation == "Wrong about Allah's presence."
    assert not hasattr(result, "corrected_reply")
    schema = fake.requests[0]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"explanation", "label"}


# ---- the request ---------------------------------------------------------------


def test_sends_the_prompt_the_conversation_and_a_strict_schema():
    g, fake = guardrail(verdict())
    conversation = [Message("visitor", "مرحبا"), Message("muneer", "Hello!"), Message("visitor", "Where is God?")]
    g.review(conversation, REPLY)
    sent = fake.requests[0]

    assert sent["model"] == "gpt-5.6-sol"
    assert sent["store"] is False
    assert "temperature" not in sent  # gpt-5.6-sol takes only its own
    assert sent["reasoning_effort"] == "low"
    assert sent["max_completion_tokens"] > 0
    system, user = sent["messages"]
    assert system == {"role": "system", "content": PROMPT_FILE.read_text(encoding="utf-8")}
    assert user["role"] == "user"
    assert fake.user_data() == {
        "conversation": [
            {"from": "visitor", "text": "مرحبا"},  # non-Latin text is sent as is, not escaped
            {"from": "muneer", "text": "Hello!"},
            {"from": "visitor", "text": "Where is God?"},
        ],
        "muneer_latest_reply": REPLY,
    }

    schema = sent["response_format"]["json_schema"]
    assert schema["strict"] is True
    labels = json.dumps(schema["schema"])
    assert all(f'"{label.value}"' in labels for label in Label)


def test_temperature_and_reasoning_effort_can_be_left_to_the_model():
    g, fake = guardrail(verdict(), temperature=None, reasoning_effort=None)
    g.review(CONVERSATION, REPLY)
    assert "temperature" not in fake.requests[0] and "reasoning_effort" not in fake.requests[0]


def test_sends_only_the_most_recent_messages():
    g, fake = guardrail(verdict())
    conversation = [Message("visitor", f"message {n}") for n in range(HISTORY_LIMIT + 5)]
    g.review(conversation, REPLY)
    sent = fake.user_data()["conversation"]
    assert len(sent) == HISTORY_LIMIT
    assert sent[-1]["text"] == f"message {HISTORY_LIMIT + 4}"


# ---- failures: retry once, then hold the reply back ----------------------------


@pytest.mark.parametrize(
    "failure",
    [500, 429, "refusal", "not json", verdict(label="MAYBE"), httpx.ConnectError("network down")],
    ids=["server error", "rate limited", "refusal", "not json", "unknown label", "network down"],
)
def test_retries_once_after_a_failure(failure):
    g, fake = guardrail(failure, verdict("OK"))
    assert g.review(CONVERSATION, REPLY).label is Label.OK
    assert len(fake.requests) == 2


def test_gives_up_after_the_retry():
    g, fake = guardrail(500, "refusal")
    with pytest.raises(GuardrailUnavailable, match=f"after {ATTEMPTS} attempts"):
        g.review(CONVERSATION, REPLY)
    assert len(fake.requests) == ATTEMPTS == 2


# ---- settings a model doesn't take ------------------------------------------

# The errors OpenAI returns (checked against the live API for gpt-4.1).
NO_REASONING_EFFORT = (400, "Unrecognized request argument supplied: reasoning_effort", None)
NO_TEMPERATURE = (400, "Unsupported value: 'temperature' does not support 0.5 with this model. "
                       "Only the default (1) value is supported.", "temperature")


def test_a_setting_the_model_refuses_is_left_out_from_then_on(caplog):
    # gpt-4.1 isn't a reasoning model, so it refuses a reasoning effort.
    g, fake = guardrail(NO_REASONING_EFFORT, verdict("OK"), verdict("OK"), temperature=0.5)
    assert g.review(CONVERSATION, REPLY).label is Label.OK
    assert g.review(CONVERSATION, REPLY).label is Label.OK
    assert [("reasoning_effort" in r, r["temperature"]) for r in fake.requests] == [(True, 0.5), (False, 0.5), (False, 0.5)]
    assert g.refused == {"reasoning_effort"}
    assert "doesn't take reasoning_effort" in caplog.text


def test_a_reasoning_model_that_refuses_the_temperature_keeps_its_effort():
    g, fake = guardrail(NO_TEMPERATURE, verdict("OK"), temperature=0.5)
    g.review(CONVERSATION, REPLY)
    assert "temperature" not in fake.requests[1] and fake.requests[1]["reasoning_effort"] == "low"


def test_dropping_a_setting_is_not_a_failed_attempt():
    g, fake = guardrail(NO_REASONING_EFFORT, NO_TEMPERATURE, 500, verdict("OK"), temperature=0.5)
    assert g.review(CONVERSATION, REPLY).label is Label.OK
    assert len(fake.requests) == 4


@pytest.mark.parametrize("error", [
    (400, "The model `gpt-9` does not exist", "model"),
    # A value the model doesn't offer is a mistake to fix, not a setting to drop.
    (400, "Invalid value: 'lwo'. Supported values are: 'low', 'medium', and 'high'.", "reasoning_effort"),
])
def test_other_request_errors_still_fail(error):
    g, fake = guardrail(error, error)
    with pytest.raises(GuardrailUnavailable):
        g.review(CONVERSATION, REPLY)
    assert len(fake.requests) == 2
    assert g.refused == set()


# ---- labels ----------------------------------------------------------------


def test_label_behaviour():
    assert [l for l in Label if l.hands_off] == [
        Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW, Label.IMPROPER_RESPONSE, Label.CONTROVERSIAL_TOPIC,
        Label.TRANSFER_REQUESTED, Label.VISITOR_UPSET,
    ]
    assert Label("response_wrong").display_name == "Improper response"
    assert Label.STOP.display_name == "Stop" and not Label.STOP.hands_off


# ---- the prompt ------------------------------------------------------------


def test_prompt_is_tidied_for_the_public_repo():
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    for removed in ("biased", "corrected_reply"):
        assert removed not in prompt
    for kept in ("hadith", "above the Throne", "don't rewrite it", "only in English", "wrong surah or verse number",
                 "thinking of suicide", "politely declines"):
        assert kept in prompt
    for label in Label:
        assert label.value in prompt


def test_prompt_examples_are_valid_and_cover_every_label():
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    examples = prompt.split("## Examples", 1)[1].split("## Your answer", 1)[0]
    blocks = re.findall(r"```json\n(.*?)\n```", examples)
    data, answers = blocks[0::2], blocks[1::2]
    assert len(data) == len(answers) >= 3 * len(Label)
    counts = Counter()
    for given, expected in zip(data, answers):
        given, expected = json.loads(given), json.loads(expected)
        # The same shape as the data the guardrail is sent.
        assert list(given) == ["conversation", "muneer_latest_reply"]
        assert given["conversation"][-1]["from"] == "visitor"
        assert {m["from"] for m in given["conversation"]} <= {"visitor", "muneer"}
        assert list(expected) == ["explanation", "label"]  # the order the answer schema asks for
        counts[Label(expected["label"])] += 1
    assert all(counts[label] >= 3 for label in Label), counts


# ---- settings --------------------------------------------------------------


def test_from_env_reads_the_settings():
    g = Guardrail.from_env({
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": "https://proxy.example/v1",
        "GUARDRAIL_MODEL": "gpt-4.1-mini",
        "GUARDRAIL_TEMPERATURE": "0",
        "GUARDRAIL_REASONING_EFFORT": "medium",
        "GUARDRAIL_TIMEOUT_SECONDS": "7.5",
    })
    assert (g.model, g.temperature, g.reasoning_effort) == ("gpt-4.1-mini", 0.0, "medium")
    assert str(g.client.base_url) == "https://proxy.example/v1/"
    assert g.client.timeout == 7.5 and g.client.max_retries == 0


def test_from_env_defaults():
    g = Guardrail.from_env({"OPENAI_API_KEY": "sk-test"})
    assert (g.model, g.temperature, g.reasoning_effort, g.client.timeout) == ("gpt-5.6-sol", None, "low", 20.0)


def test_empty_settings_send_none():
    g = Guardrail.from_env({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_TEMPERATURE": " ", "GUARDRAIL_REASONING_EFFORT": ""})
    assert (g.temperature, g.reasoning_effort) == (None, None)


def test_the_reasoning_effort_is_read_in_any_case():
    assert Guardrail.from_env({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_REASONING_EFFORT": " High "}).reasoning_effort == "high"


@pytest.mark.parametrize(
    "env, message",
    [
        ({}, "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": "  "}, "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_TIMEOUT_SECONDS": "soon"}, "must be a number"),
        ({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_TIMEOUT_SECONDS": "0"}, "more than 0"),
        ({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_TEMPERATURE": "warm"}, "must be a number"),
        ({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_TEMPERATURE": "2.5"}, "from 0 to 2"),
        ({"OPENAI_API_KEY": "sk-test", "GUARDRAIL_REASONING_EFFORT": "lwo"}, "must be one of none, minimal, low"),
    ],
)
def test_from_env_rejects_bad_settings(env, message):
    with pytest.raises(ConfigError, match=message):
        Guardrail.from_env(env)
