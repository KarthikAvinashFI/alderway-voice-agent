"""The invariants. The prompt states them in words; this is what actually enforces them.

Six things must be true of every call this agent makes, and none of them can be left to a model being
persuaded not to break them:

1. Nothing is collected before consent, beyond establishing who answered and getting that consent.
2. No price, no advice, no statement about what is covered. Ever, however it is asked.
3. A request not to be called again is honoured on the spot and stops the intake dead.
4. A refusal is recorded as a refusal, never skipped and never nulled.
5. A question already answered is never asked again.
6. Nothing is said about eligibility that the tools API did not return.

Each has a guard here and a test in tests/test_state.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .objections import ObjectionTracker


class GuardError(ValueError):
    """Raised when an action is not safe to take yet."""


# The only things askable before consent: who answered, whether they own the home, the recording
# disclosure, and whether they are willing to continue. Everything else is collection.
CONSENT_SECTION_FIELDS = frozenset(
    {
        "reached_right_party",
        "homeowner_confirmed",
        "recording_disclosure_ack",
        "consent_to_continue",
        "contact_name",
        "preferred_language",
        "callback_number_confirm",
    }
)

# Statuses the API reports back for an answer.
ANSWERED = "answered"
REFUSED = "refused"
UNKNOWN = "unknown"
SETTLED = frozenset({ANSWERED, REFUSED, UNKNOWN})


@dataclass
class IntakeState:
    """What has been established on this call, and therefore what may happen next."""

    lead_id: str
    phone: str
    session_id: str
    call_id: str = ""
    lead_name: str = ""

    recording_acknowledged: bool = False
    consent_granted: bool = False
    suppressed: bool = False

    # Mirrors what the API has confirmed it holds. The API is the authority; this is the copy the
    # guards read, refreshed from every response.
    settled: dict[str, str] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    current_field: str = ""

    # Set only by a verdict from the API. Until then the agent has nothing it may say about whether
    # this can be quoted.
    verdict: dict[str, Any] | None = None

    tracker: ObjectionTracker = field(default_factory=ObjectionTracker)
    misheard_streak: int = 0
    silence_prompts: int = 0
    disposition: str = ""
    ended_reason: str = ""

    # ------------------------------------------------------------ consent

    def note_consent(self, field_id: str, value: Any) -> None:
        """Track the two answers that open collection, as the API confirms them."""
        if field_id == "recording_disclosure_ack":
            self.recording_acknowledged = bool(value)
        if field_id == "consent_to_continue":
            self.consent_granted = bool(value)

    @property
    def collection_open(self) -> bool:
        return self.recording_acknowledged and self.consent_granted

    def ensure_collection_allowed(self, field_id: str) -> None:
        if field_id in CONSENT_SECTION_FIELDS:
            return
        if not self.recording_acknowledged:
            raise GuardError(
                "The recording disclosure has not been acknowledged yet, so nothing else may be collected."
            )
        if not self.consent_granted:
            raise GuardError(
                "The caller has not agreed to continue, so nothing else may be collected."
            )

    # ------------------------------------------------------------ suppression

    def mark_suppressed(self, reason: str) -> None:
        """A removal request ends the call. Nothing may be asked afterwards."""
        self.suppressed = True
        self.disposition = "removed"
        self.ended_reason = reason

    def ensure_not_suppressed(self) -> None:
        if self.suppressed:
            raise GuardError("This caller asked not to be contacted. Nothing further may be asked.")

    # ------------------------------------------------------------ questions

    def ensure_askable(self, field_id: str) -> None:
        """A question already settled is never asked again, whatever the model thinks."""
        self.ensure_not_suppressed()
        status = self.settled.get(field_id)
        if status in SETTLED:
            raise GuardError(
                f"{field_id} is already recorded as {status}. Move on to the next question."
            )
        self.ensure_collection_allowed(field_id)

    def note_asked(self, field_id: str) -> None:
        self.current_field = field_id
        if field_id and (not self.asked or self.asked[-1] != field_id):
            self.asked.append(field_id)

    def note_settled(self, field_id: str, status: str, value: Any = None) -> None:
        if status not in SETTLED:
            raise GuardError(f"unknown answer status {status!r}")
        self.settled[field_id] = status
        if status == ANSWERED:
            self.note_consent(field_id, value)
        self.misheard_streak = 0
        self.silence_prompts = 0

    def ensure_refusal_recorded(self, field_id: str) -> None:
        """A field the caller declined must be on the record as declined, not merely unasked."""
        if self.settled.get(field_id) != REFUSED:
            raise GuardError(f"{field_id} was not recorded as a refusal.")

    def outstanding(self) -> list[str]:
        return [one for one in self.asked if one not in self.settled]

    # ------------------------------------------------------------ eligibility

    def set_verdict(self, verdict: dict[str, Any]) -> None:
        decision = str(verdict.get("decision") or "")
        if decision not in {"eligible", "needs_review", "disqualified"}:
            raise GuardError(f"the eligibility service returned no usable decision: {decision!r}")
        self.verdict = dict(verdict)

    def ensure_verdict(self) -> dict[str, Any]:
        if self.verdict is None:
            raise GuardError(
                "Eligibility has not been checked. Call check_eligibility before saying anything "
                "about whether this can be quoted."
            )
        return self.verdict

    def may_say_reason(self, text: str) -> bool:
        """Whether a sentence about eligibility is one the API actually returned.

        The model is allowed to speak the reason it was given and nothing else, because an invented
        decline reason is a statement about underwriting that nobody at this company made.
        """
        verdict = self.verdict
        if verdict is None:
            return False
        reason = str(verdict.get("spoken_reason") or "").strip()
        return bool(reason) and reason.lower()[:40] in text.lower()

    def ensure_transferable(self) -> dict[str, Any]:
        verdict = self.ensure_verdict()
        if verdict.get("decision") == "disqualified":
            raise GuardError(
                "This intake cannot be quoted, so there is nothing to transfer. Give the reason and "
                "close the call."
            )
        return verdict

    # ------------------------------------------------------------ pacing

    def note_misheard(self) -> int:
        self.misheard_streak += 1
        return self.misheard_streak

    def note_silence(self) -> int:
        self.silence_prompts += 1
        return self.silence_prompts
