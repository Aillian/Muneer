"""The conversation log: every conversation, message and turn, in the database.

Everything is stored as it happened, exactly as typed, with nothing masked:
- each conversation, with the browser page it came from and its state;
- every message the visitor saw, in order: the visitor's, Muneer's (filled),
  the simulated da'i's and the notices;
- one turn per visitor message: Muneer's raw reply, placeholder filling, the
  guardrail's verdict, the simulated da'i's reply, timings, tokens, errors and
  the outcome (the same steps the Behind the scenes tab shows).

The log shares the database file with the review queue (database.py). The app's
Conversations tab shows it, behind ADMIN_PASSCODE.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pipeline.conversation import Message
from pipeline.database import connect, database_path, utc_now
from pipeline.guardrail import Label, Verdict
from pipeline.muneer import MuneerReply
from pipeline.placeholders import FilledReply
from pipeline.simulated_dai import DaiReply
from pipeline.trace import Trace

if TYPE_CHECKING:
    from pipeline.pipeline import Conversation

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id                TEXT PRIMARY KEY,
        session_id        TEXT,
        started_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        state             TEXT NOT NULL,
        handoff_label     TEXT,
        visitor_messages  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id          TEXT NOT NULL REFERENCES conversations (id),
        number                   INTEGER NOT NULL,
        created_at               TEXT NOT NULL,
        visitor_message          TEXT NOT NULL,
        outcome                  TEXT NOT NULL,
        error                    TEXT,
        muneer_raw_reply         TEXT,
        muneer_model             TEXT,
        muneer_latency_ms        INTEGER,
        muneer_input_tokens      INTEGER,
        muneer_output_tokens     INTEGER,
        muneer_finish_reason     TEXT,
        muneer_source            TEXT,
        filled_reply             TEXT,
        replacements             TEXT,
        placeholders             TEXT,
        missing_placeholders     TEXT,
        guardrail_label          TEXT,
        guardrail_explanation    TEXT,
        guardrail_model          TEXT,
        guardrail_latency_ms     INTEGER,
        guardrail_input_tokens   INTEGER,
        guardrail_output_tokens  INTEGER,
        dai_reply                TEXT,
        dai_model                TEXT,
        dai_latency_ms           INTEGER,
        dai_input_tokens         INTEGER,
        dai_output_tokens        INTEGER,
        review_id                INTEGER,
        UNIQUE (conversation_id, number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id  TEXT NOT NULL REFERENCES conversations (id),
        position         INTEGER NOT NULL,
        turn_id          INTEGER REFERENCES turns (id),
        speaker          TEXT NOT NULL,
        text             TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        UNIQUE (conversation_id, position)
    )
    """,
    "CREATE INDEX IF NOT EXISTS conversations_by_start ON conversations (started_at)",
)

_TURN_COLUMNS = (
    "conversation_id", "number", "created_at", "visitor_message", "outcome", "error",
    "muneer_raw_reply", "muneer_model", "muneer_latency_ms", "muneer_input_tokens", "muneer_output_tokens",
    "muneer_finish_reason", "muneer_source",
    "filled_reply", "replacements", "placeholders", "missing_placeholders",
    "guardrail_label", "guardrail_explanation", "guardrail_model", "guardrail_latency_ms",
    "guardrail_input_tokens", "guardrail_output_tokens",
    "dai_reply", "dai_model", "dai_latency_ms", "dai_input_tokens", "dai_output_tokens",
    "review_id",
)


_COLUMNS = {
    "conversations": {"id", "session_id", "started_at", "updated_at", "state", "handoff_label", "visitor_messages"},
    "turns": {"id", *_TURN_COLUMNS},
    "messages": {"id", "conversation_id", "position", "turn_id", "speaker", "text", "created_at"},
}


@dataclass(frozen=True)
class LoggedConversation:
    """One row of the conversation list."""

    id: str
    session_id: str | None
    started_at: str  # UTC, ISO 8601
    updated_at: str
    state: str  # muneer, dai or closed
    handoff_label: Label | None
    visitor_messages: int
    first_message: str  # the visitor's first message, "" if none yet


@dataclass(frozen=True)
class Totals:
    conversations: int
    messages: int
    turns: int


class ConversationLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Creates the folder, the file and the tables now, so problems show at startup,
        # including a file whose tables are from an older version of the app.
        with self._connect() as db:
            for table, expected in _COLUMNS.items():
                missing = expected - {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if missing:
                    raise sqlite3.OperationalError(
                        f"{self.path} has an older {table} table, without {', '.join(sorted(missing))}. "
                        "Move the file aside to start a new database."
                    )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ConversationLog:
        return cls(database_path(env))

    # ---- writing --------------------------------------------------------------

    def record(self, conversation: Conversation, trace: Trace, first_new: int) -> int:
        """Save one turn: the trace, the messages it added (from position `first_new`)
        and the conversation's new state. Returns the turn's id.

        The first turn saves the conversation itself and its opening notices.
        Earlier turns and messages missing from the log, say because the database
        was unreachable then, are saved too (those messages without a turn), so
        the turns keep the numbers the Behind the scenes tab shows."""
        now = utc_now()
        number = len(conversation.traces)
        with self._connect() as db:
            self._save_conversation(db, conversation, now)
            db.executemany(
                f"INSERT OR IGNORE INTO turns ({', '.join(_TURN_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_TURN_COLUMNS))})",
                [_turn_values(conversation.id, n, t) for n, t in enumerate(conversation.traces, 1) if t is not trace]
                + [_turn_values(conversation.id, number, trace)],
            )
            turn_id = db.execute("SELECT id FROM turns WHERE conversation_id = ? AND number = ?",
                                 (conversation.id, number)).fetchone()[0]
            self._save_messages(db, conversation, first_new, turn_id, now)
            return turn_id

    @staticmethod
    def _save_conversation(db, conversation: Conversation, now: str) -> None:
        handoff = conversation.handoff.label.value if conversation.handoff else None
        # Started when the visitor first wrote, not when the page opened; kept from the first save.
        started = conversation.traces[0].created_at if conversation.traces else conversation.started_at
        db.execute(
            "INSERT INTO conversations (id, session_id, started_at, updated_at, state, handoff_label, visitor_messages) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET updated_at = excluded.updated_at, "
            "state = excluded.state, handoff_label = excluded.handoff_label, "
            "visitor_messages = excluded.visitor_messages",
            (conversation.id, conversation.session_id, started, now, conversation.state, handoff,
             conversation.visitor_messages),
        )

    @staticmethod
    def _save_messages(db, conversation: Conversation, first_new: int, turn_id: int | None, now: str) -> None:
        # Messages already saved are left as they are.
        db.executemany(
            "INSERT OR IGNORE INTO messages (conversation_id, position, turn_id, speaker, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (conversation.id, position, turn_id if position >= first_new else None, m.speaker, m.text, now)
                for position, m in enumerate(conversation.messages)
            ],
        )

    # ---- reading --------------------------------------------------------------

    def recent(self, limit: int = 100) -> list[LoggedConversation]:
        """The latest conversations, newest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.id, c.session_id, c.started_at, c.updated_at, c.state, c.handoff_label, c.visitor_messages, "
                "(SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.speaker = 'visitor' "
                "ORDER BY m.position LIMIT 1) FROM conversations c ORDER BY c.started_at DESC, c.rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            LoggedConversation(r[0], r[1], r[2], r[3], r[4], Label(r[5]) if r[5] else None, r[6], r[7] or "")
            for r in rows
        ]

    def totals(self) -> Totals:
        with self._connect() as db:
            return Totals(*(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            for table in ("conversations", "messages", "turns")))

    def messages(self, conversation_id: str) -> list[Message]:
        """Every message of a conversation, in order."""
        with self._connect() as db:
            rows = db.execute("SELECT speaker, text FROM messages WHERE conversation_id = ? ORDER BY position",
                              (conversation_id,)).fetchall()
        return [Message(speaker, text) for speaker, text in rows]

    def turns(self, conversation_id: str) -> list[Trace]:
        """Every turn of a conversation, oldest first, as the traces they were."""
        with self._connect() as db:
            rows = db.execute(f"SELECT {', '.join(_TURN_COLUMNS)} FROM turns WHERE conversation_id = ? ORDER BY number",
                              (conversation_id,)).fetchall()
        return [_trace(dict(zip(_TURN_COLUMNS, row))) for row in rows]

    def _connect(self):
        return connect(self.path, _SCHEMA)


def _turn_values(conversation_id: str, number: int, trace: Trace) -> tuple:
    m, f, v, d = trace.muneer, trace.filled, trace.verdict, trace.dai
    return (
        conversation_id, number, trace.created_at, trace.visitor_message, trace.outcome, trace.error,
        m and m.text, m and m.model, m and m.latency_ms, m and m.input_tokens, m and m.output_tokens,
        m and m.finish_reason, m and m.source,
        f and f.text, f and json.dumps(f.replacements, ensure_ascii=False),
        f and json.dumps(f.placeholders, ensure_ascii=False), f and json.dumps(f.missing, ensure_ascii=False),
        v and v.label.value, v and v.explanation, v and v.model, v and v.latency_ms, v and v.input_tokens,
        v and v.output_tokens,
        d and d.text, d and d.model, d and d.latency_ms, d and d.input_tokens, d and d.output_tokens,
        trace.review_id,
    )


def _trace(row: dict[str, Any]) -> Trace:
    muneer = filled = verdict = dai = None
    if row["muneer_raw_reply"] is not None:
        muneer = MuneerReply(row["muneer_raw_reply"], row["muneer_model"], row["muneer_latency_ms"],
                             row["muneer_input_tokens"], row["muneer_output_tokens"], row["muneer_finish_reason"],
                             row["muneer_source"])
    if row["filled_reply"] is not None:
        filled = FilledReply(
            raw=row["muneer_raw_reply"] or "",
            text=row["filled_reply"],
            replacements=tuple(tuple(r) for r in json.loads(row["replacements"] or "[]")),
            placeholders=tuple(tuple(p) for p in json.loads(row["placeholders"] or "[]")),
            missing=tuple(json.loads(row["missing_placeholders"] or "[]")),
        )
    if row["guardrail_label"] is not None:
        verdict = Verdict(Label(row["guardrail_label"]), row["guardrail_explanation"], row["guardrail_model"],
                          row["guardrail_latency_ms"], row["guardrail_input_tokens"], row["guardrail_output_tokens"])
    if row["dai_reply"] is not None:
        dai = DaiReply(row["dai_reply"], row["dai_model"], row["dai_latency_ms"], row["dai_input_tokens"],
                       row["dai_output_tokens"])
    return Trace(row["visitor_message"], row["outcome"], muneer, filled, verdict, dai, row["review_id"], row["error"],
                 row["created_at"])
