"""Decide how urgent a home-service enquiry is.

Rules run first and always. An optional LLM classifier can refine the label,
but it is never allowed to downgrade a rule-detected safety case, and any
error from it falls back to the rule result.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Urgency(StrEnum):
    SAFETY = "safety"  # gas smell, CO alarm: tell them to leave and call the utility
    EMERGENCY = "emergency"  # no AC in summer, burst pipe, flooding
    STANDARD = "standard"  # repair or quote request
    LOW = "low"  # maintenance, general question

    @property
    def rank(self) -> int:
        return {"safety": 3, "emergency": 2, "standard": 1, "low": 0}[self.value]


@dataclass(frozen=True)
class Classification:
    urgency: Urgency
    reason: str
    source: str = "rules"


_RULES: list[tuple[Urgency, str, re.Pattern[str]]] = [
    (
        Urgency.SAFETY,
        "gas or carbon monoxide",
        re.compile(r"\b(gas (smell|leak)|smell(s|ing)? (like |of )?gas|carbon monoxide|co alarm|co detector)\b"),
    ),
    (
        Urgency.EMERGENCY,
        "active water damage",
        re.compile(r"\b(burst|flood(ing|ed)?|water everywhere|pipe (broke|burst)|sewage|backing up|overflow(ing)?)\b"),
    ),
    (
        Urgency.EMERGENCY,
        "no cooling or heating",
        re.compile(
            r"\b(no (ac|a/c|air|cooling|heat|heating|hot water)|(ac|a/c|heater|furnace|unit) (is )?(not working|stopped|died|dead|out)|blowing (hot|warm) air)\b"
        ),
    ),
    (
        Urgency.EMERGENCY,
        "vulnerable occupant",
        re.compile(r"\b(baby|infant|newborn|elderly|grandma|grandpa|medical)\b"),
    ),
    (
        Urgency.LOW,
        "maintenance or general question",
        re.compile(r"\b(tune[- ]?up|maintenance|annual|inspection|filter|quote for next|just wondering|membership)\b"),
    ),
]


def classify_rules(text: str) -> Classification:
    lowered = text.lower()
    best: Classification | None = None
    for urgency, reason, pattern in _RULES:
        if pattern.search(lowered):
            if best is None or urgency.rank > best.urgency.rank:
                best = Classification(urgency, reason)
    return best or Classification(Urgency.STANDARD, "repair or quote request")


class LLMClassifier(Protocol):
    def __call__(self, text: str) -> Urgency: ...


def classify(text: str, llm: LLMClassifier | Callable[[str], Urgency] | None = None) -> Classification:
    base = classify_rules(text)
    if llm is None or base.urgency is Urgency.SAFETY:
        return base
    try:
        refined = llm(text)
    except Exception:  # an LLM outage must never block intake
        return base
    if refined.rank < base.urgency.rank and base.urgency is not Urgency.STANDARD:
        # Rules found a concrete emergency signal; keep it.
        return base
    return Classification(refined, "llm refinement", source="llm")
