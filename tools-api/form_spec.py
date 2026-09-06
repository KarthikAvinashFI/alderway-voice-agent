"""The vocabulary a questionnaire is written in.

Nothing here knows about insurance. The intake in `intake.py` is data expressed in these types, so
the same engine can walk a different form without being touched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Any


class AnswerType(str, Enum):
    BOOL = "bool"
    INT = "int"
    YEAR = "year"
    CURRENCY = "currency"
    STRING = "string"
    ENUM = "enum"
    DATE = "date"
    ADDRESS = "address"
    EMAIL = "email"
    PHONE = "phone"


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    # Declined, and that is a fact worth keeping rather than a blank. A quote desk needs to know the
    # difference between a caller who was never asked and one who would not say.
    REFUSED = "refused"
    # Asked, willing, does not know. Different from refused because a lookup can still fill it in.
    UNKNOWN = "unknown"
    # Branching decided this field does not apply.
    NOT_APPLICABLE = "not_applicable"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Condition:
    """One test against a collected answer.

    `answered` and `missing` deliberately ignore `value`, which is what lets a branch depend on
    whether something was said at all rather than on what it was.
    """

    field: str
    op: str
    value: Any = None

    def evaluate(self, answers: Mapping[str, Any]) -> bool | None:
        """True, False, or None where the answer this depends on is not available.

        None matters: a branch that cannot be decided is not the same as a branch that is closed,
        and a rule that cannot be decided must not read as a pass.
        """
        if self.op == "answered":
            return self.field in answers and answers[self.field] is not None
        if self.op == "missing":
            return self.field not in answers or answers[self.field] is None
        if self.field not in answers or answers[self.field] is None:
            return None
        actual = answers[self.field]
        if self.op == "eq":
            return actual == self.value
        if self.op == "ne":
            return actual != self.value
        if self.op == "in":
            return actual in self.value
        if self.op == "not_in":
            return actual not in self.value
        if self.op == "contains":
            return self.value in actual if isinstance(actual, (str, list, tuple)) else False
        try:
            if self.op == "gt":
                return actual > self.value
            if self.op == "gte":
                return actual >= self.value
            if self.op == "lt":
                return actual < self.value
            if self.op == "lte":
                return actual <= self.value
        except TypeError:
            # A comparison against the wrong shape is undecidable, never False. Treating it as False
            # would silently clear a disqualifier.
            return None
        raise ValueError(f"unknown condition op {self.op!r}")


@dataclass(frozen=True)
class All:
    conditions: tuple[Predicate, ...]

    def evaluate(self, answers: Mapping[str, Any]) -> bool | None:
        results = [one.evaluate(answers) for one in self.conditions]
        if any(result is False for result in results):
            return False
        if any(result is None for result in results):
            return None
        return True


@dataclass(frozen=True)
class Any_:
    conditions: tuple[Predicate, ...]

    def evaluate(self, answers: Mapping[str, Any]) -> bool | None:
        results = [one.evaluate(answers) for one in self.conditions]
        if any(result is True for result in results):
            return True
        if any(result is None for result in results):
            return None
        return False


@dataclass(frozen=True)
class Not:
    condition: Predicate

    def evaluate(self, answers: Mapping[str, Any]) -> bool | None:
        result = self.condition.evaluate(answers)
        return None if result is None else not result


Predicate = Condition | All | Any_ | Not


@dataclass(frozen=True)
class Field:
    id: str
    section: str
    ask: str
    type: AnswerType
    # Said differently on a second attempt. A caller who did not understand the first phrasing rarely
    # understands the same phrasing louder.
    reprompts: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    # Spoken phrases that mean one of the choices. A caller says "our main home", not "primary", and a
    # form that only accepts its own tokens makes the agent re-ask a question already answered.
    synonyms: tuple[tuple[str, str], ...] = ()
    required_for_quote: bool = False
    asked_when: Predicate | None = None
    # Ending the call over a refusal is reserved for the few answers without which there is nothing
    # to quote and no right to continue.
    fatal_if_refused: bool = False
    minimum: float | None = None
    maximum: float | None = None
    # Which eligibility rules read this field. Written down so a rule cannot quietly depend on
    # something the form never asks.
    feeds_rules: tuple[str, ...] = ()
    # Set on the template fields of a repeating group. The engine expands them per index.
    repeat_group: str | None = None
    help_text: str = ""

    def applies(self, answers: Mapping[str, Any]) -> bool | None:
        if self.asked_when is None:
            return True
        return self.asked_when.evaluate(answers)


@dataclass(frozen=True)
class RepeatGroup:
    """A block of fields asked once per item, as many times as a count field says.

    Claims are the reason this exists: "three claims" means nine more answers, and a flat form
    cannot express that without inventing the fields up front.
    """

    name: str
    count_field: str
    maximum: int
    label_singular: str


@dataclass(frozen=True)
class Section:
    name: str
    title: str
    # Spoken once when the section opens, so a caller knows why the subject changed.
    lead_in: str = ""


@dataclass
class Questionnaire:
    name: str
    version: str
    sections: tuple[Section, ...]
    fields: tuple[Field, ...]
    repeat_groups: tuple[RepeatGroup, ...] = ()
    opening: str = ""
    closing: str = ""
    _by_id: dict[str, Field] = dataclass_field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {one.id: one for one in self.fields}
        duplicates = len(self.fields) - len(self._by_id)
        if duplicates:
            raise ValueError(f"{duplicates} duplicate field id(s) in {self.name}")
        known_sections = {one.name for one in self.sections}
        unknown = {one.section for one in self.fields} - known_sections
        if unknown:
            raise ValueError(f"fields reference unknown sections: {sorted(unknown)}")

    def field(self, field_id: str) -> Field | None:
        return self._by_id.get(field_id)

    def in_section(self, section: str) -> tuple[Field, ...]:
        return tuple(one for one in self.fields if one.section == section)

    def group(self, name: str) -> RepeatGroup | None:
        for one in self.repeat_groups:
            if one.name == name:
                return one
        return None

    def template_fields(self, group: str) -> tuple[Field, ...]:
        return tuple(one for one in self.fields if one.repeat_group == group)

    def required_ids(self) -> tuple[str, ...]:
        return tuple(one.id for one in self.fields if one.required_for_quote)


def sections_of(questionnaire: Questionnaire) -> Sequence[str]:
    return [one.name for one in questionnaire.sections]
