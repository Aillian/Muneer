"""Reading the pipeline's settings from environment variables."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openai import OpenAI


class ConfigError(Exception):
    """A setting is missing or invalid."""


def env_text(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = (env.get(name) or "").strip()
    return value or default


def env_json(env: Mapping[str, str], name: str, default: Any) -> Any:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise ConfigError(f"{name} is not valid JSON: {err.msg} at character {err.pos}") from None


def env_number(env: Mapping[str, str], name: str, default: float) -> float:
    """A number greater than 0."""
    text = env_text(env, name)
    if text is None:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {text!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be more than 0.")
    return value


def env_count(env: Mapping[str, str], name: str, default: int) -> int:
    """A whole number of at least 1."""
    text = env_text(env, name)
    if text is None:
        return default
    try:
        value = int(text)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {text!r}") from None
    if value < 1:
        raise ConfigError(f"{name} must be at least 1.")
    return value


REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


def env_reasoning_effort(env: Mapping[str, str], name: str, default: str = "low") -> str | None:
    """Unset means the default effort; set but empty means send none."""
    value = env.get(name)
    if value is None:
        return default
    effort = value.strip().lower()
    if effort and effort not in REASONING_EFFORTS:
        raise ConfigError(f"{name} must be one of {', '.join(REASONING_EFFORTS)}, or empty; got {value.strip()!r}")
    return effort or None


def env_temperature(env: Mapping[str, str], name: str, default: float | None = None) -> float | None:
    """From 0 to 2. Unset means the default; set but empty means send none (the model's own)."""
    value = env.get(name)
    if value is None:
        return default
    text = value.strip()
    if not text:
        return None
    try:
        temperature = float(text)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {text!r}") from None
    if not 0 <= temperature <= 2:
        raise ConfigError(f"{name} must be from 0 to 2.")
    return temperature


def openai_client(env: Mapping[str, str], timeout: float) -> OpenAI:
    """The OpenAI client shared by the guardrail and the simulated da'i.
    Retries are done by the caller, once."""
    key = env_text(env, "OPENAI_API_KEY")
    if not key:
        raise ConfigError("Set OPENAI_API_KEY in .env; the guardrail and the simulated da'i need it.")
    return OpenAI(api_key=key, base_url=env_text(env, "OPENAI_BASE_URL"), timeout=timeout, max_retries=0)
