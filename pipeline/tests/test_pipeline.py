"""The pipeline end to end: the mock model server, the real placeholder filling
and review queue, and scripted stand-ins for the guardrail and the da'i."""

import sqlite3
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from openai import OpenAI
from starlette.testclient import TestClient

from mock_muneer.app import create_app
from pipeline import notices
from pipeline.config import ConfigError
from pipeline.consent import DEFAULT_CONSENT_NOTICE
from pipeline.conversation_log import ConversationLog
from pipeline.conversation import Message
from pipeline.guardrail import Guardrail, GuardrailUnavailable, Label, Verdict
from pipeline.muneer import MuneerClient
from pipeline.pipeline import Pipeline, PipelineSettings
from pipeline.placeholders import PlaceholderFiller
from pipeline.review_queue import ReviewQueue
from pipeline.simulated_dai import DaiReply, SimulatedDai, SimulatedDaiUnavailable

ENV = dotenv_values(Path(__file__).resolve().parents[2] / ".env.example")
KEY = "test-key"  # the mock model server checks it; the client sends it


class StubGuardrail:
    """Returns scripted labels ("unavailable" raises) and records what it was shown."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def review(self, conversation, reply):
        self.calls.append((list(conversation), reply))
        outcome = self.outcomes.pop(0)
        if outcome == "unavailable":
            raise GuardrailUnavailable("down")
        label = Label(outcome)
        return Verdict(label, f"Why: {label.value}.", "stub", 5)


class StubDai:
    """Returns scripted messages ("unavailable" raises) and records what it was given."""

    name = "Mohammad"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def reply(self, conversation, handoff):
        self.calls.append((list(conversation), handoff))
        outcome = self.outcomes.pop(0)
        if outcome == "unavailable":
            raise SimulatedDaiUnavailable("down")
        return DaiReply(outcome, "stub", 5)


def mock_muneer():
    http = TestClient(create_app(api_key=KEY))
    return MuneerClient(OpenAI(base_url="http://testserver/v1", api_key=KEY, max_retries=0, http_client=http))


def build(tmp_path, guardrail=(), dai=(), muneer=None, review_queue=None, **settings):
    g, d = StubGuardrail(*guardrail), StubDai(*dai)
    queue = review_queue or ReviewQueue(tmp_path / "review.sqlite3")
    pipeline = Pipeline(muneer or mock_muneer(), PlaceholderFiller.from_env(ENV), g, d, queue, PipelineSettings(**settings))
    return pipeline, g, d, queue


def speakers(result):
    return [m.speaker for m in result.messages]


def test_a_conversation_opens_with_the_welcome_and_the_ai_disclosure(tmp_path):
    pipeline, *_ = build(tmp_path)
    conversation = pipeline.start()
    assert conversation.messages == [Message("notice", notices.WELCOME), Message("notice", notices.AI_DISCLOSURE)]
    assert conversation.state == "muneer" and conversation.muneer_history == []


# ---- OK: delivered -------------------------------------------------------------


def test_an_ok_reply_is_filled_and_delivered(tmp_path):
    pipeline, g, d, queue = build(tmp_path, guardrail=["OK"])
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "Who are you?")

    assert speakers(result) == ["visitor", "muneer"]
    assert result.messages[1].text.startswith("I'm Muneer, an AI Islamic Advisor. I'm here")
    assert result.trace.outcome == "delivered" and result.trace.verdict.label is Label.OK
    # Muneer's history keeps its raw reply; the visitor sees the filled one.
    assert result.trace.muneer.text.startswith("I'm Daee")
    assert conversation.muneer_history == [
        {"role": "user", "content": "Who are you?"},
        {"role": "assistant", "content": result.trace.muneer.text},
    ]
    assert queue.count() == 0 and d.calls == []


def test_the_guardrail_reviews_the_filled_reply(tmp_path):
    pipeline, g, *_ = build(tmp_path, guardrail=["OK"])
    conversation = pipeline.start()
    pipeline.handle(conversation, "How do I pray?")
    shown, reply = g.calls[0]
    assert shown == [Message("visitor", "How do I pray?")]  # notices are left out
    assert "write to Bathel at info@bathel.sa" in reply and "[org_" not in reply
    assert "write to [org_name] at [org_email]" in conversation.muneer_history[-1]["content"]


def test_the_guardrail_sees_the_conversation_so_far(tmp_path):
    pipeline, g, *_ = build(tmp_path, guardrail=["OK", "OK"])
    conversation = pipeline.start()
    first = pipeline.handle(conversation, "hello")
    pipeline.handle(conversation, "Who are you?")
    shown, _ = g.calls[1]
    assert shown == [Message("visitor", "hello"), first.messages[1], Message("visitor", "Who are you?")]


def test_a_request_for_contact_details_is_followed_by_the_consent_notice(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["OK"])
    result = pipeline.handle(pipeline.start(), "I said it!")
    assert speakers(result) == ["visitor", "muneer", "notice"]
    assert "WhatsApp number" in result.messages[1].text
    assert result.messages[2].text == DEFAULT_CONSENT_NOTICE


def test_the_consent_notice_is_a_setting(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["OK"], consent_notice="Optional, and only for lessons.")
    assert pipeline.handle(pipeline.start(), "I said it!").messages[-1].text == "Optional, and only for lessons."


# ---- handoff to the da'i -----------------------------------------------------------


def test_a_wrong_reply_is_held_back_and_the_da_i_takes_over(tmp_path):
    pipeline, g, d, queue = build(tmp_path, guardrail=["wrong_from_islamic_point_of_view"], dai=["I'm one of the da'is."])
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "Where is God?")

    assert speakers(result) == ["visitor", "notice", "dai", "notice"]
    assert result.messages[1].text == notices.HANDOFF.format(name="Mohammad")
    assert result.messages[1].text == "You're being connected to Mohammad, one of our da'is."
    assert result.messages[2].text == "I'm one of the da'is."
    assert result.messages[3].text == notices.HANDED_OVER.format(name="Mohammad")
    assert not any("present everywhere" in m.text for m in conversation.messages)  # never shown
    assert conversation.state == "closed" and result.trace.outcome == "handed_off"

    handoff = conversation.handoff
    assert handoff.label is Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW
    assert handoff.withheld_reply.startswith("God is present everywhere")
    assert handoff.reviewer_note == "Why: wrong_from_islamic_point_of_view."
    assert d.calls == [([Message("visitor", "Where is God?")], handoff)]

    [flagged] = queue.recent()
    assert flagged.id == result.trace.review_id
    assert flagged.visitor_message == "Where is God?"
    assert flagged.raw_reply == result.trace.muneer.text and flagged.explanation == "Why: wrong_from_islamic_point_of_view."


def test_the_da_i_replies_once_and_then_the_conversation_is_closed(tmp_path):
    pipeline, g, d, queue = build(tmp_path, guardrail=["wrong_transfer_requested"], dai=["Hello, I'm Mohammad."])
    conversation = pipeline.start()
    pipeline.handle(conversation, "Can I talk to a real person?")
    assert "start a new conversation" in conversation.messages[-1].text  # the closing notice

    result = pipeline.handle(conversation, "What is Islam?")
    assert result.messages == [Message("notice", notices.CLOSED)] and result.trace.outcome == "closed"
    assert len(g.calls) == 1 and len(d.calls) == 1  # nobody is asked again: a new conversation starts afresh
    assert conversation.handoff.reviewer_note == "Why: wrong_transfer_requested."
    assert queue.recent()[0].label is Label.TRANSFER_REQUESTED


def test_the_da_i_s_request_for_contact_details_gets_the_consent_notice(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["wrong_transfer_requested"], dai=["Would you like to share your WhatsApp number?"])
    result = pipeline.handle(pipeline.start(), "Can I talk to a real person?")
    assert speakers(result) == ["visitor", "notice", "dai", "notice", "notice"]
    assert result.messages[-2].text == DEFAULT_CONSENT_NOTICE
    assert result.messages[-1].text == notices.HANDED_OVER.format(name="Mohammad")


def test_if_the_da_i_is_unavailable_the_visitor_can_try_again(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["wrong_transfer_upset"], dai=["unavailable", "Sorry for the trouble."])
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "This is useless")
    assert [m.text for m in result.messages[1:]] == [notices.HANDOFF.format(name="Mohammad"), notices.DAI_BUSY]
    assert conversation.state == "dai" and result.trace.error.startswith("Simulated da'i unavailable")

    result = pipeline.handle(conversation, "hello?")
    assert speakers(result) == ["visitor", "dai", "notice"] and result.trace.outcome == "dai_replied"
    assert conversation.state == "closed"


# ---- stop ----------------------------------------------------------------------


def test_stop_closes_the_conversation(tmp_path):
    pipeline, g, d, queue = build(tmp_path, guardrail=["STOP"])
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "Stop messaging me")
    assert speakers(result) == ["visitor", "notice"] and result.messages[1].text == notices.STOPPED
    assert conversation.state == "closed" and result.trace.outcome == "stopped"
    assert queue.recent()[0].label is Label.STOP and d.calls == []

    result = pipeline.handle(conversation, "hello?")
    assert result.messages == [Message("notice", notices.CLOSED)] and result.trace.outcome == "closed"
    assert len(g.calls) == 1


# ---- nothing unchecked is ever shown ---------------------------------------------


def test_a_reply_the_guardrail_could_not_check_is_not_shown(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["unavailable", "OK"])
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "What is Islam?")
    assert speakers(result) == ["visitor", "notice"] and result.messages[1].text == notices.TRY_AGAIN
    assert result.trace.outcome == "error" and result.trace.error.startswith("Guardrail unavailable")
    assert conversation.muneer_history == []  # the failed exchange never reaches Muneer's history

    result = pipeline.handle(conversation, "What is Islam?")
    assert result.trace.outcome == "delivered"
    assert [m["role"] for m in conversation.muneer_history] == ["user", "assistant"]


def test_if_muneer_is_down_the_visitor_is_asked_to_try_again(tmp_path):
    down = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    muneer = MuneerClient(OpenAI(base_url="http://fake/v1", api_key="k", max_retries=0, http_client=down))
    pipeline, g, *_ = build(tmp_path, muneer=muneer)
    result = pipeline.handle(pipeline.start(), "hi")
    assert speakers(result) == ["visitor", "notice"] and result.messages[1].text == notices.TRY_AGAIN
    assert result.trace.error.startswith("Muneer unavailable") and g.calls == []


def test_a_model_server_answering_with_a_web_page_is_a_failure_too(tmp_path):
    # A proxy's HTML page with HTTP 200: the OpenAI client returns text instead of raising.
    page = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>Starting…</html>", headers={"content-type": "text/html"})))
    muneer = MuneerClient(OpenAI(base_url="http://fake/v1", api_key="k", max_retries=0, http_client=page))
    pipeline, g, *_ = build(tmp_path, muneer=muneer)
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "hi")
    assert speakers(result) == ["visitor", "notice"] and result.messages[1].text == notices.TRY_AGAIN
    assert result.trace.outcome == "error" and result.trace.error == "Unexpected error: AttributeError"
    assert conversation.state == "muneer" and conversation.muneer_history == [] and g.calls == []


@pytest.mark.parametrize("error", [sqlite3.OperationalError("disk I/O error"), PermissionError("read-only folder")])
def test_a_broken_review_queue_does_not_break_the_chat(tmp_path, error):
    class BrokenQueue:
        def add(self, **_):
            raise error

    pipeline, *_ = build(tmp_path, guardrail=["wrong_transfer_requested"], dai=["Hello."], review_queue=BrokenQueue())
    result = pipeline.handle(pipeline.start(), "Can I talk to a real person?")
    assert speakers(result) == ["visitor", "notice", "dai", "notice"] and result.trace.review_id is None


# ---- limits ------------------------------------------------------------------------


def test_a_message_over_the_length_limit_is_refused(tmp_path):
    pipeline, g, *_ = build(tmp_path, max_message_chars=10)
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "x" * 11)
    assert result.messages == [Message("notice", "Your message is too long. Please keep it under 10 characters.")]
    assert conversation.visitor_messages == 0 and g.calls == []


def test_the_default_length_limit_is_2000_characters(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["OK"])
    assert pipeline.handle(pipeline.start(), "x" * 2001).messages[0].text.endswith("under 2,000 characters.")
    assert pipeline.handle(pipeline.start(), "hello " + "x" * 1994).trace.outcome == "delivered"


def test_a_conversation_has_a_message_limit(tmp_path):
    pipeline, *_ = build(tmp_path, guardrail=["OK"], max_visitor_messages=1)
    conversation = pipeline.start()
    pipeline.handle(conversation, "hello")
    result = pipeline.handle(conversation, "hello again")
    assert result.messages == [Message("notice", notices.LIMIT_REACHED.format(limit=1))]
    assert conversation.state == "closed"  # so the app locks the box and offers a new conversation
    assert pipeline.handle(conversation, "and again").messages == [Message("notice", notices.CLOSED)]


def test_an_empty_message_is_ignored(tmp_path):
    pipeline, g, *_ = build(tmp_path)
    result = pipeline.handle(pipeline.start(), "   ")
    assert result.messages == [] and result.trace.outcome == "rejected" and g.calls == []


# ---- settings ----------------------------------------------------------------------


def test_from_env_builds_every_step(tmp_path):
    env = {**ENV, "OPENAI_API_KEY": "sk-test", "DATABASE_PATH": str(tmp_path / "queue.sqlite3")}
    pipeline = Pipeline.from_env(env)
    assert isinstance(pipeline.guardrail, Guardrail) and isinstance(pipeline.dai, SimulatedDai)
    assert pipeline.dai.organisation_name == "Bathel"  # to introduce itself; never its email or links
    assert pipeline.dai.name == "Mohammad" and pipeline.filler.profile["org_name"] == "Bathel"
    assert (pipeline.settings.max_message_chars, pipeline.settings.max_visitor_messages) == (2000, 40)
    assert pipeline.settings.consent_notice == DEFAULT_CONSENT_NOTICE
    assert pipeline.review_queue.path == tmp_path / "queue.sqlite3"


def test_the_review_queue_and_the_conversation_log_share_one_database(tmp_path):
    env = {**ENV, "OPENAI_API_KEY": "sk-test", "DATABASE_PATH": str(tmp_path / "muneer.sqlite3")}
    pipeline = Pipeline.from_env(env)
    assert pipeline.conversation_log.path == pipeline.review_queue.path == tmp_path / "muneer.sqlite3"


def test_the_chat_runs_without_a_database_that_cannot_be_opened(tmp_path, caplog):
    (tmp_path / "file").write_text("")  # a file where the database's folder should be
    env = {**ENV, "OPENAI_API_KEY": "sk-test", "DATABASE_PATH": str(tmp_path / "file" / "muneer.sqlite3")}
    pipeline = Pipeline.from_env(env)
    assert pipeline.review_queue is None and pipeline.conversation_log is None
    assert "flagged replies won't be saved" in caplog.text and "conversations won't be saved" in caplog.text
    assert pipeline.start().messages  # and the chat still works


# ---- the conversation log ------------------------------------------------------------


def test_every_conversation_message_and_turn_is_saved(tmp_path):
    path = tmp_path / "muneer.sqlite3"
    log = ConversationLog(path)
    pipeline = Pipeline(mock_muneer(), PlaceholderFiller.from_env(ENV), StubGuardrail("OK", "wrong_from_islamic_point_of_view"),
                        StubDai("Allah is above His Throne."), ReviewQueue(path), PipelineSettings(), log)
    conversation = pipeline.start(session_id="page-1")
    pipeline.handle(conversation, "How do I pray? Write to me at me@example.com")
    pipeline.handle(conversation, "Where is God?")

    assert log.messages(conversation.id) == conversation.messages  # every message, notices included
    assert log.turns(conversation.id) == conversation.traces  # every step of every turn
    assert [t.outcome for t in conversation.traces] == ["delivered", "handed_off"]
    pipeline.start(session_id="page-1")  # opened, but nothing sent: not saved
    assert "me@example.com" in log.messages(conversation.id)[2].text  # exactly as typed
    [saved] = log.recent()
    assert (saved.session_id, saved.state, saved.visitor_messages) == ("page-1", "closed", 2)
    assert saved.handoff_label is Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW
    assert log.turns(conversation.id)[1].review_id == conversation.traces[1].review_id is not None


def test_refused_messages_are_saved_too(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    pipeline = Pipeline(mock_muneer(), PlaceholderFiller.from_env(ENV), StubGuardrail(), StubDai(), None,
                        PipelineSettings(max_message_chars=10), log)
    conversation = pipeline.start()
    pipeline.handle(conversation, "x" * 11)
    [turn] = log.turns(conversation.id)
    assert turn.outcome == "rejected" and turn.visitor_message == "x" * 10 + "…"  # never more than the limit
    assert log.messages(conversation.id)[-1] == Message("notice", notices.TOO_LONG.format(limit=10))


def test_a_broken_conversation_log_does_not_break_the_chat(tmp_path, caplog):
    class BrokenLog:
        def record(self, conversation, trace, first_new):
            raise sqlite3.OperationalError("disk I/O error")

    pipeline = Pipeline(mock_muneer(), PlaceholderFiller.from_env(ENV), StubGuardrail("OK"), StubDai(),
                        ReviewQueue(tmp_path / "q.sqlite3"), PipelineSettings(), BrokenLog())
    conversation = pipeline.start()
    result = pipeline.handle(conversation, "Who are you?")
    assert speakers(result) == ["visitor", "muneer"] and result.trace.outcome == "delivered"
    assert "could not save it to the conversation log" in caplog.text


@pytest.mark.parametrize("name", ["MAX_MESSAGE_CHARS", "MAX_VISITOR_MESSAGES"])
def test_limits_must_be_whole_numbers(name):
    with pytest.raises(ConfigError, match=name):
        PipelineSettings.from_env({name: "lots"})
