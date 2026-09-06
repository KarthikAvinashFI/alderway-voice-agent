"""Telling a machine from a person, quickly, on the first thing that is said.

An outbound campaign reaches a mailbox more often than it reaches anybody, and a form worked into a
recording is the worst outcome available: it collects nothing, it costs a full call, and the person
gets ninety seconds of an automated voice asking about their roof.
"""

from __future__ import annotations

from dataclasses import dataclass

_MACHINE_PHRASES = (
    "leave a message",
    "leave your message",
    "after the tone",
    "after the beep",
    "at the tone",
    "you have reached",
    "you've reached",
    "is not available",
    "isn't available",
    "unable to take your call",
    "cannot take your call",
    "can't take your call",
    "not able to take your call",
    "please record",
    "record your message",
    "voice mail",
    "voicemail",
    "voice messaging system",
    "mailbox is full",
    "the person you are calling",
    "the subscriber",
    "press one",
    "at the sound of the tone",
    "start recording",
)
# A greeting rarely asks anything. A person almost always does, within a word or two of picking up.
_HUMAN_CUES = ("hello", "hi", "who is this", "speaking", "yes", "yeah", "who's calling", "can i help")


@dataclass(frozen=True)
class VoicemailVerdict:
    is_machine: bool
    confidence: float
    matched: str = ""

    @property
    def certain(self) -> bool:
        return self.is_machine and self.confidence >= 0.8


def classify_opening(transcript: str, *, words_spoken: int | None = None) -> VoicemailVerdict:
    """Whether the first thing heard came from a mailbox.

    Phrase matching first, because a greeting says one of a small number of things. Length is the
    fallback signal: a person answering the phone does not deliver twenty unbroken words.
    """
    text = " ".join(transcript.lower().split())
    if not text:
        return VoicemailVerdict(False, 0.0)

    for phrase in _MACHINE_PHRASES:
        if phrase in text:
            return VoicemailVerdict(True, 0.95, phrase)

    count = words_spoken if words_spoken is not None else len(text.split())
    starts_human = any(text.startswith(cue) for cue in _HUMAN_CUES)
    if count >= 20 and not starts_human:
        return VoicemailVerdict(True, 0.6, "long unbroken opening")
    if count >= 12 and not starts_human and "?" not in transcript:
        return VoicemailVerdict(True, 0.45, "long opening with no question")
    return VoicemailVerdict(False, 0.0)


_WRONG_NUMBER_PHRASES = (
    "wrong number",
    "no one here by that name",
    "nobody here by that name",
    "don't know who that is",
    "do not know who that is",
    "you have the wrong",
    "there's no one called",
    "never heard of",
    "this is a business",
)


def wrong_number(transcript: str) -> bool:
    text = " ".join(transcript.lower().split())
    return any(phrase in text for phrase in _WRONG_NUMBER_PHRASES)
