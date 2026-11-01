"""Component normalization and architectural relationships."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Optional

from hotel_automation.incidents.constants import (
    COMPONENT_TOKEN_TO_ROLE,
    DEPENDS_ON,
    HEAD_INFRA_ROLES,
    STOP_COMPONENT_TOKENS,
)

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ComponentProfile:
    display: str
    name: str
    tokens: frozenset[str]
    role: Optional[str]
    qualifiers: frozenset[str]


def _tokenize(text: str) -> list[str]:
    return [tok for tok in _TOKEN_SPLIT_RE.split(text.lower()) if tok]


def _resolve_role(name: str, tokens: list[str], significant: list[str]) -> Optional[str]:
    role = COMPONENT_TOKEN_TO_ROLE.get(name)
    if role is not None:
        return role
    if significant:
        tail_role = COMPONENT_TOKEN_TO_ROLE.get(significant[-1])
        if tail_role in HEAD_INFRA_ROLES:
            return tail_role
    for tok in significant + tokens:
        mapped = COMPONENT_TOKEN_TO_ROLE.get(tok)
        if mapped is not None:
            return mapped
    return None


@lru_cache(maxsize=4096)
def profile_component(component: str) -> ComponentProfile:
    tokens = _tokenize(component)
    name = "_".join(tokens) if tokens else component.strip().lower()
    significant = [tok for tok in tokens if tok not in STOP_COMPONENT_TOKENS]
    role = _resolve_role(name, tokens, significant)
    distinctive = [tok for tok in significant if len(tok) >= 2]
    qualifiers = frozenset(
        tok for tok in distinctive if COMPONENT_TOKEN_TO_ROLE.get(tok) != role
    )
    return ComponentProfile(
        display=component,
        name=name,
        tokens=frozenset(tokens),
        role=role,
        qualifiers=qualifiers,
    )


@lru_cache(maxsize=4096)
def code_profile(event_code: Optional[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Return (roles referenced, significant tokens) for an event code."""
    if not event_code:
        return frozenset(), frozenset()
    tokens = _tokenize(event_code)
    roles = {
        COMPONENT_TOKEN_TO_ROLE[tok]
        for tok in tokens
        if tok in COMPONENT_TOKEN_TO_ROLE
    }
    significant = {
        tok for tok in tokens if tok not in STOP_COMPONENT_TOKENS and len(tok) >= 2
    }
    return frozenset(roles), frozenset(significant)


def same_logical_component(a: ComponentProfile, b: ComponentProfile) -> bool:
    if a.name == b.name:
        return True
    return (
        a.role is not None
        and a.role == b.role
        and not a.qualifiers
        and not b.qualifiers
    )


def role_depends_on(downstream: Optional[str], upstream: Optional[str]) -> bool:
    if downstream is None or upstream is None or downstream == upstream:
        return False
    return upstream in DEPENDS_ON.get(downstream, frozenset())


def roles_related(a: Optional[str], b: Optional[str]) -> bool:
    return role_depends_on(a, b) or role_depends_on(b, a)
