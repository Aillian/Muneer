"""The conversation log: everything saved exactly as typed, and read back as it was."""

import sqlite3

from pipeline.conversation import Message
from pipeline.conversation_log import ConversationLog, Totals
from pipeline.database import DEFAULT_PATH
from pipeline.guardrail import Label, Verdict
from pipeline.muneer import MuneerReply
from pipeline.pipeline import Conversation
from pipeline.placeholders import FilledReply
from pipeline.review_queue import ReviewQueue
from pipeline.simulated_dai import DaiReply, Handoff
from pipeline.trace import Trace

OPENING = [Message("notice", "Hello, welcome to our live chat."), Message("notice", "You are now chatting with Muneer.")]


def a_conversation(**fields):
    return Conversation(session_id="page-1", messages=list(OPENING), **fields)


def a_handoff_trace():
    raw = "I'm Daee. God is present everywhere in His essence. Write to {{SUPPORT_EMAIL}} or {{NEWSLETTER_URL}}"
    return Trace(
        visitor_message="Where is God? My email is visitor@example.com, phone +44 7700 900123",
        outcome="handed_off",
        muneer=MuneerReply(raw, "muneer", 612, 20, 18, "stop", "mock-muneer/creed_error"),
        filled=FilledReply(raw, "I'm Muneer. God is present everywhere in His essence. Write to info@bathel.sa or ",
                           replacements=(("Daee", "Muneer", 1),), placeholders=(("SUPPORT_EMAIL", 1),),
                           missing=("NEWSLETTER_URL",)),
        verdict=Verdict(Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW, "Wrong about Allah.", "gpt-5.6-sol", 2700, 3935, 52),
        dai=DaiReply("Peace be upon you. Allah is above His Throne.", "gpt-5.6-sol", 5400, 1040, 221),
        review_id=7,
    )


def take_turn(conversation, trace, *added):
    """Add a turn as the pipeline does; returns where its messages start."""
    first_new = len(conversation.messages)
    conversation.messages.extend(added)
    conversation.traces.append(trace)
    return first_new


def handed_off(conversation, trace):
    first_new = take_turn(conversation, trace, Message("visitor", trace.visitor_message),
                          Message("notice", "You're being connected to Mohammad."), Message("dai", trace.dai.text),
                          Message("notice", "Muneer's turn ends here."))
    conversation.visitor_messages += 1
    conversation.state = "closed"
    conversation.handoff = Handoff(trace.verdict.label, trace.verdict.explanation, trace.filled.text)
    return first_new


