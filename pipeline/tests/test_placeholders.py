import logging
from pathlib import Path

import pytest
from dotenv import dotenv_values

from pipeline.config import ConfigError
from pipeline.placeholders import PlaceholderFiller, ReplacementRule as Rule

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


# ---- placeholders --------------------------------------------------------


def test_fills_placeholders_from_the_profile():
    reply = PlaceholderFiller(profile={"org_email": "help@example.org"}).fill("Email us at [org_email].")
    assert reply.text == "Email us at help@example.org."
    assert reply.raw == "Email us at [org_email]."
    assert reply.placeholders == (("org_email", 1),)
    assert reply.missing == () and reply.changed


def test_tolerates_spaces_inside_the_markers():
    assert PlaceholderFiller(profile={"org_name": "X"}).fill("[ org_name ]").text == "X"


def test_profile_names_are_not_case_sensitive():
    assert PlaceholderFiller(profile={"ORG_NAME": "X"}).fill("[org_name]").text == "X"


def test_removes_and_reports_a_placeholder_without_a_value(caplog):
    with caplog.at_level(logging.WARNING):
        reply = PlaceholderFiller().fill("Subscribe here: [newsletter_url] and [newsletter_url]")
    assert reply.text == "Subscribe here:  and "
    assert reply.missing == ("newsletter_url",)
    assert "newsletter_url" in caplog.text


def test_only_snake_case_names_with_an_underscore_are_placeholders():
    # Hadith citations, and the words translators add inside quoted verses, must survive untouched.
    filler = PlaceholderFiller(profile={"org_email": "x@y.z"})
    text = "[Muslim] [1] [End of quote] [Al-Bukhari and Muslim] [so] [it] [org_email]"
    reply = filler.fill(text)
    assert reply.text == "[Muslim] [1] [End of quote] [Al-Bukhari and Muslim] [so] [it] x@y.z"
    assert reply.missing == ()
    verse = "Allah does not charge a soul except [with that within] its capacity. [So] fear Allah."
    assert filler.fill(verse).text == verse


def test_custom_markers():
    filler = PlaceholderFiller(profile={"org_name": "1"}, open_marker="{{", close_marker="}}")
    assert filler.fill("{{org_name}} [org_name]").text == "1 [org_name]"


def test_plain_text_is_unchanged():
    reply = PlaceholderFiller(profile={"org_name": "1"}, rules=[Rule("Daee", "Muneer")]).fill("Islam means submission to God.")
    assert reply.text == reply.raw and not reply.changed
    assert reply.replacements == () and reply.placeholders == () and reply.missing == ()


# ---- replacement rules ---------------------------------------------------

PERSONA = [
    Rule("Daee, an AI Islamic Advisor", "Muneer, an AI Islamic Advisor"),
    Rule("an AI Islamic Advisor", "Muneer, an AI Islamic Advisor"),
    Rule("Daee", "Muneer"),
]


def test_first_listed_rule_wins_and_its_output_is_not_rewritten():
    reply = PlaceholderFiller(rules=PERSONA).fill("I'm Daee, an AI Islamic Advisor.")
    assert reply.text == "I'm Muneer, an AI Islamic Advisor."
    assert reply.replacements == (("Daee, an AI Islamic Advisor", "Muneer, an AI Islamic Advisor", 1),)


def test_leaves_text_that_already_reads_like_the_replacement():
    filler = PlaceholderFiller(rules=PERSONA)
    assert filler.fill("I'm Muneer, an AI Islamic Advisor.").text == "I'm Muneer, an AI Islamic Advisor."
    once = filler.fill("I am an AI Islamic Advisor.").text
    assert once == "I am Muneer, an AI Islamic Advisor."
    assert filler.fill(once).text == once  # filling twice changes nothing


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Daee's answer", "Muneer's answer"),
        ("Daees", "Daees"),  # part of a longer word
        ("저는 Daee입니다.", "저는 Muneer입니다."),  # Korean text around the name
        ("Daee，我是一名AI伊斯兰顾问。", "Muneer，我是一名AI伊斯兰顾问。"),
    ],
)
def test_whole_word_matching_by_default(raw, expected):
    assert PlaceholderFiller(rules=[Rule("Daee", "Muneer")]).fill(raw).text == expected


def test_whole_word_can_be_turned_off():
    assert PlaceholderFiller(rules=[Rule("Daee", "Muneer", whole_word=False)]).fill("Daees").text == "Muneers"


