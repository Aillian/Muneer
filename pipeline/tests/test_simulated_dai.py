import pytest

from pipeline.conversation import Message
from pipeline.guardrail import Label
from pipeline.simulated_dai import PROMPT_FILE, Handoff, SimulatedDai, SimulatedDaiUnavailable, has_link_or_email
from pipeline.tests.fakes import FakeOpenAI

HANDOFF = Handoff(
    label=Label.WRONG_FROM_ISLAMIC_POINT_OF_VIEW,
    reviewer_note="The reply says God is everywhere in His essence.",
    withheld_reply="God is present everywhere in His essence.",
)
CONVERSATION = [
    Message("notice", "Hello, welcome to our live chat."),
    Message("visitor", "Where is God?"),
    Message("notice", "You are being connected to one of our da'is."),
]


def answer(message="Assalamu alaikum! I'm Mohammad, one of Bathel's da'is."):
    return {"message": message}


def dai(*outcomes, **options):
    fake = FakeOpenAI(*outcomes)
    return SimulatedDai(fake.client(), **options), fake


def test_returns_the_message():
    d, _ = dai(answer("  Assalamu alaikum! How can I help?  "))
    reply = d.reply(CONVERSATION, HANDOFF)
    assert reply.text == "Assalamu alaikum! How can I help?"
    assert reply.model == "gpt-4.1-2025-04-14" and (reply.input_tokens, reply.output_tokens) == (1234, 56)


def test_sends_the_conversation_the_handoff_notes_and_the_organisation():
    d, fake = dai(answer(), organisation_name="Bathel")
    d.reply(CONVERSATION + [Message("dai", "Hello, I'm one of the da'is."), Message("visitor", "Thanks")], HANDOFF)
    assert fake.user_data() == {
        "conversation": [  # notices are left out
            {"from": "visitor", "text": "Where is God?"},
            {"from": "dai", "text": "Hello, I'm one of the da'is."},
            {"from": "visitor", "text": "Thanks"},
        ],
        "handoff": {
            "reason": "Wrong from an Islamic point of view",
            "reviewer_note": HANDOFF.reviewer_note,
            "withheld_reply": HANDOFF.withheld_reply,
        },
        "your_name": "Mohammad",
        "organisation_name": "Bathel",  # its email and links are never sent
    }
    sent = fake.requests[0]
    assert sent["messages"][0] == {"role": "system", "content": PROMPT_FILE.read_text(encoding="utf-8")}
    assert sent["model"] == "gpt-5.6-sol" and sent["store"] is False
    assert "temperature" not in sent and sent["reasoning_effort"] == "low"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert set(sent["response_format"]["json_schema"]["schema"]["properties"]) == {"message"}  # one message, nothing more


def test_a_setting_the_model_refuses_is_left_out_from_then_on():
    no_effort = (400, "Unrecognized request argument supplied: reasoning_effort", None)
    d, fake = dai(no_effort, answer(), answer())
    d.reply(CONVERSATION, HANDOFF)
    d.reply(CONVERSATION, HANDOFF)
    assert ["reasoning_effort" in r for r in fake.requests] == [True, False, False]


def test_a_message_with_a_link_or_an_email_is_asked_for_again():
    d, fake = dai(answer("Write to us at info@bathel.sa."), answer("Allah is above His Throne."))
    assert d.reply(CONVERSATION, HANDOFF).text == "Allah is above His Throne."
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "text",
    [
        "Write to us at info@bathel.sa.",
        "Visit https://bathel.sa/ for more.",
        "See www.bathel.sa",
        "You can find us at bathel.sa.",
        "More at Example.ORG",
        "Voir islamhouse.fr pour plus.",
        "Bisitahin ang islam.ph ngayon.",
        "Watch it at bit.ly/shahada",
    ],
)
def test_a_link_or_an_email_is_never_shown(text):
    # A second message with one counts as unavailable: the visitor is asked to send theirs again.
    d, fake = dai(answer(text), answer(text))
    with pytest.raises(SimulatedDaiUnavailable):
        d.reply(CONVERSATION, HANDOFF)
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "text",
    [
        "The Most Merciful rose over the Throne (Surah Taha, 20:5).",
        "It is reported in Sahih al-Bukhari, e.g. in the Book of Faith.",
        "Peace be upon him. Muslims pray five times a day.",
        "Ask a qualified scholar or your local mosque.",
        "There are many mosques in the U.K. and the U.S.",
        "Hay muchas mezquitas en EE.UU., p.ej. en Nueva York.",
        "Il y a des mosquées en France, p.ex. à Paris (i.e. partout).",
    ],
)
def test_ordinary_text_is_not_taken_for_a_link(text):
    assert not has_link_or_email(text)


def test_retries_once():
    d, fake = dai(500, answer())
    assert d.reply(CONVERSATION, HANDOFF).text.startswith("Assalamu alaikum")
    assert len(fake.requests) == 2


def test_gives_up_after_the_retry():
    d, _ = dai(500, "refusal")
    with pytest.raises(SimulatedDaiUnavailable, match="after 2 attempts"):
        d.reply(CONVERSATION, HANDOFF)


def test_an_empty_message_counts_as_unavailable():
    d, _ = dai(answer("   "))
    with pytest.raises(SimulatedDaiUnavailable, match="empty"):
        d.reply(CONVERSATION, HANDOFF)


def test_prompt_keeps_the_da_i_honest():
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    assert "simulated by AI" in prompt
    assert "never deny being an AI" in prompt
    assert "your_name" in prompt and "Don't invent an age" in prompt
    assert "null if it isn't given" in prompt and "never make up an organisation's name" in prompt
    assert "exactly one message" in prompt and "don't ask the visitor anything" in prompt
    assert "withheld_reply" in prompt and "never saw it" in prompt
    assert "Never include a link, a website or an email address" in prompt
    assert "share the organisation's" not in prompt
    assert "suggested_correction" not in prompt  # the guardrail no longer suggests answers


def test_from_env():
    d = SimulatedDai.from_env({"OPENAI_API_KEY": "sk-test"}, organisation_name="Center")
    assert (d.model, d.temperature, d.reasoning_effort, d.client.timeout) == ("gpt-5.6-sol", None, "low", 30)
    assert d.organisation_name == "Center" and d.name == "Mohammad"
    d = SimulatedDai.from_env({
        "OPENAI_API_KEY": "sk-test",
        "SIMULATED_DAI_MODEL": "gpt-4.1-mini",
        "SIMULATED_DAI_TEMPERATURE": "0.9",
        "SIMULATED_DAI_REASONING_EFFORT": "",
        "SIMULATED_DAI_TIMEOUT_SECONDS": "12",
        "SIMULATED_DAI_NAME": "Yusuf",
    })
    assert d.name == "Yusuf"
    assert (d.model, d.temperature, d.reasoning_effort, d.client.timeout) == ("gpt-4.1-mini", 0.9, None, 12)
