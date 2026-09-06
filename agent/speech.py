"""Saying things back, because over a phone line the expensive mistakes are all mishearings.

An address, an email and a dollar figure are the three answers nobody notices are wrong until a quote
lands at the wrong house. Each gets read back in a form that is hard to mishear twice: an address as a
whole, an email letter by letter, money and dates in words.

Driven by the field descriptor the tools API hands back rather than by a form definition of its own, so
the agent never holds a second, drifting copy of the questionnaire.
"""

from __future__ import annotations

from typing import Any

# Letters that sound alike on a narrowband line, which is why an email is spelled rather than said.
_LETTER_NAMES = {
    "a": "A", "b": "B for bravo", "c": "C for charlie", "d": "D for delta", "e": "E",
    "f": "F for foxtrot", "g": "G", "h": "H", "i": "I", "j": "J for juliet", "k": "K",
    "l": "L for lima", "m": "M for mike", "n": "N for november", "o": "O", "p": "P for papa",
    "q": "Q", "r": "R", "s": "S for sierra", "t": "T", "u": "U", "v": "V for victor",
    "w": "W", "x": "X", "y": "Y", "z": "Z for zulu",
}
_SYMBOL_NAMES = {".": "dot", "@": "at", "_": "underscore", "-": "dash", "+": "plus"}


def spell_out(value: str) -> str:
    """One character at a time, with a distinguishing word where the letter alone is ambiguous."""
    parts: list[str] = []
    for character in str(value):
        lowered = character.lower()
        if lowered in _LETTER_NAMES:
            parts.append(_LETTER_NAMES[lowered])
        elif character.isdigit():
            parts.append(character)
        elif character in _SYMBOL_NAMES:
            parts.append(_SYMBOL_NAMES[character])
        elif character == " ":
            continue
        else:
            parts.append(character)
    return ", ".join(parts)


def _money(value: Any) -> str:
    try:
        return f"{int(value):,} dollars"
    except (TypeError, ValueError):
        return f"{value} dollars"


def _phone(value: Any) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) == 10:
        return f"{' '.join(digits[:3])}, {' '.join(digits[3:6])}, {' '.join(digits[6:])}"
    return " ".join(digits)


def readback(answer_type: str, value: Any) -> str:
    """The sentence to say back, or empty where this answer does not warrant one."""
    if value is None or value == "":
        return ""
    if answer_type == "email":
        return f"Let me read that back letter by letter. {spell_out(value)}. Is that right?"
    if answer_type == "address":
        return f"So that is {value}. Have I got that right?"
    if answer_type == "phone":
        return f"That is {_phone(value)}. Correct?"
    if answer_type == "currency":
        return f"{_money(value)}, is that right?"
    if answer_type == "date":
        return f"I have {value}. Correct?"
    return f"I have {value}. Is that right?"


# Said when nothing came back, or came back as noise. Varied because hearing the same sentence three
# times is what makes a caller hang up.
MISHEARD_LINES = (
    "Sorry, I missed that. Could you say it again?",
    "I still did not catch it. One more time?",
    "I am struggling to hear you. Let me have a colleague call you back instead.",
)
SILENCE_LINES = (
    "Are you still there?",
    "I cannot hear anything, so I will call back another time. Thank you.",
)
