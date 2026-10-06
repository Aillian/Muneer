"""The review queue: flagged replies and their verdicts, waiting for a da'i.

Every reply the guardrail labels anything but OK is saved with:
- its verdict;
- both versions of the reply, raw and filled (docs/adr/0004);
- the messages before it.

A da'i can then write the right answers and turn them into training examples.
Emails and phone numbers are masked before anything is saved here. (The
conversation log, in the same database, keeps every conversation exactly as
typed: conversation_log.py.)

Settings (environment variables):
    DATABASE_PATH   the SQLite file, shared with the conversation log
                    (database.py), default data/muneer.sqlite3
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipeline.consent import EMAIL
from pipeline.conversation import Message
from pipeline.database import connect, database_path, utc_now
from pipeline.guardrail import Label, Verdict
from pipeline.placeholders import FilledReply

CONTEXT_MESSAGES = 10  # messages kept from before the visitor's message

_PHONE_LIKE = re.compile(r"\+?\d[\d\s().-]{5,}\d")

# Files made before the guardrail stopped suggesting answers also have a
# corrected_reply column after explanation; it is no longer filled.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS flagged_replies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    conversation_id  TEXT NOT NULL,
    label            TEXT NOT NULL,
    explanation      TEXT NOT NULL,
    raw_reply        TEXT NOT NULL,
    filled_reply     TEXT NOT NULL,
    visitor_message  TEXT NOT NULL,
    context          TEXT NOT NULL,
    guardrail_model  TEXT NOT NULL
)
"""
_COLUMNS = ("id, created_at, conversation_id, label, explanation, raw_reply, filled_reply, "
            "visitor_message, context, guardrail_model")


def _only(conversation_ids: Sequence[str] | None) -> tuple[str, tuple[str, ...]]:
    """A WHERE clause limiting rows to some conversations (None: all of them)."""
    if conversation_ids is None:
        return "", ()
    ids = tuple(conversation_ids)
    if not ids:
        return " WHERE 0", ()
    return f" WHERE conversation_id IN ({', '.join('?' * len(ids))})", ids


def mask(text: str) -> str:
    """Hide email addresses and phone numbers (7 or more digits)."""
    text = EMAIL.sub("[email]", text)
    return _PHONE_LIKE.sub(lambda m: "[phone]" if sum(c.isdigit() for c in m.group()) >= 7 else m.group(), text)


@dataclass(frozen=True)
class FlaggedReply:
    id: int
    created_at: str  # UTC, ISO 8601
    conversation_id: str
    label: Label
    explanation: str
    raw_reply: str
    filled_reply: str
    visitor_message: str
    context: tuple[Message, ...]
    guardrail_model: str


class ReviewQueue:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self._connect():
            pass  # creates the folder, the file and the table now, so problems show at startup

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ReviewQueue:
        return cls(database_path(env))

    def add(self, *, conversation_id: str, verdict: Verdict, filled: FilledReply, conversation: Sequence[Message]) -> int:
        """Save a flagged reply. `conversation` is what the visitor saw, ending with their message."""
        visitor_at = max((i for i, m in enumerate(conversation) if m.speaker == "visitor"), default=None)
        visitor_message = conversation[visitor_at].text if visitor_at is not None else ""
        context = conversation[max(0, visitor_at - CONTEXT_MESSAGES):visitor_at] if visitor_at is not None else []
        row = (
            utc_now(),
            conversation_id,
            verdict.label.value,
            mask(verdict.explanation),
            mask(filled.raw),
            mask(filled.text),
            mask(visitor_message),
            json.dumps([{"from": m.speaker, "text": mask(m.text)} for m in context], ensure_ascii=False),
            verdict.model,
        )
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO flagged_replies (created_at, conversation_id, label, explanation, "
                "raw_reply, filled_reply, visitor_message, context, guardrail_model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            return cursor.lastrowid

    def recent(self, limit: int = 50, conversation_ids: Sequence[str] | None = None) -> list[FlaggedReply]:
        """The latest flagged replies, newest first; only from `conversation_ids` if given."""
        where, params = _only(conversation_ids)
        with self._connect() as db:
            rows = db.execute(f"SELECT {_COLUMNS} FROM flagged_replies{where} ORDER BY id DESC LIMIT ?",
                              (*params, limit)).fetchall()
        return [
            FlaggedReply(
                id=r[0], created_at=r[1], conversation_id=r[2], label=Label(r[3]), explanation=r[4],
                raw_reply=r[5], filled_reply=r[6], visitor_message=r[7],
                context=tuple(Message(m["from"], m["text"]) for m in json.loads(r[8])), guardrail_model=r[9],
            )
            for r in rows
        ]

    def count(self, conversation_ids: Sequence[str] | None = None) -> int:
        where, params = _only(conversation_ids)
        with self._connect() as db:
            return db.execute(f"SELECT COUNT(*) FROM flagged_replies{where}", params).fetchone()[0]

    def _connect(self):
        return connect(self.path, (_SCHEMA,))
