"""The mock's scripted replies, one scenario per path through the pipeline.

Some replies copy what the current model really writes, such as its old
persona name and its refusal to transfer a chat. Others use placeholders,
which the next model version will write. The planted errors give the
guardrail something to catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    phrases: tuple[str, ...]  # matched as whole words in the visitor's last message
    reply: str


# Checked in this order; the first scenario with a matching phrase wins.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "stop",
        ("stop messaging", "stop writing", "stop texting", "stop bothering", "stop sending", "don't message",
         "do not message", "don't contact", "do not contact", "leave me alone", "unsubscribe"),
        "If at any point you have any concerns or questions about Islam, then please feel free to contact us.",
    ),
    Scenario(
        "transfer",
        ("talk to a human", "speak to a human", "talk to a person", "speak to a person", "talk to someone",
         "speak to someone", "real person", "human agent", "transfer me", "transfer this chat"),
        # What the current model writes when asked for a human.
        "I am sorry I will not be able to transfer this chat to another advisor.",
    ),
    Scenario(
        "upset",
        ("useless", "stupid", "annoying", "waste of time", "not helpful", "you are wrong", "you're wrong",
         "i am angry", "i'm angry", "so frustrating", "ridiculous"),
        "I understand. Is there anything else I can help you with?",
    ),
    Scenario(
        "controversial",
        ("politics", "political", "jihad", "israel", "hamas", "palestine", "terrorism", "terrorist", "isis"),
        "People hold strong views on that topic, and there are many sides to it. What exactly would you like to know?",
    ),
    Scenario(
        "creed_error",
        ("where is god", "where is allah", "god everywhere", "allah everywhere"),
        # Planted error: the guardrail must catch this.
        "God is present everywhere in His essence, so He is with you in every place at every moment.",
    ),
    Scenario(
        "pushy",
        ("not sure", "not ready", "maybe later", "need time", "need more time", "think about it", "let me think"),
        # Planted improper response: pushy, not wrong in belief.
        "There is no time to think about it. You must accept Islam right now, or you are making a huge "
        "mistake that you will regret forever.",
    ),
    Scenario(
        "said_shahada",
        ("i said it", "i have said it", "i've said it", "i just said it", "i said the shahada", "i pronounced it",
         "i repeated it"),
        "Congratulations, you are now a Muslim! I have two good tidings for you: Islam wipes away all the sins "
        "that came before it, and every good deed you do from now on is multiplied many times over. We would "
        "like to add you to a WhatsApp learning group. Please provide your WhatsApp number (with an "
        "international dialing code) for us to be able to do so.",
    ),
    Scenario(
        "convert",
        ("become a muslim", "become muslim", "convert", "converting", "embrace islam", "accept islam", "revert",
         "shahada"),
        "That is wonderful news! To become a Muslim, you pronounce the testimony of faith (Shahada) with sincere "
        "belief: \"Ash-hadu an la ilaha illa Allah, wa ash-hadu anna Muhammadan rasul Allah\". It means: \"I bear "
        "witness that there is no god but Allah, and I bear witness that Muhammad is the Messenger of Allah.\" "
        "If you would like to hear it recited first, write to [org_name] at [org_email] and we will send "
        "you a recording. Then repeat it aloud, and tell me once you have said it.",
    ),
    Scenario(
        "identity",
        ("who are you", "what are you", "your name", "are you human", "are you a bot", "are you a robot",
         "are you ai", "are you an ai", "who made you", "who created you", "who developed you"),
        # The current model's persona line; replacement rules turn it into Muneer.
        "I'm Daee, an AI Islamic Advisor. I'm here to answer your questions about Islam. How can I help you today?",
    ),
    Scenario(
        "prayer",
        ("pray", "prayer", "prayers", "salah", "salat", "namaz"),
        "Muslims pray five times a day: at dawn, midday, afternoon, sunset and night. Each prayer includes "
        "recitation from the Qur'an, bowing and prostration, and takes only a few minutes. For a simple "
        "step-by-step guide, write to [org_name] at [org_email].",
    ),
    Scenario(
        "contact",
        ("email", "contact you", "reach you", "support", "get in touch"),
        "You can reach our support team at [org_email], and we will be glad to help.",
    ),
    Scenario(
        "newsletter",
        ("newsletter", "subscribe"),
        # newsletter_url is deliberately missing from the example profile.
        "You can subscribe to our newsletter here: [newsletter_url]",
    ),
    Scenario(
        "about_islam",
        ("what is islam", "about islam", "islam mean", "overview of islam", "what do muslims believe"),
        "Islam means submission to the One God, Allah. Muslims believe in all the prophets God sent, including "
        "Abraham, Moses, Jesus and Muhammad, peace be upon them, with Muhammad as the final messenger. Islam is "
        "built on five pillars: the testimony of faith, prayer, charity, fasting in Ramadan and the pilgrimage "
        "to Makkah. Would you like to know more about any of them?",
    ),
    Scenario(
        "greeting",
        ("hello", "hi", "hey", "salam", "assalamu alaikum", "as-salamu alaykum", "good morning", "good evening"),
        "Hello, welcome to our live help. How can I assist you today?",
    ),
)

FALLBACK = Scenario(
    "fallback",
    (),
    "Thank you for your question. Islam teaches belief in the One God, Allah, and in all of His prophets. "
    "Could you tell me a little more about what you would like to know, so I can give you a clear answer?",
)

_PATTERNS = tuple(
    (scenario, re.compile("|".join(rf"(?<!\w){re.escape(p)}(?!\w)" for p in scenario.phrases)))
    for scenario in SCENARIOS
)


def pick(message: str) -> Scenario:
    """Choose the scenario for the visitor's message."""
    text = " ".join(message.lower().replace("’", "'").split())
    for scenario, pattern in _PATTERNS:
        if pattern.search(text):
            return scenario
    return FALLBACK
