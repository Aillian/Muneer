"""The conversation as the visitor sees it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# muneer: Muneer's filled reply. dai: the (simulated) da'i after a handoff.
# notice: a message from the platform itself, such as the welcome or a handoff notice.
Speaker = Literal["visitor", "muneer", "dai", "notice"]


@dataclass(frozen=True)
class Message:
    speaker: Speaker
    text: str
