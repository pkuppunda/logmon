"""Deterministic root-cause ranking for correlated incidents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from hotel_automation.incidents.components import role_depends_on
from hotel_automation.incidents.constants import (
    CAUSAL_DOWNSTREAM_PHRASES,
    EDGE_ROLES,
    INFRA_INITIATING_MARKERS,
    INFRA_ROLES,
    INITIATING_CODE_MARKERS,
    OPAQUE_MESSAGES,
    SYMPTOM_CODE_MARKERS,
    SYMPTOM_CODE_SUFFIXES,
    SYMPTOM_MESSAGE_PHRASES,
)
from hotel_automation.incidents.summary import ComponentSummary

MIN_WINNING_MARGIN = 5.0
MIN_WINNING_SCORE = -10.0


@dataclass(frozen=True)
class RootCause:
    component_key: Optional[str]
    description: Optional[str]
    confidence: Optional[float]


UNDETERMINED = RootCause(None, None, None)


def depends_on(downstream: ComponentSummary, upstream: ComponentSummary) -> bool:
    if role_depends_on(downstream.role, upstream.role):
        return True
    if (
        upstream.role is not None
        and upstream.role != downstream.role
        and upstream.role in downstream.code_roles
    ):
        return True
    return bool(downstream.code_tokens & upstream.tokens) and not (
        upstream.code_tokens & downstream.tokens
    )


def has_initiating_code(summary: ComponentSummary) -> bool:
    for code in summary.event_codes:
        if any(marker in code for marker in INITIATING_CODE_MARKERS):
            return True
        if summary.role in INFRA_ROLES and any(
            marker in code for marker in INFRA_INITIATING_MARKERS
        ):
            return True
    return False


def has_symptom_markers(summary: ComponentSummary) -> bool:
    for code in summary.event_codes:
        if code.endswith(SYMPTOM_CODE_SUFFIXES):
            return True
        if any(marker in code for marker in SYMPTOM_CODE_MARKERS):
            return True
    message = summary.representative_message.strip().lower()
    return any(phrase in message for phrase in CAUSAL_DOWNSTREAM_PHRASES)


def is_symptom_only_or_opaque(summary: ComponentSummary) -> bool:
    codes = summary.event_codes
    if codes and all(
        any(marker in code for marker in SYMPTOM_CODE_MARKERS) for code in codes
    ):
        return True
    message = summary.representative_message.strip().lower().rstrip(".!")
    if not codes and any(phrase in message for phrase in SYMPTOM_MESSAGE_PHRASES):
        return True
    return not codes and summary.role is None and message in OPAQUE_MESSAGES


def describe(summary: ComponentSummary) -> str:
    if summary.representative_code:
        return (
            f"{summary.display_component} ({summary.representative_code}): "
            f"{summary.representative_message}"
        )
    return f"{summary.display_component}: {summary.representative_message}"


def _single_component(summary: ComponentSummary) -> RootCause:
    if is_symptom_only_or_opaque(summary):
        return UNDETERMINED

    confidence = 0.58
    if summary.representative_code:
        confidence += 0.10
        if has_initiating_code(summary):
            confidence += 0.07
    if summary.role in INFRA_ROLES:
        confidence += 0.06
    if summary.has_critical:
        confidence += 0.05
    if summary.failure_count >= 2:
        confidence += 0.04

    return RootCause(
        summary.key, describe(summary), round(min(0.85, max(0.50, confidence)), 2)
    )


def _score(
    candidate: ComponentSummary, others: list[ComponentSummary], min_ts, max_ts
) -> tuple[float, bool]:
    downstream = sum(1 for other in others if depends_on(other, candidate))
    upstream = sum(1 for other in others if depends_on(candidate, other))

    score = min(50.0, downstream * 35.0) - min(50.0, upstream * 35.0)

    symptomatic = has_symptom_markers(candidate)
    if has_initiating_code(candidate):
        score += 15.0
    elif candidate.representative_code and not symptomatic:
        score += 4.0
    if symptomatic:
        score -= 25.0

    if candidate.role in INFRA_ROLES:
        score += 10.0
    elif candidate.role in EDGE_ROLES:
        score -= 5.0

    if candidate.earliest_ts == min_ts and max_ts > min_ts:
        score += 15.0
    elif candidate.earliest_ts > min_ts:
        delay = (candidate.earliest_ts - min_ts).total_seconds()
        score -= min(15.0, delay * 1.5)

    if candidate.has_critical:
        score += 8.0

    return score, downstream > 0 and upstream == 0


def rank_root_cause(summaries: Iterable[ComponentSummary]) -> RootCause:
    """Return the most likely initiating component, or UNDETERMINED."""
    items = list(summaries)
    if not items:
        return UNDETERMINED
    if len(items) == 1:
        return _single_component(items[0])

    min_ts = min(s.earliest_ts for s in items)
    max_ts = max(s.earliest_ts for s in items)

    scored = []
    for candidate in items:
        others = [s for s in items if s is not candidate]
        score, dependency_support = _score(candidate, others, min_ts, max_ts)
        scored.append((score, candidate, dependency_support))

    scored.sort(
        key=lambda item: (item[0], -item[1].earliest_ts.timestamp(), -item[1].earliest[1]),
        reverse=True,
    )
    best_score, best, dependency_support = scored[0]
    margin = best_score - scored[1][0]

    if best_score < MIN_WINNING_SCORE or margin < MIN_WINNING_MARGIN:
        return UNDETERMINED

    confidence = 0.50 + min(0.40, margin / 120.0)
    if dependency_support and best.earliest_ts == min_ts:
        confidence += 0.05
    elif best.earliest_ts > min_ts:
        confidence = min(confidence, 0.74)

    return RootCause(
        best.key, describe(best), round(min(0.95, max(0.35, confidence)), 2)
    )
