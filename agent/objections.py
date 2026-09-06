"""What to say when the caller pushes back, and the line an unlicensed assistant does not cross.

Every entry is bounded. An outbound call that argues twice with the same objection is a complaint
waiting to happen, and the exits here exist so the agent runs out of persistence before the caller
runs out of patience.

Two of these are honoured on first mention and are not negotiated at all: a request to be taken off
the list, and somebody telling us they are driving.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum


class ObjectionExit(str, Enum):
    CONTINUE = "continue"
    SCHEDULE_CALLBACK = "schedule_callback"
    END_POLITE = "end_polite"
    TRANSFER_HUMAN = "transfer_human"
    HONOUR_REMOVAL = "honour_removal"


@dataclass(frozen=True)
class Objection:
    code: str
    # Cues are a cheap first pass. The model classifies too, and either path reaches the same entry.
    cues: tuple[str, ...]
    responses: tuple[str, ...]
    on_exhausted: ObjectionExit
    # Honoured the moment it is raised, without a rebuttal.
    immediate: ObjectionExit | None = None

    @property
    def max_attempts(self) -> int:
        return len(self.responses)


PLAYBOOK: tuple[Objection, ...] = (
    Objection(
        "remove_me",
        (
            "take me off", "remove me", "do not call", "don't call", "stop calling",
            "unsubscribe", "opt out", "never call",
        ),
        (),
        ObjectionExit.HONOUR_REMOVAL,
        immediate=ObjectionExit.HONOUR_REMOVAL,
    ),
    Objection(
        "im_driving",
        ("i'm driving", "im driving", "on the road", "behind the wheel", "in the car"),
        ("Of course, I will not keep you while you are driving. I will call you back another time.",),
        ObjectionExit.SCHEDULE_CALLBACK,
        immediate=ObjectionExit.SCHEDULE_CALLBACK,
    ),
    Objection(
        "wants_human",
        (
            "speak to a person", "real person", "human", "speak to someone", "talk to an agent",
            "is this a robot", "are you a bot", "are you real",
        ),
        (
            "I am an automated assistant, and I can put you straight through to a licensed agent. "
            "Let me do that now.",
        ),
        ObjectionExit.TRANSFER_HUMAN,
        immediate=ObjectionExit.TRANSFER_HUMAN,
    ),
    Objection(
        "not_interested",
        ("not interested", "no thanks", "don't want", "dont want", "not looking"),
        (
            "Understood. Most people are not looking until they see the number, and this takes about "
            "four minutes. Would you rather I just check whether there is a saving worth having?",
            "That is fair. I will leave it there. Thank you for your time.",
        ),
        ObjectionExit.END_POLITE,
    ),
    Objection(
        "how_did_you_get_my_number",
        (
            "how did you get", "where did you get my", "who gave you", "why are you calling me",
            "how do you have my",
        ),
        (
            "Your mortgage lender passes details to us so the insurance can be shopped before "
            "closing. You can ask me to remove you at any point and I will do it on this call.",
            "I understand the concern. I will take you off the list now rather than keep you.",
        ),
        ObjectionExit.HONOUR_REMOVAL,
    ),
    Objection(
        "already_insured",
        ("already have", "already insured", "i'm covered", "im covered", "got insurance"),
        (
            "That is usually the case, and what we do is compare against what you already hold. "
            "If nobody beats it, I will tell you that.",
            "No problem at all. Would it help if I called nearer your renewal instead?",
        ),
        ObjectionExit.SCHEDULE_CALLBACK,
    ),
    Objection(
        "is_this_a_scam",
        ("is this a scam", "sounds like a scam", "fraud", "phishing", "i don't trust", "scam"),
        (
            "A fair question. I will not ask for a bank detail, a card number or a social security "
            "number on this call. Everything I need is about the property itself.",
            "I understand. You can call the number on your lender's website and ask for the "
            "insurance desk instead. I will close this out.",
        ),
        ObjectionExit.END_POLITE,
    ),
    Objection(
        "call_me_later",
        ("call me later", "bad time", "not a good time", "busy right now", "call back"),
        ("No problem. When would suit you better?",),
        ObjectionExit.SCHEDULE_CALLBACK,
    ),
    Objection(
        "too_many_questions",
        ("too many questions", "how long is this", "how much longer", "this is taking"),
        (
            "You are right that it is a lot. Carriers set the questions, and we are about halfway. "
            "Shall I carry on?",
            "Let me stop there and have a licensed agent pick up the rest with you.",
        ),
        ObjectionExit.TRANSFER_HUMAN,
    ),
    Objection(
        "hostile",
        ("shut up", "leave me alone", "harassment", "sick of these calls", "stop bothering"),
        ("I am sorry to have bothered you. I will take you off the list now.",),
        ObjectionExit.HONOUR_REMOVAL,
        immediate=ObjectionExit.HONOUR_REMOVAL,
    ),
)

# Beyond this many objections of any kind the call is not going anywhere, and pressing on is what
# turns a declined quote into a complaint.
TOTAL_OBJECTION_BUDGET = 5


@dataclass(frozen=True)
class ObjectionOutcome:
    code: str
    exit: ObjectionExit
    say: str = ""


@dataclass
class ObjectionTracker:
    playbook: tuple[Objection, ...] = PLAYBOOK
    budget: int = TOTAL_OBJECTION_BUDGET
    counts: dict[str, int] = dataclass_field(default_factory=dict)
    raised: int = 0

    def entry(self, code: str) -> Objection | None:
        for one in self.playbook:
            if one.code == code:
                return one
        return None

    def classify(self, text: str) -> str | None:
        """Which objection this turn is, by the earliest cue that matches.

        Ordered by the playbook rather than by cue length, so a removal request is recognised before
        anything softer that happens to share a word with it.
        """
        lowered = f" {text.lower().strip()} "
        for one in self.playbook:
            if any(cue in lowered for cue in one.cues):
                return one.code
        return None

    def handle(self, code: str) -> ObjectionOutcome:
        entry = self.entry(code)
        if entry is None:
            return ObjectionOutcome(code, ObjectionExit.CONTINUE)
        self.raised += 1
        seen = self.counts.get(code, 0)
        self.counts[code] = seen + 1

        if entry.immediate is not None:
            return ObjectionOutcome(
                code,
                entry.immediate,
                entry.responses[0] if entry.responses else "",
            )
        if seen < entry.max_attempts - 1:
            return ObjectionOutcome(code, ObjectionExit.CONTINUE, entry.responses[seen])
        if seen < entry.max_attempts:
            return ObjectionOutcome(code, entry.on_exhausted, entry.responses[seen])
        return ObjectionOutcome(code, entry.on_exhausted)

    def over_budget(self) -> bool:
        return self.raised >= self.budget

# ---------------------------------------------------------------- the licensed agent boundary
#
# Collecting facts needs no licence. Recommending cover, naming a premium, or agreeing that
# somebody is covered is the practice of insurance, and doing it through a machine is how an
# agency loses its licence rather than merely annoys somebody. So this is a refusal surface with
# fixed wording, not a line in a prompt.


class BoundaryKind(str, Enum):
    QUOTE_PRICE = "quote_price"
    RECOMMEND_COVERAGE = "recommend_coverage"
    CONFIRM_COVERED = "confirm_covered"
    BIND_OR_CANCEL = "bind_or_cancel"
    CLAIM_ADVICE = "claim_advice"
    LEGAL_OR_TAX = "legal_or_tax"


@dataclass(frozen=True)
class BoundaryRule:
    kind: BoundaryKind
    cues: tuple[str, ...]
    say: str
    # Whether the right answer is to hand the call to a licensed agent there and then.
    offer_transfer: bool = True


BOUNDARIES: tuple[BoundaryRule, ...] = (
    BoundaryRule(
        BoundaryKind.QUOTE_PRICE,
        (
            "how much will it be", "how much would it cost", "what's the price", "whats the price",
            "give me a quote", "what will i pay", "how much do you charge", "ballpark",
            "roughly how much",
        ),
        "I am not able to give you a price myself. A licensed agent works out the numbers once I have "
        "the details, and they can go through them with you.",
    ),
    BoundaryRule(
        BoundaryKind.RECOMMEND_COVERAGE,
        (
            # "should i get" rather than "what should i get", because the question arrives with the
            # subject in the middle: "what deductible should i get".
            "should i get", "should i have", "should i take", "what do you recommend",
            "what would you recommend", "do i need", "is that enough cover",
            "is that enough coverage", "what would you do", "how much cover should",
            "is that the right",
        ),
        "That is advice, and it has to come from a licensed agent rather than from me. I can note what "
        "you are asking and have them answer it.",
    ),
    BoundaryRule(
        BoundaryKind.CONFIRM_COVERED,
        (
            "am i covered", "would that be covered", "does it cover", "is that included",
            "will they pay for",
        ),
        "I cannot tell you what is or is not covered. That depends on the policy wording, and a "
        "licensed agent will read it with you.",
    ),
    BoundaryRule(
        BoundaryKind.BIND_OR_CANCEL,
        (
            "sign me up", "go ahead and buy", "cancel my current", "switch me over", "bind it",
            "take the payment", "put it in place today",
        ),
        "I cannot put cover in place or cancel anything. That has to be a licensed agent, and I can "
        "connect you.",
    ),
    BoundaryRule(
        BoundaryKind.CLAIM_ADVICE,
        (
            "should i claim", "will claiming", "should i report it", "will my premium go up if i claim",
        ),
        "Whether to claim is not something I can weigh up for you. A licensed agent can talk it "
        "through.",
    ),
    BoundaryRule(
        BoundaryKind.LEGAL_OR_TAX,
        ("is that legal", "tax deductible", "sue", "my lawyer", "liable for"),
        "That is outside anything I can speak to. I will pass it to a licensed agent.",
        offer_transfer=True,
    ),
)


def crossed(text: str) -> BoundaryRule | None:
    """Which boundary this turn asks the agent to cross, if any."""
    lowered = f" {text.lower().strip()} "
    for one in BOUNDARIES:
        if any(cue in lowered for cue in one.cues):
            return one
    return None


def deflection_for(text: str) -> str:
    rule = crossed(text)
    return rule.say if rule else ""
