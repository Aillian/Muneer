"""Placeholder filling: turns Muneer's raw reply into the filled reply.

Two kinds of change are made, in this order:

1. Replacement rules rewrite fixed text the model learned, such as its old
   persona name. The text is read once from left to right. Where two rules
   match at the same place, the one listed first wins. Text a rule has
   written is never rewritten by another rule. A rule also leaves alone text
   that already reads like its own replacement, a placeholder counting as
   its value, so filling a reply twice changes nothing and "[ai_name], an AI
   Islamic Advisor" doesn't get a second name.
2. Placeholders such as [org_email] are filled from the organisation
   profile. A placeholder with no value is removed and reported.

Rules run first, so a rule may turn learned text into a placeholder (an old
support email into [org_email], say). The organisation's own values are
never rewritten by a rule.

Settings (environment variables):
    PLACEHOLDERS       JSON object: placeholder name -> value
    PLACEHOLDER_OPEN   opening marker, default "["
    PLACEHOLDER_CLOSE  closing marker, default "]"
    REPLACEMENT_RULES  JSON list of rules, each
                       {"find": ..., "replace": ..., "ignore_case": false, "whole_word": true}
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipeline.config import ConfigError, env_json, env_text

log = logging.getLogger(__name__)

# Placeholder names are lower_snake_case with at least one underscore, such as
# org_name. So citations such as [Muslim] or [1], and the words translators add
# inside quoted verses, such as [so] or [it], are never mistaken for placeholders.
PLACEHOLDER_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
DEFAULT_OPEN, DEFAULT_CLOSE = "[", "]"
# Whole-word matching looks only at Latin letters and digits, so a name written
# against Chinese or Korean text ("Daee입니다") still counts as a whole word.
_LATIN_WORD_CHAR = re.compile(r"[A-Za-z0-9_]")
_RULE_KEYS = {"find", "replace", "ignore_case", "whole_word"}


@dataclass(frozen=True)
class ReplacementRule:
    find: str
    replace: str
    ignore_case: bool = False
    whole_word: bool = True

    def __post_init__(self) -> None:
        if not self.find:
            raise ConfigError("A replacement rule needs a non-empty 'find' text.")

    def pattern(self) -> str:
        body = re.escape(self.find)
        if self.whole_word:
            if _LATIN_WORD_CHAR.fullmatch(self.find[0]):
                body = r"(?<![A-Za-z0-9_])" + body
            if _LATIN_WORD_CHAR.fullmatch(self.find[-1]):
                body += r"(?![A-Za-z0-9_])"
        return ("(?i:" if self.ignore_case else "(?:") + body + ")"


@dataclass(frozen=True)
class FilledReply:
    """A reply after placeholder filling, with a record of what changed."""

    raw: str
    text: str
    replacements: tuple[tuple[str, str, int], ...] = ()  # (find, replace, times applied)
    placeholders: tuple[tuple[str, int], ...] = ()  # (name, times filled)
    missing: tuple[str, ...] = ()  # placeholders with no value; removed from the text

    @property
    def changed(self) -> bool:
        return self.text != self.raw


class PlaceholderFiller:
    def __init__(
        self,
        profile: Mapping[str, object] | None = None,
        rules: Sequence[ReplacementRule] = (),
        open_marker: str = DEFAULT_OPEN,
        close_marker: str = DEFAULT_CLOSE,
    ) -> None:
        if not open_marker or not close_marker:
            raise ConfigError("PLACEHOLDER_OPEN and PLACEHOLDER_CLOSE must not be empty.")
        self.profile = _normalise_profile(profile or {})
        self.rules = tuple(rules)
        self._marker_open, self._marker_close = re.escape(open_marker), re.escape(close_marker)
        # Each profile value, escaped as it appears in an escaped replacement text -> the names it fills. Two
        # placeholders may share a value, as when an organisation names the assistant after itself.
        self._value_names: dict[str, list[str]] = {}
        for name, value in self.profile.items():
            if value:
                self._value_names.setdefault(re.escape(value), []).append(name)
        self._values = (
            re.compile("|".join(re.escape(v) for v in sorted(self._value_names, key=len, reverse=True)))
            if self._value_names
            else None
        )
        self._placeholder = re.compile(
            re.escape(open_marker) + r"\s*(" + PLACEHOLDER_NAME.pattern + r")\s*" + re.escape(close_marker)
        )
        self._rules = (
            re.compile("|".join(f"(?P<r{i}>{rule.pattern()})" for i, rule in enumerate(self.rules)))
            if self.rules
            else None
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PlaceholderFiller:
        profile = env_json(env, "PLACEHOLDERS", {})
        if not isinstance(profile, dict):
            raise ConfigError("PLACEHOLDERS must be a JSON object of placeholder names and values.")
        return cls(
            profile=profile,
            rules=_parse_rules(env_json(env, "REPLACEMENT_RULES", [])),
            open_marker=env_text(env, "PLACEHOLDER_OPEN", DEFAULT_OPEN),
            close_marker=env_text(env, "PLACEHOLDER_CLOSE", DEFAULT_CLOSE),
        )

    def fill(self, raw: str) -> FilledReply:
        text, replacements = self._apply_rules(raw)
        text, placeholders, missing = self._fill_placeholders(text)
        return FilledReply(raw, text, replacements, placeholders, missing)

    def _apply_rules(self, raw: str) -> tuple[str, tuple[tuple[str, str, int], ...]]:
        if self._rules is None:
            return raw, ()
        placeholder_spans = [m.span() for m in self._placeholder.finditer(raw)]
        own_text = [self._reads_like(rule.replace, raw, rule.ignore_case) for rule in self.rules]
        applied: Counter[int] = Counter()

        def swap(match: re.Match[str]) -> str:
            i = int(match.lastgroup[1:])
            start, end = match.span()
            touches_placeholder = any(s < end and start < e for s, e in placeholder_spans)
            already_replaced = any(s <= start and end <= e for s, e in own_text[i])
            if touches_placeholder or already_replaced:
                return match.group(0)
            applied[i] += 1
            return self.rules[i].replace

        text = self._rules.sub(swap, raw)
        return text, tuple((self.rules[i].find, self.rules[i].replace, applied[i]) for i in sorted(applied))

    def _reads_like(self, replacement: str, raw: str, ignore_case: bool) -> list[tuple[int, int]]:
        """Where the raw text already reads like a rule's replacement, a placeholder and its value counting as the
        same text. So with ai_name Muneer, "[ai_name], an AI Islamic Advisor" and "Muneer, an AI Islamic Advisor"
        each read like the other, and a rule never names the assistant twice.
        """
        if not replacement:
            return []
        parts, last = [], 0
        for match in self._placeholder.finditer(replacement):
            parts += [self._with_placeholders(replacement[last:match.start()]), self._either(match.group(1))]
            last = match.end()
        pattern = "".join([*parts, self._with_placeholders(replacement[last:])])
        return [m.span() for m in re.finditer(pattern, raw, re.IGNORECASE if ignore_case else 0)]

    def _either(self, name: str) -> str:
        """A pattern for a placeholder, or the value that fills it, or another placeholder with the same value."""
        value = self.profile.get(name)
        return self._any_of(re.escape(value)) if value else self._placeholder_pattern(name)

    def _any_of(self, escaped_value: str) -> str:
        """A pattern for a profile value, or any placeholder it fills."""
        placeholders = [self._placeholder_pattern(name) for name in self._value_names[escaped_value]]
        return "(?:" + "|".join([escaped_value, *placeholders]) + ")"

    def _placeholder_pattern(self, name: str) -> str:
        return f"{self._marker_open}\\s*{name}\\s*{self._marker_close}"

    def _with_placeholders(self, text: str) -> str:
        """A pattern for literal text, in which each profile value may also appear as a placeholder it fills."""
        if self._values is None:
            return re.escape(text)
        # One pass, longest value first, so a value is never looked for inside an alternative already added.
        return self._values.sub(lambda m: self._any_of(m.group(0)), re.escape(text))

    def _fill_placeholders(self, text: str) -> tuple[str, tuple[tuple[str, int], ...], tuple[str, ...]]:
        filled: Counter[str] = Counter()
        missing: list[str] = []

        def put(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self.profile:
                filled[name] += 1
                return self.profile[name]
            if name not in missing:
                missing.append(name)
            return ""

        text = self._placeholder.sub(put, text)
        if missing:
            log.warning("No value for placeholder(s) %s; removed from the reply. Add them to PLACEHOLDERS.", ", ".join(missing))
        return text, tuple(filled.items()), tuple(missing)



def _normalise_profile(profile: Mapping[str, object]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in profile.items():
        name = str(key).strip().lower()
        if not PLACEHOLDER_NAME.fullmatch(name):
            raise ConfigError(
                f"PLACEHOLDERS name {key!r} must be letters, digits and underscores, with at least one underscore "
                "and starting with a letter, such as org_name."
            )
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ConfigError(f"PLACEHOLDERS value for {name} must be text, not {type(value).__name__}.")
        values[name] = str(value)
    return values


def _parse_rules(data: object) -> list[ReplacementRule]:
    if not isinstance(data, list):
        raise ConfigError("REPLACEMENT_RULES must be a JSON list of rules.")
    rules = []
    for number, item in enumerate(data, 1):
        where = f"REPLACEMENT_RULES rule {number}"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be an object with 'find' and 'replace'.")
        unknown = set(item) - _RULE_KEYS
        if unknown:
            raise ConfigError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}.")
        find, replace = item.get("find"), item.get("replace")
        if not isinstance(find, str) or not find:
            raise ConfigError(f"{where} needs a non-empty 'find' text.")
        if not isinstance(replace, str):
            raise ConfigError(f"{where} needs a 'replace' text (use \"\" to delete the text).")
        flags = {}
        for key in ("ignore_case", "whole_word"):
            if key in item:
                if not isinstance(item[key], bool):
                    raise ConfigError(f"{where}: '{key}' must be true or false.")
                flags[key] = item[key]
        rules.append(ReplacementRule(find, replace, **flags))
    return rules
