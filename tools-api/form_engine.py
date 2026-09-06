"""Walking a questionnaire: what to ask next, and what to do with what comes back.

The engine is the part that makes this survive a real conversation. Three things it must get right,
and each one is a defect if it does not:

- Answers arrive for questions that were not asked. A caller who says "1998, roof done four years
  back, never claimed" has answered three fields, and asking any of them again reads as not
  listening.
- A refusal is data. Stored as a refusal with what they said, so a licensed agent can see the
  difference between not asked and would not say.
- A correction replaces a value without losing the old one, because "actually it was 2019" is the
  kind of thing an underwriter later needs the history of.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Any

from form_spec import (
    All,
    AnswerStatus,
    Any_,
    Condition,
    Confidence,
    Field,
    Not,
    Predicate,
    Questionnaire,
)
from form_values import confidence_for, normalize


class IntakeState(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    # A refusal on something there is no proceeding without: consent, the address, ownership.
    ENDED_FATAL_REFUSAL = "ended_fatal_refusal"
    ENDED_BY_CALLER = "ended_by_caller"


@dataclass(frozen=True)
class Revision:
    value: Any
    status: AnswerStatus
    verbatim: str
    confidence: Confidence
    sequence: int
    reason: str = ""


@dataclass
class Answer:
    field_id: str
    value: Any
    status: AnswerStatus
    verbatim: str = ""
    confidence: Confidence = Confidence.HIGH
    sequence: int = 0
    revisions: list[Revision] = dataclass_field(default_factory=list)

    @property
    def corrected(self) -> bool:
        return bool(self.revisions)


@dataclass(frozen=True)
class ExtractedAnswer:
    """One field the model believes it heard, from anywhere in the caller's turn."""

    field_id: str
    text: str
    status: AnswerStatus = AnswerStatus.ANSWERED


@dataclass(frozen=True)
class ApplyResult:
    field_id: str
    accepted: bool
    value: Any = None
    problem: str = ""
    corrected: bool = False
    fatal: bool = False


def _substitute(predicate: Predicate | None, index: int) -> Predicate | None:
    """Rewrite `{i}` in a repeat template's condition to the item it now belongs to."""
    if predicate is None:
        return None
    if isinstance(predicate, Condition):
        return replace(predicate, field=predicate.field.replace("{i}", str(index)))
    if isinstance(predicate, All):
        return All(tuple(_substitute(one, index) for one in predicate.conditions))
    if isinstance(predicate, Any_):
        return Any_(tuple(_substitute(one, index) for one in predicate.conditions))
    if isinstance(predicate, Not):
        return Not(_substitute(predicate.condition, index))
    raise TypeError(f"cannot substitute into {type(predicate)!r}")


def expanded_id(template_id: str, index: int) -> str:
    return f"{template_id}#{index}"


_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


def _ordinal(index: int) -> str:
    return _ORDINALS[index - 1] if 1 <= index <= len(_ORDINALS) else f"number {index}"


@dataclass(frozen=True)
class PlanItem:
    field: Field
    # Set for a field belonging to a repeating group, so the agent can say which item it is asking about.
    group: str | None = None
    index: int | None = None
    label: str = ""

    def prompt(self) -> str:
        if self.index is None:
            return self.field.ask
        return f"For the {_ordinal(self.index)} {self.label}: {self.field.ask}"


