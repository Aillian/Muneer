"""Muneer: the fine-tuned model, reached through its OpenAI-compatible API.

The pipeline sends Muneer only the conversation; the model server adds
Muneer's system prompt (docs/adr/0002). Muneer's earlier replies go back raw,
exactly as it wrote them (docs/adr/0004). Until the model server is deployed,
MUNEER_BASE_URL can point at the mock (python -m mock_muneer).

Settings (environment variables):
    MUNEER_BASE_URL         required, e.g. https://<pod-id>-8000.proxy.runpod.net/v1
    MUNEER_API_KEY          the key the model server was started with
    MUNEER_MODEL            default muneer
    MUNEER_TIMEOUT_SECONDS  default 60, per attempt
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from pipeline.config import ConfigError, env_number, env_text
from pipeline.llm import ATTEMPTS

log = logging.getLogger(__name__)

# The most recent messages sent to Muneer: about 3,000-4,000 tokens, leaving
# room for the server's system prompt and Muneer's reply within the server's
# 8,192-token window.
HISTORY_CHARS = 12_000


@dataclass(frozen=True)
class MuneerReply:
    text: str  # the raw reply
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    source: str | None = None  # the server's fingerprint; the mock names its scenario here


class MuneerUnavailable(Exception):
    """Muneer gave no reply, even after a retry."""


class MuneerClient:
    def __init__(self, client: OpenAI, model: str = "muneer") -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> MuneerClient:
        base_url = env_text(env, "MUNEER_BASE_URL")
        if not base_url:
            raise ConfigError("Set MUNEER_BASE_URL in .env: the model server, or the mock (python -m mock_muneer).")
        client = OpenAI(
            api_key=env_text(env, "MUNEER_API_KEY", "no-key"),
            base_url=base_url,
            timeout=env_number(env, "MUNEER_TIMEOUT_SECONDS", 60),
            max_retries=0,
        )
        return cls(client, model=env_text(env, "MUNEER_MODEL", "muneer"))

    def reply(self, history: Sequence[Mapping[str, str]]) -> MuneerReply:
        """Muneer's raw reply to the conversation, given as OpenAI chat messages."""
        messages = trim_history(history, HISTORY_CHARS)
        failure: Exception | None = None
        for attempt in range(1, ATTEMPTS + 1):
            start = time.perf_counter()
            try:
                completion = self.client.chat.completions.create(model=self.model, messages=messages)
                choice = completion.choices[0]
                text = (choice.message.content or "").strip()
                if not text:
                    raise ValueError("empty reply")
            except (OpenAIError, ValueError) as err:
                failure = err
                log.warning("Muneer attempt %d of %d failed: %s", attempt, ATTEMPTS, type(err).__name__)
                continue
            usage = completion.usage
            return MuneerReply(
                text=text,
                model=completion.model,
                latency_ms=round((time.perf_counter() - start) * 1000),
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
                finish_reason=choice.finish_reason,
                source=completion.system_fingerprint,
            )
        raise MuneerUnavailable(f"No reply after {ATTEMPTS} attempts: {failure}") from failure


def trim_history(history: Sequence[Mapping[str, str]], budget: int) -> list[dict[str, str]]:
    """The most recent messages that fit in `budget` characters, starting with a
    visitor message. The latest message is always kept."""
    kept: list[dict[str, str]] = []
    total = 0
    for message in reversed(history):
        size = len(message["content"])
        if kept and total + size > budget:
            break
        kept.append(dict(message))
        total += size
    kept.reverse()
    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)
    return kept
