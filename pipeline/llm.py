"""Calling an OpenAI model for a structured answer, with one retry."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from openai import BadRequestError, OpenAI, OpenAIError
from pydantic import ValidationError

log = logging.getLogger(__name__)

ATTEMPTS = 2  # one try and one retry
T = TypeVar("T")

# Settings that not every model takes: reasoning models refuse a temperature,
# and other models, such as gpt-4.1, refuse a reasoning effort. When a model
# refuses one, it is left out of that request and every later one.
OPTIONAL_SETTINGS = ("reasoning_effort", "temperature")
# How OpenAI words such a refusal (checked against the live API). An invalid
# value, such as a mistyped reasoning effort, reads "Invalid value" instead and
# stays a failure.
REFUSALS = ("Unrecognized request argument", "Unsupported parameter", "Unsupported value")


@dataclass(frozen=True)
class Parsed(Generic[T]):
    answer: T
    model: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


def refused_setting(err: BadRequestError, request: dict[str, Any]) -> str | None:
    """The optional setting that a request error says the model doesn't take, if any."""
    body = err.body if isinstance(err.body, dict) else {}
    message = str(body.get("message") or err.message)
    if not any(refusal in message for refusal in REFUSALS):
        return None
    text = f"{body.get('param') or ''} {message}"
    return next((name for name in OPTIONAL_SETTINGS if name in request and name in text), None)


def parse_with_retry(
    client: OpenAI,
    request: dict[str, Any],
    *,
    name: str,
    error: type[Exception],
    refused: set[str] | None = None,
    check: Callable[[Any], None] | None = None,
) -> Parsed:
    """Ask for an answer matching request["response_format"]. A failure of any
    kind (network, HTTP error, refusal, wrong shape) is retried once, then
    raised as `error`.

    refused: the optional settings this model doesn't take. A setting the model
    refuses is added to it and the request is sent again without it; that
    doesn't count as a failed attempt.

    check: raises ValueError for an answer that can't be used, which then counts
    as a failed attempt.
    """
    refused = set() if refused is None else refused
    failure: Exception | None = None
    attempt = 0
    while attempt < ATTEMPTS:
        sent = {key: value for key, value in request.items() if key not in refused}
        start = time.perf_counter()
        try:
            completion = client.chat.completions.parse(**sent)
            message = completion.choices[0].message
            if message.parsed is None:
                raise ValueError(f"no answer (refusal: {message.refusal!r})")
            if check:
                check(message.parsed)
        except BadRequestError as err:
            if setting := refused_setting(err, sent):
                refused.add(setting)
                log.warning("%s: %s doesn't take %s; leaving it out from now on.", name, request.get("model"), setting)
                continue
            failure = err
        except (OpenAIError, ValidationError, ValueError) as err:
            failure = err
        else:
            usage = completion.usage
            return Parsed(
                answer=message.parsed,
                model=completion.model,
                latency_ms=round((time.perf_counter() - start) * 1000),
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
            )
        attempt += 1
        log.warning("%s attempt %d of %d failed: %s", name, attempt, ATTEMPTS, type(failure).__name__)
    raise error(f"No answer after {ATTEMPTS} attempts: {failure}") from failure
