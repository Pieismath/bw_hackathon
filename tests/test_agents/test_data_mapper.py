"""Tests for B1 (Data Mapper) and B2 (Mapping Verifier)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.agents.implementation import (
    CATALOG,
    DATA_MAPPER_MODEL,
    MAPPING_VERIFIER_MODEL,
    method_exists,
    source_exists,
    verify_mapping,
)
from src.agents.implementation.data_mapper import map_data
from src.agents.extraction import extract_methodology
from src.pdf.parser import parse_pdf
from src.specs import (
    DataMapping,
    FieldMapping,
    PortfolioSpec,
    RebalanceSpec,
    ReplicationSpec,
    SignalSpec,
    UniverseSpec,
    VerifiedDataMapping,
)

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")


def _minimal_spec(start=date(1995, 1, 1), end=date(2020, 12, 31)) -> ReplicationSpec:
    return ReplicationSpec(
        paper_id="t",
        paper_title="t",
        universe=UniverseSpec(name="u"),
        signal=SignalSpec(
            name="s", formula="f", inputs=("close",),
            kind="past_return", lookback_months=6, skip_months=0,
            frequency="monthly",
        ),
        portfolio=PortfolioSpec(
            construction="decile", n_buckets=10, long_bucket=10,
            short_bucket=1, weighting="equal", long_short=True, gross_exposure=2.0,
        ),
        rebalance=RebalanceSpec(
            frequency="monthly", execution_lag_days=1, holding_period_months=6,
        ),
        start_date=start, end_date=end,
    )


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------

def test_catalog_contains_defeatbeta_and_kf():
    names = {s["name"] for s in CATALOG}
    assert "defeatbeta_yahoo" in names
    assert "ken_french_csv" in names


def test_source_exists_lookup():
    assert source_exists("defeatbeta_yahoo")
    assert not source_exists("nonsense_source")


def test_method_exists_lookup():
    assert method_exists("defeatbeta_yahoo", "get_monthly_close_panel")
    assert not method_exists("defeatbeta_yahoo", "get_unicorns")


# ---------------------------------------------------------------------------
# B1/B2 model routing
# ---------------------------------------------------------------------------

def test_data_mapper_uses_opus_47():
    assert DATA_MAPPER_MODEL == "claude-opus-4-7"


def test_mapping_verifier_uses_haiku():
    assert MAPPING_VERIFIER_MODEL == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# B2 deterministic verification (no LLM)
# ---------------------------------------------------------------------------

def test_verify_mapping_flags_unknown_source():
    spec = _minimal_spec()
    bad = DataMapping(
        mappings=(
            FieldMapping(
                spec_field="universe",
                source_name="made_up_source",
                method="get_universe",
                fidelity="high",
                fidelity_notes="n/a",
            ),
        ),
    )
    verified = verify_mapping(bad, spec, use_llm_check=False)
    assert len(verified.blocking_issues) == 1
    check = verified.checks[0]
    assert check.source_exists is False
    assert check.blocking is True


def test_verify_mapping_flags_unknown_method():
    spec = _minimal_spec()
    bad = DataMapping(
        mappings=(
            FieldMapping(
                spec_field="universe",
                source_name="defeatbeta_yahoo",
                method="get_unicorns",
                fidelity="high",
                fidelity_notes="n/a",
            ),
        ),
    )
    verified = verify_mapping(bad, spec, use_llm_check=False)
    check = verified.checks[0]
    assert check.source_exists is True
    assert check.fields_present is False
    assert check.blocking is True


def test_verify_mapping_flags_out_of_range_dates():
    # Ask for 1965-1989 on defeatbeta_yahoo (1994-present) with high fidelity.
    spec = _minimal_spec(start=date(1965, 1, 1), end=date(1989, 12, 31))
    bad = DataMapping(
        mappings=(
            FieldMapping(
                spec_field="signal.inputs[0]",
                source_name="defeatbeta_yahoo",
                method="get_monthly_close_panel",
                fidelity="high",
                fidelity_notes="n/a",
            ),
        ),
    )
    verified = verify_mapping(bad, spec, use_llm_check=False)
    check = verified.checks[0]
    assert check.date_range_ok is False
    # Blocking because fidelity='high' is inconsistent with the coverage gap.
    assert check.blocking is True


def test_verify_mapping_date_range_low_fidelity_not_blocking():
    # Same coverage gap but flagged 'low' fidelity with notes — not blocking.
    spec = _minimal_spec(start=date(1965, 1, 1), end=date(1989, 12, 31))
    honest = DataMapping(
        mappings=(
            FieldMapping(
                spec_field="signal.inputs[0]",
                source_name="defeatbeta_yahoo",
                method="get_monthly_close_panel",
                fidelity="low",
                fidelity_notes="coverage begins 1994-11; paper window unavailable",
            ),
        ),
    )
    verified = verify_mapping(honest, spec, use_llm_check=False)
    check = verified.checks[0]
    assert check.date_range_ok is False
    assert check.blocking is False  # low + honest note → not blocking


# ---------------------------------------------------------------------------
# Overall fidelity rollup
# ---------------------------------------------------------------------------

def test_overall_fidelity_min_of_all():
    # Two mappings; one high, one medium → overall medium.
    spec = _minimal_spec()
    mixed = DataMapping(
        mappings=(
            FieldMapping(
                spec_field="universe",
                source_name="defeatbeta_yahoo",
                method="get_universe",
                fidelity="high",
                fidelity_notes="exact fit",
            ),
            FieldMapping(
                spec_field="signal.inputs[0]",
                source_name="defeatbeta_yahoo",
                method="get_monthly_close_panel",
                fidelity="medium",
                fidelity_notes="survivorship bias",
            ),
        ),
    )
    verified = verify_mapping(mixed, spec, use_llm_check=False)
    assert verified.overall_fidelity == "medium"


# ---------------------------------------------------------------------------
# Integration (cached)
# ---------------------------------------------------------------------------

@pytest.mark.llm
def test_b1_produces_mapping_for_jt_spec():
    pdf = parse_pdf(JT_PDF)
    spec = extract_methodology(pdf)
    mapping = map_data(spec)
    assert isinstance(mapping, DataMapping)
    assert len(mapping.mappings) >= 1
    # Every proposed mapping points at an existing source
    for m in mapping.mappings:
        assert source_exists(m.source_name), (
            f"B1 proposed unknown source {m.source_name}"
        )


@pytest.mark.llm
def test_b1_b2_end_to_end_on_jt():
    pdf = parse_pdf(JT_PDF)
    spec = extract_methodology(pdf)
    mapping = map_data(spec)
    verified = verify_mapping(mapping, spec, use_llm_check=True)
    assert isinstance(verified, VerifiedDataMapping)
    # Block on any missing source/method, but survivorship-bias mappings
    # should NOT block.
    assert all(
        check.source_exists and check.fields_present
        for check in verified.checks
    ), f"B1/B2 found missing sources/methods: {verified.blocking_issues}"
