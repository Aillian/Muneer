"""The guardrail: a second language model that reviews each of Muneer's replies.

The guardrail reads the conversation as the visitor sees it, then gives
Muneer's latest filled reply one of seven labels. It adds a short explanation,
but only classifies: it never writes or suggests a reply. The pipeline calls it
before the visitor sees anything (docs/adr/0001) and never shows a reply the guardrail could not
check: after one retry it raises GuardrailUnavailable.

Settings (environment variables):
    OPENAI_API_KEY              required
    OPENAI_BASE_URL             optional: another OpenAI-compatible provider
    GUARDRAIL_MODEL             default gpt-5.6-sol
    GUARDRAIL_TEMPERATURE       default none: the model's own (gpt-5.6-sol
                                takes no other)
    GUARDRAIL_REASONING_EFFORT  default low; empty sends none. Left out
                                for models that don't take it, like gpt-4.1
    GUARDRAIL_TIMEOUT_SECONDS   default 20, per attempt
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from pipeline.config import env_number, env_reasoning_effort, env_temperature, env_text, openai_client
from pipeline.conversation import Message
from pipeline.llm import ATTEMPTS, parse_with_retry

PROMPT_FILE = Path(__file__).with_name("prompts") / "guardrail.md"
HISTORY_LIMIT = 20  # most recent messages sent as context, to bound cost
MAX_OUTPUT_TOKENS = 4000  # includes reasoning; caps the cost of one review

__all__ = ["ATTEMPTS", "Guardrail", "GuardrailUnavailable", "Label", "Verdict"]


class Label(StrEnum):
    """The seven labels, valued with the names used in production logs."""

    OK = "OK"
    WRONG_FROM_ISLAMIC_POINT_OF_VIEW = "wrong_from_islamic_point_of_view"
    IMPROPER_RESPONSE = "response_wrong"
    CONTROVERSIAL_TOPIC = "wrong_controversial"
    TRANSFER_REQUESTED = "wrong_transfer_requested"
    VISITOR_UPSET = "wrong_transfer_upset"
    STOP = "STOP"

    @property
    def display_name(self) -> str:
        return _DISPLAY_NAMES[self]

    @property
    def hands_off(self) -> bool:
        """The conversation goes to a da'i."""
        return self not in (Label.OK, Label.STOP)


_DISPLAY_NAMES = {
    Label.OK: "OK",
    Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW: "Wrong from an Islamic point of view",
    Label.IMPROPER_RESPONSE: "Improper response",
    Label.CONTROVERSIAL_TOPIC: "Controversial topic",
    Label.TRANSFER_REQUESTED: "Transfer requested",
    Label.VISITOR_UPSET: "Visitor upset",
    Label.STOP: "Stop",
}


@dataclass(frozen=True)
class Verdict:
    """The guardrail's judgement on one reply."""

    label: Label
    explanation: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class GuardrailUnavailable(Exception):
    """The guardrail gave no usable verdict, even after a retry."""


class _Answer(BaseModel):
    """The JSON the guardrail model must return (enforced as a strict schema)."""

    explanation: str
    label: Label


class Guardrail:
    def __init__(
        self,
        client: OpenAI,
        model: str = "gpt-5.6-sol",
        temperature: float | None = None,
        reasoning_effort: str | None = "low",
        prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.refused: set[str] = set()  # settings this model turned down (pipeline/llm.py)
        self.prompt = prompt if prompt is not None else PROMPT_FILE.read_text(encoding="utf-8")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Guardrail:
        timeout = env_number(env, "GUARDRAIL_TIMEOUT_SECONDS", 20)
        return cls(
            openai_client(env, timeout),
            model=env_text(env, "GUARDRAIL_MODEL", "gpt-5.6-sol"),
            temperature=env_temperature(env, "GUARDRAIL_TEMPERATURE"),
            reasoning_effort=env_reasoning_effort(env, "GUARDRAIL_REASONING_EFFORT"),
        )

    def review(self, conversation: Sequence[Message], reply: str) -> Verdict:
        """Label Muneer's latest filled reply, given the conversation that led to it."""
        result = parse_with_retry(
            self.client,
            self._request(conversation, reply),
            name="Guardrail",
            error=GuardrailUnavailable,
            refused=self.refused,
        )
        answer: _Answer = result.answer
        return Verdict(
            label=answer.label,
            explanation=answer.explanation.strip(),
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def _request(self, conversation: Sequence[Message], reply: str) -> dict:
        data = {
            "conversation": [{"from": m.speaker, "text": m.text} for m in conversation[-HISTORY_LIMIT:]],
            "muneer_latest_reply": reply,
        }
        request = {
            "model": self.model,
            "messages": [
                # The fixed instructions come first, so the provider can cache them.
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": "Review Muneer's latest reply.\n\n" + json.dumps(data, ensure_ascii=False)},
            ],
            "response_format": _Answer,
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "store": False,  # conversations are sensitive; nothing is kept on OpenAI's side
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        return request
