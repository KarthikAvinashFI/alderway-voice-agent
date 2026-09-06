"""Deciding whether a collected intake can be quoted.

The one thing this must never do is treat "I do not know" as "no". A predicate over a missing answer
returns None, and a hard rule that returns None makes the whole intake reviewable by a person rather
than quietly eligible. Undecidable is a third outcome, not a rounding error.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from form_spec import All, Any_, Condition, Not, Predicate
from rules import RULES, Decision, EligibilityResult, Finding, Rule, Severity
from rules_derived import DEFAULT_REFERENCE_YEAR, derive


def fields_in(predicate: Predicate) -> tuple[str, ...]:
    if isinstance(predicate, Condition):
        return (predicate.field,)
    if isinstance(predicate, (All, Any_)):
        found: list[str] = []
        for one in predicate.conditions:
            found.extend(fields_in(one))
        return tuple(dict.fromkeys(found))
    if isinstance(predicate, Not):
        return fields_in(predicate.condition)
    return ()


def _missing_for(predicate: Predicate, values: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        name for name in fields_in(predicate) if name not in values or values[name] is None
    )


def evaluate(
    values: Mapping[str, Any],
    *,
    rules: Iterable[Rule] = RULES,
    reference_year: int = DEFAULT_REFERENCE_YEAR,
) -> EligibilityResult:
    """Run every rule against the answers, and say what a licensed agent should do next."""
    merged: dict[str, Any] = dict(values)
    merged.update(derive(values, reference_year=reference_year))

    hard: list[Finding] = []
    soft: list[Finding] = []
    indeterminate: list[Finding] = []

    for one in rules:
        outcome = one.predicate.evaluate(merged)
        if outcome is True:
            finding = Finding(one.code, one.severity, one.reason, one.internal_note)
            (hard if one.severity is Severity.HARD else soft).append(finding)
        elif outcome is None:
            # Only worth reporting for a hard rule. A soft flag nobody can settle changes nothing
            # about whether the call transfers, and listing it would bury the ones that matter.
            if one.severity is Severity.HARD:
                indeterminate.append(
                    Finding(
                        one.code,
                        one.severity,
                        one.reason,
                        one.internal_note,
                        missing=_missing_for(one.predicate, merged),
                    )
                )

    if hard:
        decision = Decision.DISQUALIFIED
    elif indeterminate:
        decision = Decision.NEEDS_REVIEW
    else:
        decision = Decision.ELIGIBLE

    return EligibilityResult(
        decision=decision,
        hard=tuple(hard),
        soft=tuple(soft),
        indeterminate=tuple(indeterminate),
    )


def evaluate_intake(engine: Any, *, reference_year: int = DEFAULT_REFERENCE_YEAR) -> EligibilityResult:
    """Convenience over an `IntakeEngine`, kept untyped to avoid importing the form layer here."""
    return evaluate(engine.values(), reference_year=reference_year)