def test_ignore_case():
    assert PlaceholderFiller(rules=[Rule("Daee", "Muneer", ignore_case=True)]).fill("DAEE").text == "Muneer"
    assert PlaceholderFiller(rules=[Rule("Daee", "Muneer")]).fill("DAEE").text == "DAEE"


def test_an_empty_replacement_deletes_text():
    rule = Rule(", developed by Example Labs", "")
    assert PlaceholderFiller(rules=[rule]).fill("I'm Daee, developed by Example Labs.").text == "I'm Daee."


def test_counts_each_rule():
    reply = PlaceholderFiller(rules=[Rule("Daee", "Muneer")]).fill("Daee here. Yes, Daee.")
    assert reply.replacements == (("Daee", "Muneer", 2),)


def test_rules_run_before_placeholders_so_profile_values_are_never_rewritten():
    filler = PlaceholderFiller(
        profile={"org_email": "daee@example.org"},
        rules=[Rule("Daee", "Muneer", ignore_case=True)],
    )
    assert filler.fill("Ask Daee or write to [org_email]").text == "Ask Muneer or write to daee@example.org"


def test_a_rule_can_turn_learned_text_into_a_placeholder():
    filler = PlaceholderFiller(
        profile={"org_email": "help@example.org"},
        rules=[Rule("support@old-example.org", "[org_email]", ignore_case=True)],
    )
    reply = filler.fill("Email support@Old-Example.Org")
    assert reply.text == "Email help@example.org"
    assert reply.placeholders == (("org_email", 1),)


def test_a_placeholder_and_its_value_read_alike_so_the_name_is_never_doubled():
    # Rules that write the name literally, and a model that writes the placeholder.
    literal = PlaceholderFiller(profile={"ai_name": "Muneer"}, rules=PERSONA)
    reply = literal.fill("I'm [ai_name], an AI Islamic Advisor.")
    assert reply.text == "I'm Muneer, an AI Islamic Advisor." and reply.replacements == ()
    # Rules that write the placeholder, for any name the organisation chooses.
    named = PlaceholderFiller(profile={"ai_name": "Rashid"}, rules=[Rule("an AI Islamic Advisor", "[ai_name], an AI Islamic Advisor")])
    assert named.fill("I am an AI Islamic Advisor.").text == "I am Rashid, an AI Islamic Advisor."
    assert named.fill("I'm [ai_name], an AI Islamic Advisor.").text == "I'm Rashid, an AI Islamic Advisor."
    assert named.fill("I am Rashid, an AI Islamic Advisor.").text == "I am Rashid, an AI Islamic Advisor."  # filled twice


def test_two_placeholders_with_the_same_value_both_read_like_it():
    # An organisation that names the assistant after itself.
    filler = PlaceholderFiller(
        profile={"ai_name": "Hidayah", "org_name": "Hidayah"},
        rules=[Rule("an AI Islamic Advisor", "Hidayah, an AI Islamic Advisor")],
    )
    for raw in ("I'm [ai_name], an AI Islamic Advisor.", "I'm [org_name], an AI Islamic Advisor."):
        reply = filler.fill(raw)
        assert reply.text == "I'm Hidayah, an AI Islamic Advisor." and reply.replacements == ()
    assert filler.fill("I am an AI Islamic Advisor.").text == "I am Hidayah, an AI Islamic Advisor."


def test_rules_never_touch_placeholders():
    filler = PlaceholderFiller(profile={"org_name": "Center"}, rules=[Rule("org", "X", whole_word=False)])
    assert filler.fill("[org_name] and org").text == "Center and X"


# ---- the default rules shipped in .env.example ---------------------------


@pytest.fixture(scope="module")
def example():
    return PlaceholderFiller.from_env(dotenv_values(ENV_EXAMPLE))


