import pytest

from pipeline.consent import asks_for_contact_details


@pytest.mark.parametrize(
    "text",
    [
        # English, including the current model's own wording
        "Please provide your WhatsApp number (with an international dialing code) for us to be able to do so.",
        "Can we have your email, please?",
        "Do you have a WhatsApp number, please?",
        "Do you have an email?",
        "Could you share your number?",
        # French, Spanish, Portuguese, Swahili, Filipino
        "Pouvez-vous me donner votre numéro WhatsApp ?",
        "Quelle est votre adresse e-mail ?",
        "¿Nos puedes dar tu número de WhatsApp?",
        "¿Tienes correo electrónico?",
        "Você pode nos informar seu número de WhatsApp?",
        "Tafadhali tupe namba yako ya WhatsApp.",
        "Pakibigay ang iyong numero sa WhatsApp.",
        "Ano ang email mo?",
    ],
)
def test_detects_a_request_for_contact_details(text):
    assert asks_for_contact_details(text)


@pytest.mark.parametrize(
    "text",
    [
        "You can reach our support team at support@example.org, and we will be glad to help.",
        "Please email us at support@example.org.",
        "Here is your guide: https://example.org/how-to-pray",
        "Muslims pray five times a day.",
        "Il a donné son numéro à la mosquée.",
        "Please email more questions any time.",
    ],
)
def test_ignores_other_messages(text):
    assert not asks_for_contact_details(text)
