"""The consent notice: shown whenever a message asks for the visitor's contact details.

Muneer learned to ask for a WhatsApp number or email without saying why or
that it is optional. The pipeline adds the notice after any message (from
Muneer or the da'i) that asks for them, in the six languages the current
model speaks.
"""

from __future__ import annotations

import re

# An email address: masked in the review queue, and never sent by the simulated da'i.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

DEFAULT_CONSENT_NOTICE = (
    "Sharing your contact details is optional. We use them only to send you learning material "
    "about Islam, and you can ask us to stop at any time."
)

_ASKS_FOR_CONTACT = re.compile(
    "|".join([
        # English: "your WhatsApp number", "Do you have an email?"
        r"\byour\s+(?:whats\s?app|phone|mobile|cell|telephone|number|e-?mail)",
        r"\bdo\s+you\s+have\s+an?\s+(?:whats\s?app|phone|mobile|number|e-?mail)",
        # French
        r"\bvotre\s+(?:num[ée]ro|adresse\s+e-?mail|e-?mail|courriel|whats\s?app|t[ée]l[ée]phone)",
        r"\bavez-vous\s+une?\s+(?:num[ée]ro|adresse|e-?mail|whats\s?app)",
        # Spanish
        r"\b(?:tu|su)\s+(?:n[úu]mero|correo|e-?mail|whats\s?app|tel[ée]fono)",
        r"\btienes?\s+(?:un\s+)?(?:n[úu]mero|correo|e-?mail|whats\s?app)",
        # Portuguese
        r"\b(?:seu|sua|teu|tua)\s+(?:n[úu]mero|e-?mail|whats\s?app|telefone)",
        # Swahili: "namba yako", "barua pepe yako"
        r"\b(?:namba|nambari|barua\s+pepe|whats\s?app)\s+yako\b",
        # Filipino: "iyong numero", "numero mo"
        r"\b(?:iyong|inyong)\s+(?:numero|e-?mail|whats\s?app)",
        r"\b(?:numero|e-?mail|whats\s?app)\s+(?:mo|ninyo)\b",
    ]),
    re.IGNORECASE,
)


def asks_for_contact_details(text: str) -> bool:
    return bool(_ASKS_FOR_CONTACT.search(text))