@pytest.mark.parametrize(
    "raw, expected",
    [
        # English (the persona sentences found in the training data)
        ("I am an AI Islamic Advisor.", "I am Muneer, an AI Islamic Advisor."),
        ("I’m an AI Islamic Advisor.", "I’m Muneer, an AI Islamic Advisor."),
        ("Daee, I’m an AI Islamic Advisor.", "Muneer, I’m an AI Islamic Advisor."),
        ("I'm Daee, an AI Islamic Advisor.", "I'm Muneer, an AI Islamic Advisor."),
        ("I'm Muneer, an AI Islamic Advisor.", "I'm Muneer, an AI Islamic Advisor."),
        ("I'm [ai_name], an AI Islamic Advisor.", "I'm Muneer, an AI Islamic Advisor."),  # the new model's own
        # The other five languages of the current model
        ("Je suis Daee, un conseiller islamique IA.", "Je suis Muneer, un conseiller islamique IA."),
        ("Daee, je suis un conseiller islamique IA.", "Muneer, je suis un conseiller islamique IA."),
        ("Soy Daee, un Asesor Islámico de IA.", "Soy Muneer, un Asesor Islámico de IA."),
        ("Sou Daee, um Conselheiro Islâmico de IA.", "Sou Muneer, um Conselheiro Islâmico de IA."),
        ("Mimi ni Daee, Mshauri wa Kiislamu wa AI.", "Mimi ni Muneer, Mshauri wa Kiislamu wa AI."),
        ("Ako si Daee, isang AI Islamic Advisor.", "Ako si Muneer, isang AI Islamic Advisor."),
        ("저는 Daee입니다.", "저는 Muneer입니다."),
    ],
)
def test_example_persona_rules(example, raw, expected):
    assert example.fill(raw).text == expected


def test_example_profile_is_bathels(example):
    # The mock's placeholders are checked against it in mock_muneer/tests.
    assert example.profile == {"ai_name": "Muneer", "org_name": "Bathel", "org_email": "info@bathel.sa"}


# ---- settings ------------------------------------------------------------


def test_reads_every_setting():
    filler = PlaceholderFiller.from_env({
        "PLACEHOLDERS": '{"org_name": "Center", "org_phone": 12345}',
        "PLACEHOLDER_OPEN": "<<",
        "PLACEHOLDER_CLOSE": ">>",
        "REPLACEMENT_RULES": '[{"find": "Daee", "replace": "Muneer", "ignore_case": true, "whole_word": false}]',
    })
    assert filler.profile == {"org_name": "Center", "org_phone": "12345"}
    assert filler.rules == (Rule("Daee", "Muneer", ignore_case=True, whole_word=False),)
    assert filler.fill("<<org_name>> daee").text == "Center Muneer"


def test_empty_settings_mean_no_changes():
    filler = PlaceholderFiller.from_env({})
    assert filler.profile == {} and filler.rules == ()
    assert filler.fill("[org_name] Daee").text == " Daee"  # unknown placeholder removed


@pytest.mark.parametrize(
    "env, message",
    [
        ({"PLACEHOLDERS": "{not json"}, "PLACEHOLDERS is not valid JSON"),
        ({"PLACEHOLDERS": "[]"}, "PLACEHOLDERS must be a JSON object"),
        ({"PLACEHOLDERS": '{"ORG NAME": "x"}'}, "'ORG NAME'"),
        ({"PLACEHOLDERS": '{"1ST": "x"}'}, "'1ST'"),
        ({"PLACEHOLDERS": '{"org_name": null}'}, "org_name must be text"),
        ({"PLACEHOLDERS": '{"org_name": true}'}, "org_name must be text"),
        ({"PLACEHOLDERS": '{"org": "x"}'}, "at least one underscore"),
        ({"REPLACEMENT_RULES": '{"find": "a"}'}, "must be a JSON list"),
        ({"REPLACEMENT_RULES": '["Daee"]'}, "rule 1 must be an object"),
        ({"REPLACEMENT_RULES": '[{"find": "", "replace": "x"}]'}, "rule 1 needs a non-empty 'find'"),
        ({"REPLACEMENT_RULES": '[{"find": "a", "replace": "b"}, {"find": "c"}]'}, "rule 2 needs a 'replace'"),
        ({"REPLACEMENT_RULES": '[{"find": "a", "replace": "b", "repalce": "c"}]'}, "unknown key(s): repalce"),
        ({"REPLACEMENT_RULES": '[{"find": "a", "replace": "b", "ignore_case": "yes"}]'}, "'ignore_case' must be true or false"),
    ],
)
def test_rejects_bad_settings(env, message):
    with pytest.raises(ConfigError) as err:
        PlaceholderFiller.from_env(env)
    assert message in str(err.value)


def test_markers_must_not_be_empty():
    with pytest.raises(ConfigError, match="must not be empty"):
        PlaceholderFiller(open_marker="")
