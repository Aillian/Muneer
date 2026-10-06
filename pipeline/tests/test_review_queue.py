import sqlite3

from pipeline.conversation import Message
from pipeline.guardrail import Label, Verdict
from pipeline.placeholders import FilledReply
from pipeline.review_queue import CONTEXT_MESSAGES, ReviewQueue, mask

CONVERSATION = [
    Message("visitor", "hi"),
    Message("muneer", "Hello, welcome to our live help."),
    Message("visitor", "Where is God?"),
]


def verdict(label=Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW):
    return Verdict(label, "Wrong about Allah's presence.", "gpt-4.1", 1200)


def add(queue, conversation=CONVERSATION, **options):
    return queue.add(
        conversation_id=options.get("conversation_id", "c1"),
        verdict=options.get("verdict", verdict()),
        filled=options.get("filled", FilledReply(raw="God is everywhere, see {{LINK}}", text="God is everywhere, see x")),
        conversation=conversation,
    )


def test_saves_a_flagged_reply(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    new_id = add(queue)
    [item] = queue.recent()
    assert item.id == new_id and item.conversation_id == "c1"
    assert item.label is Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW
    assert item.explanation == "Wrong about Allah's presence."
    assert (item.raw_reply, item.filled_reply) == ("God is everywhere, see {{LINK}}", "God is everywhere, see x")
    assert item.visitor_message == "Where is God?"
    assert item.context == tuple(CONVERSATION[:2])
    assert item.guardrail_model == "gpt-4.1"
    assert item.created_at.endswith("+00:00")
    assert queue.count() == 1


def test_can_list_only_some_conversations(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    for cid in ("mine-1", "someone-else", "mine-2", "someone-else"):
        add(queue, conversation_id=cid)
    mine = queue.recent(conversation_ids=["mine-1", "mine-2"])
    assert [item.conversation_id for item in mine] == ["mine-2", "mine-1"]
    assert queue.count(["mine-1", "mine-2"]) == 2
    assert queue.recent(conversation_ids=[]) == [] and queue.count([]) == 0
    assert queue.count() == 4  # no filter: everything


def test_a_queue_file_from_before_still_works(tmp_path):
    # Files made while the guardrail still suggested answers have an extra column.
    path = tmp_path / "queue.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE flagged_replies (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
        "conversation_id TEXT NOT NULL, label TEXT NOT NULL, explanation TEXT NOT NULL, corrected_reply TEXT, "
        "raw_reply TEXT NOT NULL, filled_reply TEXT NOT NULL, visitor_message TEXT NOT NULL, "
        "context TEXT NOT NULL, guardrail_model TEXT NOT NULL)"
    )
    db.commit()
    db.close()
    queue = ReviewQueue(path)
    add(queue)
    [item] = queue.recent()
    assert (item.explanation, item.filled_reply) == ("Wrong about Allah's presence.", "God is everywhere, see x")
    assert item.visitor_message == "Where is God?" and item.guardrail_model == "gpt-4.1"


def test_lists_newest_first_up_to_a_limit(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    ids = [add(queue, conversation_id=f"c{n}") for n in range(3)]
    assert [item.id for item in queue.recent(limit=2)] == [ids[2], ids[1]]


def test_keeps_its_contents_across_restarts(tmp_path):
    add(ReviewQueue(tmp_path / "queue.sqlite3"))
    assert ReviewQueue(tmp_path / "queue.sqlite3").count() == 1


def test_creates_its_folder(tmp_path):
    ReviewQueue(tmp_path / "data" / "nested" / "queue.sqlite3")
    assert (tmp_path / "data" / "nested" / "queue.sqlite3").is_file()


def test_deleting_the_file_while_running_starts_an_empty_queue(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    add(queue)
    queue.path.unlink()
    assert queue.recent() == [] and queue.count() == 0
    add(queue)
    assert queue.count() == 1


def test_deleting_the_folder_while_running_starts_an_empty_queue(tmp_path):
    import shutil

    queue = ReviewQueue(tmp_path / "data" / "queue.sqlite3")
    shutil.rmtree(tmp_path / "data")
    assert queue.recent() == []
    add(queue)
    assert queue.count() == 1


def test_keeps_at_most_ten_messages_of_context(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    long = [Message("visitor" if n % 2 else "muneer", f"m{n}") for n in range(30)] + [Message("visitor", "last")]
    add(queue, conversation=long)
    item = queue.recent()[0]
    assert item.visitor_message == "last"
    assert len(item.context) == CONTEXT_MESSAGES and item.context[-1].text == "m29"


def test_masks_emails_and_phone_numbers(tmp_path):
    queue = ReviewQueue(tmp_path / "queue.sqlite3")
    conversation = [
        Message("visitor", "Call me on +966 55 123 4567"),
        Message("muneer", "Thank you!"),
        Message("visitor", "My email is ali.test@example.com"),
    ]
    add(queue, conversation=conversation, filled=FilledReply(raw="Noted ali.test@example.com", text="Noted ali.test@example.com"))
    item = queue.recent()[0]
    assert item.visitor_message == "My email is [email]"
    assert item.context[0].text == "Call me on [phone]"
    assert item.raw_reply == item.filled_reply == "Noted [email]"


def test_mask_leaves_references_and_years_alone():
    text = "Surah Al-Baqarah 2:256, Sahih Muslim 2699, in 1998."
    assert mask(text) == text


def test_from_env(tmp_path):
    queue = ReviewQueue.from_env({"DATABASE_PATH": str(tmp_path / "q.sqlite3")})
    assert queue.path == tmp_path / "q.sqlite3"
