"""The simulated da'i: an AI that plays the da'i after a handoff (docs/adr/0005).

It exists to demonstrate the handoff: it sends exactly one message, giving
the visitor what Muneer couldn't, and the conversation then closes. In real
use a human da'i takes over. Its message is always labelled as simulated. The
guardrail doesn't check it, because it stands in for a person.

Settings (environment variables):
    OPENAI_API_KEY                  required (shared with the guardrail)
    OPENAI_BASE_URL                 optional
    SIMULATED_DAI_NAME              default Mohammad
    SIMULATED_DAI_MODEL             default gpt-5.6-sol
    SIMULATED_DAI_TEMPERATURE       default none: the model's own
    SIMULATED_DAI_REASONING_EFFORT  default low; left out for models that
                                    don't take it, like gpt-4.1
    SIMULATED_DAI_TIMEOUT_SECONDS   default 30, per attempt
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from pipeline.config import env_number, env_reasoning_effort, env_temperature, env_text, openai_client
from pipeline.consent import EMAIL
from pipeline.conversation import Message
from pipeline.guardrail import Label
from pipeline.llm import parse_with_retry

PROMPT_FILE = Path(__file__).with_name("prompts") / "simulated_dai.md"
HISTORY_LIMIT = 20  # most recent messages sent, to bound cost
DEFAULT_NAME = "Mohammad"
MAX_OUTPUT_TOKENS = 3000  # includes reasoning

# The da'i's message points nowhere outside the chat: no email address, web
# address or bare domain such as bathel.sa or bit.ly/x. A domain ends in two or
# more letters, so abbreviations such as "U.K." and "e.g." don't count.
LINK_OR_EMAIL = re.compile(
    EMAIL.pattern
    + r"|(?:https?://|www\.)\S+"
    + r"|\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b(?:/\S*)?",
    re.IGNORECASE,
)
# Abbreviations that look like a domain: Spanish "EE.UU." (the United States),
# and "p.ej." and "p.ex." (Spanish, French and Portuguese for "for example").
NOT_LINKS = frozenset({"ee.uu", "p.ej", "p.ex"})


@dataclass(frozen=True)
class Handoff:
    """Why a conversation went to the da'i: the notes a real da'i would read."""

    label: Label
    reviewer_note: str
    withheld_reply: str  # the filled reply the visitor never saw


@dataclass(frozen=True)
class DaiReply:
    text: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class SimulatedDaiUnavailable(Exception):
    """The simulated da'i gave no usable message, even after a retry."""


class _Answer(BaseModel):
    message: str


def has_link_or_email(text: str) -> bool:
    return any(m.group().lower() not in NOT_LINKS for m in LINK_OR_EMAIL.finditer(text))


def _no_link_or_email(answer: _Answer) -> None:
    if has_link_or_email(answer.message):
        raise ValueError("the message contains a link or an email address")


class SimulatedDai:
    def __init__(
        self,
        client: OpenAI,
        model: str = "gpt-5.6-sol",
        temperature: float | None = None,
        reasoning_effort: str | None = "low",
        name: str = DEFAULT_NAME,
        organisation_name: str | None = None,
        prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.refused: set[str] = set()  # settings this model turned down (pipeline/llm.py)
        self.name = name
        self.organisation_name = organisation_name
        self.prompt = prompt if prompt is not None else PROMPT_FILE.read_text(encoding="utf-8")

    @classmethod
    def from_env(cls, env: Mapping[str, str], organisation_name: str | None = None) -> SimulatedDai:
        """organisation_name: so the da'i can introduce itself as one of the
        organisation's da'is. It gets no email or link: it shares none."""
        timeout = env_number(env, "SIMULATED_DAI_TIMEOUT_SECONDS", 30)
        return cls(
            openai_client(env, timeout),
            model=env_text(env, "SIMULATED_DAI_MODEL", "gpt-5.6-sol"),
            temperature=env_temperature(env, "SIMULATED_DAI_TEMPERATURE"),
            reasoning_effort=env_reasoning_effort(env, "SIMULATED_DAI_REASONING_EFFORT"),
            name=env_text(env, "SIMULATED_DAI_NAME", DEFAULT_NAME),
            organisation_name=organisation_name,
        )

    def reply(self, conversation: Sequence[Message], handoff: Handoff) -> DaiReply:
        """The da'i's one message, given the conversation (visitor and Muneer messages)."""
        result = parse_with_retry(
            self.client,
            self._request(conversation, handoff),
            name="Simulated da'i",
            error=SimulatedDaiUnavailable,
            refused=self.refused,
            check=_no_link_or_email,  # asked again once; a second one counts as unavailable
        )
        answer: _Answer = result.answer
        if not answer.message.strip():
            raise SimulatedDaiUnavailable("The simulated da'i returned an empty message.")
        return DaiReply(
            text=answer.message.strip(),
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def _request(self, conversation: Sequence[Message], handoff: Handoff) -> dict:
        data = {
            "conversation": [
                {"from": m.speaker, "text": m.text} for m in conversation if m.speaker != "notice"
            ][-HISTORY_LIMIT:],
            "handoff": {
                "reason": handoff.label.display_name,
                "reviewer_note": handoff.reviewer_note,
                "withheld_reply": handoff.withheld_reply,
            },
            "your_name": self.name,
            "organisation_name": self.organisation_name,
        }
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": "Write the da'i's one message.\n\n" + json.dumps(data, ensure_ascii=False)},
            ],
            "response_format": _Answer,
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        return request
