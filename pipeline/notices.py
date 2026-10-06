"""Notices: the messages the platform itself shows the visitor.

Muneer is never asked to write these (docs/adr/0002). The consent notice's
text is a setting (CONSENT_NOTICE); see consent.py.
"""

# Shown at the start of every conversation. The welcome and disclosure follow
# the wording the production chat platform used.
WELCOME = "Hello, welcome to our live chat. Feel free to ask about Islam or about converting to it."
AI_DISCLOSURE = "You are now chatting with Muneer, an AI Islamic adviser. Please note that AI may occasionally make mistakes."

# {name}: the simulated da'i's name (SIMULATED_DAI_NAME).
HANDOFF = "You're being connected to {name}, one of our da'is."
# After the da'i's one reply, the demo ends and the conversation closes (docs/adr/0005).
HANDED_OVER = ("Muneer's turn ends here: {name}, our da'i, takes it from here. In real use, a human da'i would "
               "continue the conversation; in this demo, the da'i is simulated by AI and replies only once. "
               "To chat with Muneer again, start a new conversation.")
STOPPED = ("You won't receive any more messages from us. If you ever have a question about Islam, "
           "you're welcome to start a new conversation.")
CLOSED = "This conversation has ended. Please start a new conversation to continue."

TRY_AGAIN = "Sorry, something went wrong on our side and your message wasn't answered. Please send it again."
DAI_BUSY = "Our da'i isn't available right now. Please send your message again in a little while."

TOO_LONG = "Your message is too long. Please keep it under {limit:,} characters."
LIMIT_REACHED = ("This conversation has reached its limit of {limit} messages. "
                 "Please start a new conversation to continue.")
