"""Turning what somebody said into the value a field holds.

Everything here is tolerant on the way in and strict on the way out. Speech arrives as "about ten
years I think", "twelve hundred a year", "yeah that's right", and a quote desk needs 10, 1200, True.
Where the text cannot be read, the field stays unanswered and the agent asks again, which is always
better than storing a guess.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from form_spec import AnswerType, Confidence, Field

# Taken from how consent and confirmation are actually given on a call. "go ahead" and "that's fine"
# are the two commonest ways a caller says yes, and neither contains the word.
_AFFIRMATIVE = {
    "yes", "yeah", "yep", "yup", "sure", "correct", "that's right", "thats right", "right",
    "true", "affirmative", "i do", "we do", "i am", "we are", "it is", "of course", "certainly",
    "absolutely", "ok", "okay", "mhm", "uh huh", "that's fine", "thats fine", "fine", "go ahead",
    "go on", "sure thing", "alright", "all right", "no problem", "please do", "you can",
    "i suppose so", "i suppose", "i guess so", "i guess", "why not", "yes please", "indeed",
    "definitely", "sounds good", "that works", "happy to", "carry on", "if you like",
}
_NEGATIVE = {
    "no", "nope", "nah", "negative", "not really", "i don't", "i dont", "we don't", "we dont",
    "false", "never", "none", "no it isn't", "no it isnt", "not at all", "nothing like that",
    "not that i know", "none at all", "i don't think so", "i dont think so", "absolutely not",
    "no thanks", "no thank you", "rather not", "no sir", "no chance", "we never", "not us",
}
# Words that mean the number is approximate. Recorded as low confidence so a licensed agent knows to
# confirm rather than quote off it.
_HEDGES = {
    "about", "around", "roughly", "approximately", "i think", "maybe", "probably", "or so",
    "somewhere", "ish", "guess", "not sure", "close to", "give or take", "-ish",
}
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


class ValueError_(ValueError):
    """Raised only by callers that want an exception; `normalize` returns the message instead."""


def hedged(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(hedge in lowered for hedge in _HEDGES)


def confidence_for(text: str, value: Any) -> Confidence:
    if value is None:
        return Confidence.LOW
    if hedged(text):
        return Confidence.LOW
    if len(text.split()) > 25:
        # A long ramble that happened to contain a parseable number is weaker evidence than a direct
        # answer to the question asked.
        return Confidence.MEDIUM
    return Confidence.HIGH


def _words_to_number(text: str) -> int | None:
    tokens = [token for token in re.split(r"[\s-]+", text.lower()) if token in _NUMBER_WORDS]
    if not tokens:
        return None
    total = 0
    running = 0
    for token in tokens:
        scale = _NUMBER_WORDS[token]
        if scale in (100, 1000):
            running = (running or 1) * scale
            if scale == 1000:
                total += running
                running = 0
        else:
            running += scale
    return total + running


def _first_number(text: str) -> float | None:
    cleaned = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if match:
        number = float(match.group())
        # "12k" and "1.2 million" are said out loud more often than the digits are.
        tail = cleaned[match.end():].lstrip().lower()
        if tail.startswith("k"):
            number *= 1000
        elif tail.startswith(("m", "million")):
            number *= 1_000_000
        return number
    spelled = _words_to_number(text)
    return float(spelled) if spelled is not None else None


def _to_bool(text: str) -> bool | None:
    lowered = text.strip().lower().rstrip(".!?,")
    if lowered in _AFFIRMATIVE:
        return True
    if lowered in _NEGATIVE:
        return False
    # Real answers carry a tail, so match on a leading cue. Longest phrase wins across both sets
    # together rather than negatives first: "no problem" means yes, and checking negatives first reads
    # its first two letters and gets the answer backwards.
    candidates = [(phrase, True) for phrase in _AFFIRMATIVE] + [(phrase, False) for phrase in _NEGATIVE]
    best: tuple[int, bool] | None = None
    for phrase, value in candidates:
        if lowered.startswith(phrase) and (best is None or len(phrase) > best[0]):
            best = (len(phrase), value)
    if best is not None:
        return best[1]
    if "not" in lowered.split() or "n't" in lowered:
        return False
    return None


_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def spoken_digit_string(text: str) -> str:
    """Digits from a string of them said out loud, the way a phone number or a zip arrives.

    "five five five, double one, oh two" is one number, and transcription gives it back as words
    about as often as it gives digits. "double" and "triple" repeat whatever follows them, which is
    how people read digits aloud and is not something a plain word to number pass can do.
    """
    out: list[str] = []
    repeat = 1
    for token in re.split(r"[\s,.-]+", text.lower()):
        if not token:
            continue
        if token in ("double", "twice"):
            repeat = 2
            continue
        if token in ("triple", "treble", "thrice"):
            repeat = 3
            continue
        if token.isdigit():
            out.append(token * repeat if len(token) == 1 else token)
            repeat = 1
            continue
        if token in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[token] * repeat)
            repeat = 1
            continue
        # Anything else resets the run, so "my number is" contributes nothing and does not carry a
        # stale repeat into the digits that follow.
        repeat = 1
    return "".join(out)


def _to_year(text: str) -> int | None:
    match = re.search(r"(1[6-9]\d{2}|20\d{2})", text)
    if match:
        return int(match.group())
    lowered = text.lower()
    # "nineteen ninety eight" and "twenty twenty four" are how a year is said, and summing the words
    # gives 117 rather than 1998. Read it as a century word followed by the rest.
    century = None
    for word, value in (("nineteen", 1900), ("twenty", 2000), ("eighteen", 1800)):
        position = lowered.find(word)
        if position != -1:
            century = (position + len(word), value)
            break
    if century is not None:
        offset, base = century
        remainder = _words_to_number(lowered[offset:])
        if remainder is not None and 0 <= remainder < 100:
            return base + remainder
        if remainder in (None, 0) and base == 2000:
            return base
    digits = spoken_digit_string(text)
    if len(digits) == 4 and 1600 <= int(digits) <= 2100:
        return int(digits)
    # "built in the sixties" and "about eight years ago" both happen.
    spelled = _words_to_number(text)
    if spelled is not None and 1600 <= spelled <= 2100:
        return spelled
    return None


def _to_date(text: str) -> date | None:
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if match:
        year = int(match.group(3))
        year = year + 2000 if year < 100 else year
        try:
            return date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    lowered = text.lower()
    for name, month in _MONTHS.items():
        if name in lowered:
            year_match = re.search(r"(20\d{2}|19\d{2})", lowered)
            day_match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", lowered)
            if year_match:
                day = int(day_match.group(1)) if day_match else 1
                try:
                    return date(int(year_match.group(1)), month, min(day, 28))
                except ValueError:
                    return None
    return None


def _to_enum(
    text: str, choices: tuple[str, ...], synonyms: tuple[tuple[str, str], ...] = ()
) -> str | None:
    lowered = text.lower()
    for choice in choices:
        if choice.lower() == lowered.strip():
            return choice
    # Longest phrase first, so "second home" is not read as "home".
    for phrase, choice in sorted(synonyms, key=lambda pair: len(pair[0]), reverse=True):
        if phrase in lowered and choice in choices:
            return choice
    # Match on the words of a choice, so "forced air gas" reaches "gas_forced_air".
    best: tuple[int, str] | None = None
    for choice in choices:
        parts = [part for part in choice.split("_") if part]
        hits = sum(1 for part in parts if part in lowered)
        if hits and (best is None or hits > best[0]):
            best = (hits, choice)
    if best:
        return best[1]
    numeric = _first_number(text)
    if numeric is not None:
        as_text = str(int(numeric))
        if as_text in choices:
            return as_text
    return None


def normalize(field: Field, text: str) -> tuple[Any, str | None]:
    """The value this text carries for this field, plus a problem to reprompt with.

    Returns `(None, reason)` when the text cannot be read as the field's type. The reason is written
    for the agent to say back, not for a log.
    """
    raw = (text or "").strip()
    if not raw:
        return None, "nothing was said"

    if field.type is AnswerType.BOOL:
        value = _to_bool(raw)
        return (value, None) if value is not None else (None, "a yes or no is needed here")

    if field.type in (AnswerType.INT, AnswerType.CURRENCY):
        number = _first_number(raw)
        if number is None:
            return None, "a number is needed here"
        value = int(round(number))
        if field.minimum is not None and value < field.minimum:
            return None, f"that is below the lowest value this accepts, {int(field.minimum)}"
        if field.maximum is not None and value > field.maximum:
            return None, f"that is above the highest value this accepts, {int(field.maximum)}"
        return value, None

    if field.type is AnswerType.YEAR:
        year = _to_year(raw)
        if year is None:
            return None, "a year is needed here"
        if field.minimum is not None and year < field.minimum:
            return None, "that year is too far back to be right"
        if field.maximum is not None and year > field.maximum:
            return None, "that year is in the future"
        return year, None

    if field.type is AnswerType.ENUM:
        value = _to_enum(raw, field.choices, field.synonyms)
        if value is None:
            return None, "that is not one of the options for this question"
        return value, None

    if field.type is AnswerType.DATE:
        value = _to_date(raw)
        return (value, None) if value else (None, "a date is needed here")

    if field.type is AnswerType.EMAIL:
        match = re.search(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}", raw.replace(" at ", "@").replace(" dot ", "."))
        return (match.group(), None) if match else (None, "that does not read as an email address")

    if field.type is AnswerType.PHONE:
        digits = re.sub(r"\D", "", raw)
        if not digits:
            digits = spoken_digit_string(raw)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return (digits, None) if len(digits) == 10 else (None, "a ten digit number is needed here")

    if field.type is AnswerType.ADDRESS:
        # Deliberately not parsed into parts. A spoken address is verified against a lookup later,
        # and splitting it here would invent structure the caller did not give.
        if len(raw) < 8 or not re.search(r"\d", raw):
            return None, "that does not look like a full street address"
        return raw, None

    return raw, None
