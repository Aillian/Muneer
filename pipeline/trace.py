"""The trace: what happened for one visitor message, step by step.

The Behind the scenes tab shows it, and the conversation log stores it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pipeline.database import utc_now
from pipeline.guardrail import Verdict
from pipeline.muneer import MuneerReply
from pipeline.placeholders import FilledReply
from pipeline.simulated_dai import DaiReply

Outcome = Literal["delivered", "handed_off", "stopped", "dai_replied", "error", "rejected", "closed"]


@dataclass
class Trace:
    """What happened for one visitor message: the behind-the-scenes record."""

    visitor_message: str
    outcome: Outcome = "rejected"
    muneer: MuneerReply | None = None
    filled: FilledReply | None = None
    verdict: Verdict | None = None
    dai: DaiReply | None = None
    review_id: int | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)  # when the message arrived (UTC)
