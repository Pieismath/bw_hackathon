"""Tests for A3 — Adversarial Reviewer."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.extraction import (
    ADVERSARIAL_REVIEWER_MODEL,
    extract_methodology,
    fold_high_severity_into_spec,
    review,
)
from src.pdf.parser import parse_pdf
from src.specs import AdversarialCritique, Criticism

JT_PDF = Path("data/papers/jegadeesh_titman_1993_returns_to_buying_winners_and_selling_losers.pdf")


def test_reviewer_uses_sonnet_46():
    # A3 uses Sonnet 4.6 (was Opus 4.7) — see README. Same TPM-budget
    # rationale as A1; A3 also reads the full paper.
    assert ADVERSARIAL_REVIEWER_MODEL == "claude-sonnet-4-6"


def test_critique_schema_enforces_exactly_three():
    # Positive: 3 criticisms validates
    base = {
        "category": "missed",
        "severity": "medium",
        "description": "x",
        "proposed_remediation": "y",
    }
    ok = AdversarialCritique(criticisms=[base, base, base])
    assert len(ok.criticisms) == 3

    # Negative: 2 fails; 4 fails; 0 fails
    for n in (0, 1, 2, 4, 5):
        with pytest.raises(Exception):
            AdversarialCritique(criticisms=[base] * n)


def test_fold_high_severity_adds_flags_to_spec():
    # Build a synthetic critique: one high, one medium, one low. Only the
    # high one should become an ambiguity flag.
    pdf = parse_pdf(JT_PDF)
    spec = extract_methodology(pdf)  # cached
    n_before = len(spec.ambiguities)

    critique = AdversarialCritique(
        criticisms=[
            Criticism(
                category="missed",
                severity="high",
                description="Hypothetical missed choice with high impact.",
                proposed_remediation="Flag the missed choice at high severity.",
            ),
            Criticism(
                category="oversimplified",
                severity="medium",
                description="Hypothetical oversimplification.",
                proposed_remediation="Note in report.",
            ),
            Criticism(
                category="alternative_interpretation",
                severity="low",
                description="Hypothetical low-severity alt reading.",
                proposed_remediation="Note in report.",
            ),
        ]
    )
    updated = fold_high_severity_into_spec(spec, critique)
    assert len(updated.ambiguities) == n_before + 1
    new_flag = updated.ambiguities[-1]
    assert new_flag.sensitivity_priority == "high"
    assert new_flag.parameter.startswith("a3.")


@pytest.mark.llm
def test_a3_produces_three_criticisms_on_jt():
    pdf = parse_pdf(JT_PDF)
    spec = extract_methodology(pdf)
    critique = review(pdf, spec)
    assert isinstance(critique, AdversarialCritique)
    assert len(critique.criticisms) == 3
    # Category diversity is a prompt target, not a hard requirement — log.
    cats = {c.category for c in critique.criticisms}
    assert cats.issubset({"missed", "oversimplified", "alternative_interpretation"})
