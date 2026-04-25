"""E1 (Phase 5 report synthesizer) smoke + structure tests.

Verifies report.json has the contract the UI depends on. Runs offline —
loads the on-disk cached outputs/*.json and asserts shape, not specific
values (those drift if the engine reruns).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.synthesis import build_report

OUTPUTS = Path("outputs")
HAS_CACHE = (OUTPUTS / "jt_a1_extraction.json").exists()


@pytest.fixture(scope="module")
def report() -> dict:
    if not HAS_CACHE:
        pytest.skip("outputs/ caches not present (run pipelines first)")
    return build_report(OUTPUTS)


# ---------------------------------------------------------------------------
# Top-level structure
# ---------------------------------------------------------------------------

def test_report_top_level_keys(report):
    expected = {
        "meta", "headline", "spec", "verification", "critique",
        "mapping", "diagnosis", "robustness",
    }
    assert expected.issubset(report.keys())


def test_meta_carries_build_timestamp(report):
    assert "built_at" in report["meta"]
    assert "T" in report["meta"]["built_at"]  # ISO8601


# ---------------------------------------------------------------------------
# Headline strip — the demo punchline contract
# ---------------------------------------------------------------------------

def test_headline_has_paper_and_verdict(report):
    h = report["headline"]
    for k in ("paper_id", "paper_title", "sample_paper", "sample_engine",
              "claim", "verdict"):
        assert k in h, f"missing {k} in headline"


def test_headline_claim_is_jt_panel_a(report):
    c = report["headline"]["claim"]
    assert c["value"] == 0.0095
    assert c["tstat"] == 3.07
    assert "Buy-sell" in c["paper_location"]


def test_headline_verdict_carries_label_and_alpha(report):
    v = report["headline"]["verdict"]
    assert "implementable_alpha" in v
    assert "tradeable_label" in v
    assert v["tradeable_label"] in (
        "TRADEABLE", "BORDERLINE", "NOT TRADEABLE AT SCALE",
        "UNDER WATER", "UNKNOWN",
    )
    assert "tstat_estimate" in v
    assert "summary_first_clause" in v
    # The first clause must not be cut at a decimal point (regression for
    # the bug where '.split(\".\")[0]' clipped at "0.")
    assert v["summary_first_clause"] != "Implementable alpha is roughly 0."


def test_headline_jt_label_is_not_tradeable(report):
    """Sanity: with our 1995-2020 sample, JT's implementable alpha is below
    statistical significance. The label must reflect this."""
    v = report["headline"]["verdict"]
    if v.get("tstat_estimate") is not None and abs(v["tstat_estimate"]) < 1.96:
        assert v["tradeable_label"] in (
            "NOT TRADEABLE AT SCALE", "BORDERLINE", "UNDER WATER",
        )


# ---------------------------------------------------------------------------
# Robustness — sweep curves the cost slider depends on
# ---------------------------------------------------------------------------

def test_robustness_cost_curve_sorted_ascending(report):
    rb = report["robustness"]
    bps_values = [p["bps"] for p in rb["cost_curve"]]
    assert bps_values == sorted(bps_values)
    assert len(bps_values) >= 2  # need at least two points to slide between


def test_robustness_lag_curve_sorted_ascending(report):
    rb = report["robustness"]
    lags = [p["lag_days"] for p in rb["lag_curve"]]
    assert lags == sorted(lags)


def test_robustness_cost_curve_each_point_has_required_fields(report):
    rb = report["robustness"]
    for p in rb["cost_curve"]:
        for k in ("bps", "mean_return", "tstat", "surviving"):
            assert k in p


# ---------------------------------------------------------------------------
# Diagnosis tab — D2 mutation log
# ---------------------------------------------------------------------------

def test_diagnosis_mutation_log_has_n_indices(report):
    diag = report["diagnosis"]
    if not diag["mutation_results"]:
        pytest.skip("no mutations in cached D2 output")
    for i, m in enumerate(diag["mutation_results"], 1):
        assert m["n"] == i
        for k in ("parameter", "to_value", "from_value",
                  "pre_mean_return", "post_mean_return", "gap_delta",
                  "closed_sign_flip"):
            assert k in m


# ---------------------------------------------------------------------------
# Generalization side panel
# ---------------------------------------------------------------------------

def test_generalization_present_when_dbt_cached(report):
    if "generalization" not in report:
        pytest.skip("DBT cache not present — generalization optional")
    g = report["generalization"]
    assert g["paper_id"] == "debondt_thaler_1985"
    # The architectural claim: A1 extracted long_low cold
    assert "architectural_test_passed" in g
    if g.get("extracted_signal_direction") == "long_low":
        assert g["architectural_test_passed"] is True


# ---------------------------------------------------------------------------
# JSON-safe sanitization
# ---------------------------------------------------------------------------

def test_report_has_no_nan_or_inf(report):
    """JSON spec disallows NaN/Inf; browser JSON.parse rejects them. E1
    must convert all such values to None."""
    import math, json
    s = json.dumps(report, default=str)
    assert "NaN" not in s
    assert "Infinity" not in s
    assert "-Infinity" not in s