class IntakeEngine:
    def __init__(
        self,
        questionnaire: Questionnaire,
        answers: Mapping[str, Answer] | None = None,
        *,
        sequence: int = 0,
    ) -> None:
        self.questionnaire = questionnaire
        self.answers: dict[str, Answer] = dict(answers or {})
        self._sequence = sequence
        self._ended_by_caller = False

    # Reading state

    def values(self) -> dict[str, Any]:
        """What conditions and rules see.

        Only a real answer contributes a value. A refusal or a do-not-know contributes None, which
        makes every condition over it undecidable rather than false, so a branch is never silently
        closed and a disqualifier is never silently cleared.
        """
        return {
            answer.field_id: (answer.value if answer.status is AnswerStatus.ANSWERED else None)
            for answer in self.answers.values()
        }

    def plan(self) -> list[PlanItem]:
        """Every field this call could ask, in order, with repeating groups expanded in place."""
        seen = self.values()
        items: list[PlanItem] = []
        counts = {
            group.name: (group, seen.get(group.count_field))
            for group in self.questionnaire.repeat_groups
        }
        by_count_field = {group.count_field: group.name for group in self.questionnaire.repeat_groups}
        for one in self.questionnaire.fields:
            if one.repeat_group is not None:
                continue
            items.append(PlanItem(one))
            group_name = by_count_field.get(one.id)
            if group_name is None:
                continue
            group, count = counts[group_name]
            if not isinstance(count, int) or count <= 0:
                continue
            for index in range(1, min(count, group.maximum) + 1):
                for template in self.questionnaire.template_fields(group_name):
                    items.append(
                        PlanItem(
                            replace(
                                template,
                                id=expanded_id(template.id, index),
                                asked_when=_substitute(template.asked_when, index),
                            ),
                            group=group_name,
                            index=index,
                            label=group.label_singular,
                        )
                    )
        return items

    def applies(self, field: Field) -> bool | None:
        return field.applies(self.values())

    def next_item(self) -> PlanItem | None:
        """The next thing to ask, or None when there is nothing left that can be asked.

        Skips what is already answered, refused, or ruled out by a branch, and skips a field whose
        branch cannot be decided because the answer it depends on was refused.
        """
        for item in self.plan():
            if item.field.id in self.answers:
                continue
            if self.applies(item.field) is not True:
                continue
            return item
        return None

    def next_field(self) -> Field | None:
        item = self.next_item()
        return item.field if item else None

    def item_for(self, field_id: str) -> PlanItem | None:
        for item in self.plan():
            if item.field.id == field_id:
                return item
        return None

    def outstanding(self) -> list[Field]:
        return [
            item.field
            for item in self.plan()
            if item.field.id not in self.answers and self.applies(item.field) is True
        ]

    def missing_required(self) -> list[str]:
        return [
            item.field.id
            for item in self.plan()
            if item.field.required_for_quote
            and self.applies(item.field) is True
            and self.answers.get(item.field.id, None) is None
        ]

    def refused(self) -> list[str]:
        return [one.field_id for one in self.answers.values() if one.status is AnswerStatus.REFUSED]

    def unknown(self) -> list[str]:
        return [one.field_id for one in self.answers.values() if one.status is AnswerStatus.UNKNOWN]

    def blocked(self) -> list[str]:
        """Fields that can never be asked now, because what gated them was refused or not known."""
        return [
            item.field.id
            for item in self.plan()
            if item.field.id not in self.answers and self.applies(item.field) is None
        ]

    def low_confidence(self) -> list[str]:
        return [
            one.field_id
            for one in self.answers.values()
            if one.status is AnswerStatus.ANSWERED and one.confidence is Confidence.LOW
        ]

    @property
    def state(self) -> IntakeState:
        if self._ended_by_caller:
            return IntakeState.ENDED_BY_CALLER
        for answer in self.answers.values():
            field = self.questionnaire.field(answer.field_id.split("#")[0])
            if (
                field is not None
                and field.fatal_if_refused
                and answer.status is AnswerStatus.REFUSED
            ):
                return IntakeState.ENDED_FATAL_REFUSAL
        return IntakeState.COMPLETE if self.next_item() is None else IntakeState.IN_PROGRESS

    def progress(self) -> tuple[int, int]:
        """Answered and total, over what this call is actually going to ask."""
        plan = [item for item in self.plan() if self.applies(item.field) is not False]
        answered = sum(1 for item in plan if item.field.id in self.answers)
        return answered, len(plan)

    # Writing state

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _field_for(self, field_id: str) -> Field | None:
        item = self.item_for(field_id)
        if item is not None:
            return item.field
        # A template field addressed before its count is known still has a definition worth reading,
        # which is what lets a volunteered claim detail land before the claim count does.
        base = self.questionnaire.field(field_id.split("#")[0])
        if base is not None and "#" in field_id:
            index = field_id.split("#")[1]
            if index.isdigit():
                return replace(
                    base,
                    id=field_id,
                    asked_when=_substitute(base.asked_when, int(index)),
                )
        return base

    def record(
        self,
        field_id: str,
        text: str,
        *,
        status: AnswerStatus = AnswerStatus.ANSWERED,
    ) -> ApplyResult:
        """Store one answer, or say why it could not be stored.

        A second value for a field already answered is a correction, never an overwrite.
        """
        field = self._field_for(field_id)
        if field is None:
            return ApplyResult(field_id, False, problem="no such field on this form")

        if status is AnswerStatus.REFUSED:
            return self._store(
                field,
                value=None,
                status=AnswerStatus.REFUSED,
                text=text,
                confidence=Confidence.HIGH,
            )
        if status is AnswerStatus.UNKNOWN:
            return self._store(
                field,
                value=None,
                status=AnswerStatus.UNKNOWN,
                text=text,
                confidence=Confidence.HIGH,
            )

        value, problem = normalize(field, text)
        if problem is not None:
            return ApplyResult(field_id, False, problem=problem)
        return self._store(
            field,
            value=value,
            status=AnswerStatus.ANSWERED,
            text=text,
            confidence=confidence_for(text, value),
        )

    def _store(
        self,
        field: Field,
        *,
        value: Any,
        status: AnswerStatus,
        text: str,
        confidence: Confidence,
    ) -> ApplyResult:
        sequence = self._next_sequence()
        existing = self.answers.get(field.id)
        corrected = False
        if existing is not None:
            unchanged = existing.value == value and existing.status is status
            if unchanged:
                return ApplyResult(field.id, True, value=value)
            existing.revisions.append(
                Revision(
                    value=existing.value,
                    status=existing.status,
                    verbatim=existing.verbatim,
                    confidence=existing.confidence,
                    sequence=existing.sequence,
                )
            )
            existing.value = value
            existing.status = status
            existing.verbatim = text
            existing.confidence = confidence
            existing.sequence = sequence
            corrected = True
        else:
            self.answers[field.id] = Answer(
                field_id=field.id,
                value=value,
                status=status,
                verbatim=text,
                confidence=confidence,
                sequence=sequence,
            )
        fatal = field.fatal_if_refused and status is AnswerStatus.REFUSED
        return ApplyResult(field.id, True, value=value, corrected=corrected, fatal=fatal)

    def record_batch(self, extracted: Iterable[ExtractedAnswer]) -> list[ApplyResult]:
        """Apply everything heard in one turn, in the order given.

        Order matters only where one answer opens a branch another belongs to, and applying in the
        order the caller said them is the closest thing to their intent.
        """
        return [self.record(one.field_id, one.text, status=one.status) for one in extracted]

    def correct(self, field_id: str, text: str) -> ApplyResult:
        return self.record(field_id, text, status=AnswerStatus.ANSWERED)

    def refuse(self, field_id: str, text: str = "") -> ApplyResult:
        return self.record(field_id, text, status=AnswerStatus.REFUSED)

    def mark_unknown(self, field_id: str, text: str = "") -> ApplyResult:
        return self.record(field_id, text, status=AnswerStatus.UNKNOWN)

    def end_by_caller(self) -> None:
        self._ended_by_caller = True

    # Handing on

    def summary(self) -> dict[str, Any]:
        answered, total = self.progress()
        return {
            "questionnaire": self.questionnaire.name,
            "version": self.questionnaire.version,
            "state": self.state.value,
            "answered": answered,
            "askable": total,
            "values": {
                key: value for key, value in self.values().items() if value is not None
            },
            "missing_required": self.missing_required(),
            "refused": self.refused(),
            "unknown": self.unknown(),
            "blocked": self.blocked(),
            "low_confidence": self.low_confidence(),
            "corrected": [one.field_id for one in self.answers.values() if one.corrected],
        }


def engine_from_records(
    questionnaire: Questionnaire, records: Sequence[Mapping[str, Any]]
) -> IntakeEngine:
    """Rebuild an engine from stored rows, which is what makes a dropped call resumable."""
    answers: dict[str, Answer] = {}
    highest = 0
    for row in records:
        answer = Answer(
            field_id=str(row["field_id"]),
            value=row.get("value"),
            status=AnswerStatus(str(row.get("status", AnswerStatus.ANSWERED.value))),
            verbatim=str(row.get("verbatim", "")),
            confidence=Confidence(str(row.get("confidence", Confidence.HIGH.value))),
            sequence=int(row.get("sequence", 0)),
        )
        answers[answer.field_id] = answer
        highest = max(highest, answer.sequence)
    return IntakeEngine(questionnaire, answers, sequence=highest)
