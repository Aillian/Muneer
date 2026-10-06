"""The pipeline: the steps run for every visitor message.

    visitor message
      -> Muneer writes a raw reply              muneer.py (only the conversation, docs/adr/0002)
      -> placeholder filling: the filled reply  placeholders.py
      -> the guardrail reviews the filled reply guardrail.py (before the visitor sees it, docs/adr/0001)
      -> OK: the reply is delivered
         Stop: the conversation is closed
         anything else: the reply is held back and the conversation is handed
         off to the (simulated) da'i            simulated_dai.py (docs/adr/0005)
      -> a consent notice follows any message that asks for contact details

Every conversation, message and turn is saved to the conversation log, and
flagged replies also go to the review queue; both live in the database
(DATABASE_PATH, database.py). After a handoff, the simulated da'i
replies once and the conversation closes: the demo shows the handoff without
visitors chatting with the da'i's model. Nothing unchecked is ever shown: if
Muneer, the guardrail or anything else fails, the visitor is asked to send the
message again.

Settings (environment variables), besides each step's own:
    MAX_MESSAGE_CHARS     default 2000
    MAX_VISITOR_MESSAGES  default 40 per conversation
    CONSENT_NOTICE        the consent notice's text
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from pipeline import notices
from pipeline.config import env_count, env_text
from pipeline.consent import DEFAULT_CONSENT_NOTICE, asks_for_contact_details
from pipeline.conversation import Message, Speaker
from pipeline.conversation_log import ConversationLog
from pipeline.database import database_path, utc_now
from pipeline.guardrail import Guardrail, GuardrailUnavailable, Label
from pipeline.muneer import MuneerClient, MuneerUnavailable
from pipeline.placeholders import PlaceholderFiller
from pipeline.review_queue import ReviewQueue
from pipeline.simulated_dai import Handoff, SimulatedDai, SimulatedDaiUnavailable
from pipeline.trace import Outcome, Trace

__all__ = ["Conversation", "Outcome", "Pipeline", "PipelineSettings", "Trace", "TurnResult"]

log = logging.getLogger(__name__)

State = Literal["muneer", "dai", "closed"]
T = TypeVar("T")


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str | None = None  # the browser page it came from, for the conversation log
    started_at: str = field(default_factory=utc_now)
    messages: list[Message] = field(default_factory=list)  # everything the visitor sees
    muneer_history: list[dict[str, str]] = field(default_factory=list)  # raw, as Muneer wrote it
    state: State = "muneer"
    handoff: Handoff | None = None
    visitor_messages: int = 0
    traces: list[Trace] = field(default_factory=list)

    def visible(self, *speakers: Speaker) -> list[Message]:
        return [m for m in self.messages if m.speaker in speakers]


@dataclass(frozen=True)
class TurnResult:
    messages: list[Message]  # the messages this turn added, the visitor's own first
    trace: Trace


@dataclass(frozen=True)
class PipelineSettings:
    max_message_chars: int = 2000
    max_visitor_messages: int = 40
    consent_notice: str = DEFAULT_CONSENT_NOTICE

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PipelineSettings:
        return cls(
            max_message_chars=env_count(env, "MAX_MESSAGE_CHARS", 2000),
            max_visitor_messages=env_count(env, "MAX_VISITOR_MESSAGES", 40),
            consent_notice=env_text(env, "CONSENT_NOTICE", DEFAULT_CONSENT_NOTICE),
        )


class Pipeline:
    def __init__(
        self,
        muneer: MuneerClient,
        filler: PlaceholderFiller,
        guardrail: Guardrail,
        dai: SimulatedDai,
        review_queue: ReviewQueue | None = None,
        settings: PipelineSettings = PipelineSettings(),
        conversation_log: ConversationLog | None = None,
    ) -> None:
        self.muneer = muneer
        self.filler = filler
        self.guardrail = guardrail
        self.dai = dai
        self.review_queue = review_queue
        self.settings = settings
        self.conversation_log = conversation_log

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Pipeline:
        filler = PlaceholderFiller.from_env(env)
        path = database_path(env)
        return cls(
            muneer=MuneerClient.from_env(env),
            filler=filler,
            guardrail=Guardrail.from_env(env),
            dai=SimulatedDai.from_env(env, organisation_name=filler.profile.get("org_name")),
            review_queue=_open(ReviewQueue, path, "flagged replies won't be saved"),
            settings=PipelineSettings.from_env(env),
            conversation_log=_open(ConversationLog, path, "conversations won't be saved"),
        )

    def start(self, session_id: str | None = None) -> Conversation:
        """A new conversation, opened with the welcome and the AI disclosure.
        session_id: the browser page it comes from, kept in the conversation log.
        It is saved with the visitor's first message, so a page that is opened
        and left adds nothing to the database."""
        return Conversation(
            session_id=session_id,
            messages=[Message("notice", notices.WELCOME), Message("notice", notices.AI_DISCLOSURE)],
        )

    def handle(self, conversation: Conversation, text: str) -> TurnResult:
        """Run the pipeline for one visitor message. Updates `conversation` in place."""
        first_new = len(conversation.messages)
        text = text.strip()
        limit = self.settings.max_message_chars
        # The browser enforces the limit, but a request can bypass it: never keep more than the limit.
        trace = Trace(visitor_message=text if len(text) <= limit else text[:limit] + "…")
        if conversation.state == "closed":
            trace.outcome = "closed"
            self._notice(conversation, notices.CLOSED)
        elif not text:
            pass  # nothing to answer
        elif len(text) > self.settings.max_message_chars:
            self._notice(conversation, notices.TOO_LONG.format(limit=self.settings.max_message_chars))
        elif conversation.visitor_messages >= self.settings.max_visitor_messages:
            conversation.state = "closed"
            self._notice(conversation, notices.LIMIT_REACHED.format(limit=self.settings.max_visitor_messages))
        else:
            conversation.messages.append(Message("visitor", text))
            conversation.visitor_messages += 1
            try:
                if conversation.state == "dai":
                    self._ask_dai(conversation, trace)
                else:
                    self._ask_muneer(conversation, text, trace)
            except Exception as err:  # anything unexpected: the visitor is asked to send it again
                log.exception("conversation %s: unexpected error", conversation.id[:8])
                self._fail(conversation, trace, f"Unexpected error: {type(err).__name__}")
        conversation.traces.append(trace)
        self._save(conversation, lambda log: log.record(conversation, trace, first_new))
        # Log what happened, never what was said: what was said goes only to the database.
        log.info("conversation %s: %s%s", conversation.id[:8], trace.outcome,
                 f" ({trace.verdict.label.value})" if trace.verdict else "")
        return TurnResult(conversation.messages[first_new:], trace)

    # ---- the steps ------------------------------------------------------------

    def _ask_muneer(self, conversation: Conversation, text: str, trace: Trace) -> None:
        history = [*conversation.muneer_history, {"role": "user", "content": text}]
        try:
            trace.muneer = self.muneer.reply(history)
        except MuneerUnavailable as err:
            return self._fail(conversation, trace, f"Muneer unavailable: {err}")

        trace.filled = self.filler.fill(trace.muneer.text)
        try:
            trace.verdict = self.guardrail.review(conversation.visible("visitor", "muneer"), trace.filled.text)
        except GuardrailUnavailable as err:
            return self._fail(conversation, trace, f"Guardrail unavailable: {err}")

        label = trace.verdict.label
        if label is Label.OK:
            # Muneer's history keeps the raw reply; the visitor sees the filled one.
            conversation.muneer_history = [*history, {"role": "assistant", "content": trace.muneer.text}]
            self._deliver(conversation, Message("muneer", trace.filled.text))
            trace.outcome = "delivered"
            return

        trace.review_id = self._add_to_review_queue(conversation, trace)
        if label.hands_off:
            conversation.handoff = Handoff(
                label=label,
                reviewer_note=trace.verdict.explanation,
                withheld_reply=trace.filled.text,
            )
            conversation.state = "dai"
            trace.outcome = "handed_off"
            self._notice(conversation, notices.HANDOFF.format(name=self.dai.name))
            self._ask_dai(conversation, trace)
        else:  # STOP
            self._close(conversation, trace)

    def _ask_dai(self, conversation: Conversation, trace: Trace) -> None:
        """The da'i's one reply; the demo then ends and the conversation closes."""
        try:
            trace.dai = self.dai.reply(conversation.visible("visitor", "muneer", "dai"), conversation.handoff)
        except SimulatedDaiUnavailable as err:
            trace.error = f"Simulated da'i unavailable: {err}"
            if trace.outcome != "handed_off":
                trace.outcome = "error"
            self._notice(conversation, notices.DAI_BUSY)
            return  # still with the da'i: the visitor's next message asks again
        self._deliver(conversation, Message("dai", trace.dai.text))
        if trace.outcome != "handed_off":
            trace.outcome = "dai_replied"
        conversation.state = "closed"
        self._notice(conversation, notices.HANDED_OVER.format(name=self.dai.name))

    # ---- helpers ----------------------------------------------------------------

    def _deliver(self, conversation: Conversation, message: Message) -> None:
        conversation.messages.append(message)
        if asks_for_contact_details(message.text):
            self._notice(conversation, self.settings.consent_notice)

    def _close(self, conversation: Conversation, trace: Trace) -> None:
        conversation.state = "closed"
        trace.outcome = "stopped"
        self._notice(conversation, notices.STOPPED)

    def _fail(self, conversation: Conversation, trace: Trace, error: str) -> None:
        # The visitor's message stays on screen, but it never reaches Muneer's
        # history, so sending it again starts clean.
        trace.outcome = "error"
        trace.error = error
        log.error("conversation %s: %s", conversation.id[:8], error)
        self._notice(conversation, notices.TRY_AGAIN)

    def _save(self, conversation: Conversation, write: Callable[[ConversationLog], object]) -> None:
        """Write to the conversation log. The chat must not break because of it."""
        if self.conversation_log is None:
            return
        try:
            write(self.conversation_log)
        except Exception:
            log.exception("conversation %s: could not save it to the conversation log", conversation.id[:8])

    @staticmethod
    def _notice(conversation: Conversation, text: str) -> None:
        conversation.messages.append(Message("notice", text))

    def _add_to_review_queue(self, conversation: Conversation, trace: Trace) -> int | None:
        if self.review_queue is None:
            return None
        try:
            return self.review_queue.add(
                conversation_id=conversation.id,
                verdict=trace.verdict,
                filled=trace.filled,
                conversation=conversation.visible("visitor", "muneer"),
            )
        except (sqlite3.Error, OSError) as err:  # the chat must not break because of the queue
            log.error("conversation %s: could not save the flagged reply: %s", conversation.id[:8], err)
            return None


def _open(kind: Callable[[str], T], path: str, consequence: str) -> T | None:
    """The review queue or the conversation log, or None if the database can't be
    opened: the chat runs without it rather than not at all."""
    try:
        return kind(path)
    except (sqlite3.Error, OSError) as err:
        log.error("The database can't be opened (%s); %s.", err, consequence)
        return None