def test_the_first_turn_saves_the_conversation_and_its_opening_notices(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation, trace = a_conversation(), Trace("hi", "delivered")
    log.record(conversation, trace, take_turn(conversation, trace, Message("visitor", "hi"), Message("muneer", "Hello")))

    [saved] = log.recent()
    assert (saved.id, saved.session_id, saved.started_at) == (conversation.id, "page-1", conversation.started_at)
    assert (saved.state, saved.handoff_label, saved.first_message) == ("muneer", None, "hi")
    assert log.messages(conversation.id) == conversation.messages  # the opening notices first
    assert log.totals() == Totals(conversations=1, messages=4, turns=1)


def test_a_turn_is_saved_with_every_step_and_read_back_as_it_was(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation, trace = a_conversation(), a_handoff_trace()
    log.record(conversation, trace, handed_off(conversation, trace))

    assert log.turns(conversation.id) == [trace]  # Muneer, placeholder filling, the guardrail, the da'i, timings
    assert log.messages(conversation.id) == conversation.messages
    [saved] = log.recent()
    assert saved.state == "closed" and saved.handoff_label is Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW
    assert saved.visitor_messages == 1 and saved.first_message == trace.visitor_message
    assert log.totals() == Totals(conversations=1, messages=6, turns=1)


def test_everything_is_stored_exactly_as_typed(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation, trace = a_conversation(), a_handoff_trace()
    log.record(conversation, trace, handed_off(conversation, trace))
    visitor = [m.text for m in log.messages(conversation.id) if m.speaker == "visitor"]
    assert visitor == ["Where is God? My email is visitor@example.com, phone +44 7700 900123"]  # nothing masked
    assert log.turns(conversation.id)[0].visitor_message == visitor[0]


def test_each_message_points_to_the_turn_that_added_it(tmp_path):
    path = tmp_path / "muneer.sqlite3"
    log = ConversationLog(path)
    conversation = a_conversation()
    hello, stop = Trace("hello", "delivered"), Trace("stop", "stopped")
    first = log.record(conversation, hello, take_turn(conversation, hello, Message("visitor", "hello"),
                                                      Message("muneer", "Hello, welcome.")))
    second = log.record(conversation, stop, take_turn(conversation, stop, Message("visitor", "stop"),
                                                      Message("notice", "You won't receive any more messages.")))
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT position, turn_id FROM messages ORDER BY position").fetchall()
        numbers = db.execute("SELECT id, number FROM turns ORDER BY number").fetchall()
    assert rows == [(0, None), (1, None), (2, first), (3, first), (4, second), (5, second)]
    assert numbers == [(first, 1), (second, 2)]


def test_a_turn_with_only_a_notice_is_saved_too(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation = a_conversation(state="closed")
    trace = Trace("hello?", "closed")
    log.record(conversation, trace, take_turn(conversation, trace, Message("notice", "This conversation has ended.")))
    [saved] = log.turns(conversation.id)
    assert saved == trace and saved.muneer is saved.verdict is saved.dai is None


def test_messages_missed_while_the_database_was_down_are_saved_with_the_next_turn(tmp_path):
    path = tmp_path / "muneer.sqlite3"
    log = ConversationLog(path)
    conversation, first, second = a_conversation(), Trace("hi", "delivered"), Trace("bye", "delivered")
    take_turn(conversation, first, Message("visitor", "hi"), Message("muneer", "Hello"))  # not saved: database down
    log.record(conversation, second, take_turn(conversation, second, Message("visitor", "bye"), Message("muneer", "Bye")))
    assert log.messages(conversation.id) == conversation.messages
    with sqlite3.connect(path) as db:
        turn_ids = [t for (t,) in db.execute("SELECT turn_id FROM messages ORDER BY position")]
    assert turn_ids[:4] == [None] * 4 and turn_ids[4] is not None and turn_ids[4:] == [turn_ids[4]] * 2


def test_recent_lists_the_newest_conversations_first_up_to_a_limit(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversations = [a_conversation(started_at=f"2026-10-0{day}T10:00:00+00:00") for day in (1, 3, 2)]
    for conversation in conversations:
        trace = Trace("hi", "delivered", created_at=conversation.started_at)  # each one's first message
        log.record(conversation, trace, take_turn(conversation, trace, Message("visitor", "hi")))
    assert [c.started_at[:10] for c in log.recent(2)] == ["2026-10-03", "2026-10-02"]
    assert log.totals().conversations == 3


def test_it_shares_the_database_file_with_the_review_queue(tmp_path):
    path = tmp_path / "muneer.sqlite3"
    log, queue = ConversationLog(path), ReviewQueue(path)
    conversation, trace = a_conversation(), a_handoff_trace()
    log.record(conversation, trace, handed_off(conversation, trace))
    queue.add(conversation_id=conversation.id, verdict=trace.verdict, filled=trace.filled,
              conversation=[Message("visitor", trace.visitor_message)])
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"conversations", "messages", "turns", "flagged_replies"} <= tables
    assert queue.count() == 1 and log.totals().turns == 1


def test_deleting_the_file_while_running_starts_an_empty_log(tmp_path):
    path = tmp_path / "data" / "muneer.sqlite3"
    log = ConversationLog(path)

    def chat():
        conversation, trace = a_conversation(), Trace("hi", "delivered")
        log.record(conversation, trace, take_turn(conversation, trace, Message("visitor", "hi")))

    chat()
    path.unlink()
    assert log.totals() == Totals(0, 0, 0)
    chat()
    assert log.totals().conversations == 1


def test_an_unknown_conversation_has_no_messages_or_turns(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    assert log.messages("nope") == [] and log.turns("nope") == []


def test_from_env(tmp_path):
    assert DEFAULT_PATH == "data/muneer.sqlite3"
    log = ConversationLog.from_env({"DATABASE_PATH": str(tmp_path / "x.sqlite3")})
    assert log.path == tmp_path / "x.sqlite3" and log.path.is_file()


def test_a_turn_that_could_not_be_saved_is_saved_with_the_next_one(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation = a_conversation()
    traces = [Trace(text, "delivered") for text in ("one", "two", "three")]
    log.record(conversation, traces[0], take_turn(conversation, traces[0], Message("visitor", "one")))
    take_turn(conversation, traces[1], Message("visitor", "two"))  # not saved: the database was busy
    log.record(conversation, traces[2], take_turn(conversation, traces[2], Message("visitor", "three")))
    assert log.turns(conversation.id) == traces  # numbered 1, 2, 3, as in Behind the scenes


def test_a_conversation_starts_when_the_visitor_first_writes(tmp_path):
    log = ConversationLog(tmp_path / "muneer.sqlite3")
    conversation = a_conversation(started_at="2026-10-03T09:00:00+00:00")  # the page opened
    trace = Trace("hi", "delivered", created_at="2026-10-03T15:00:00+00:00")
    log.record(conversation, trace, take_turn(conversation, trace, Message("visitor", "hi")))
    assert log.recent()[0].started_at == "2026-10-03T15:00:00+00:00"


def test_a_database_from_an_older_version_is_refused_at_startup(tmp_path):
    import pytest

    path = tmp_path / "muneer.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, conversation_id TEXT, number INTEGER)")
    with pytest.raises(sqlite3.OperationalError, match="older turns table"):
        ConversationLog(path)
