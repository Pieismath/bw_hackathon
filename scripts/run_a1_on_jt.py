"""Run A1 (Methodology Extractor) on the JT 1993 PDF and report the spec.

Kept as a reproducible script so Phase 2 validation can be re-run on
prompt edits. Result also saved to outputs/jt_a1_extraction.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.extraction import extract_methodology
from src.pdf.parser import parse_pdf

JT_PDF = Path("data/papers/jegadeesh_titman_1993_momentum.pdf")
OUT = Path("outputs/jt_a1_extraction.json")


def main() -> None:
    print("Parsing JT PDF...", flush=True)
    pdf = parse_pdf(JT_PDF)
    print(f"  pages: {pdf.n_pages}", flush=True)

    print("Running A1 on JT (claude-opus-4-7; may take 30-60s first call)...", flush=True)
    spec = extract_methodology(pdf)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(spec.model_dump_json(indent=2))

    print()
    print("=" * 72)
    print(f"paper_id:    {spec.paper_id}")
    print(f"paper_title: {spec.paper_title}")
    print(f"dates:       {spec.start_date} → {spec.end_date}")
    print("=" * 72)

    print("\nUNIVERSE")
    print(f"  name:            {spec.universe.name}")
    print(f"  region:          {spec.universe.region}  asset_class: {spec.universe.asset_class}")
    print(f"  exchanges:       {spec.universe.exchanges}")
    print(f"  include_filters: {spec.universe.include_filters}")
    print(f"  exclude_filters: {spec.universe.exclude_filters}")
    print(f"  min_price:       {spec.universe.min_price}")
    if spec.universe.supporting_quote:
        q = spec.universe.supporting_quote
        print(f"  quote p.{q.page}: {q.text[:200]!r}{'…' if len(q.text) > 200 else ''}")

    print("\nSIGNAL")
    print(f"  name:           {spec.signal.name}")
    print(f"  kind:           {spec.signal.kind}")
    print(f"  formula:        {spec.signal.formula}")
    print(f"  lookback:       {spec.signal.lookback_months}  skip: {spec.signal.skip_months}")
    print(f"  direction:      {spec.signal.direction}  freq: {spec.signal.frequency}")
    if spec.signal.supporting_quote:
        q = spec.signal.supporting_quote
        print(f"  quote p.{q.page}: {q.text[:200]!r}{'…' if len(q.text) > 200 else ''}")

    print("\nPORTFOLIO")
    print(f"  construction:   {spec.portfolio.construction} n_buckets={spec.portfolio.n_buckets}")
    print(f"  long_bucket:    {spec.portfolio.long_bucket}  short_bucket: {spec.portfolio.short_bucket}")
    print(f"  weighting:      {spec.portfolio.weighting}  gross: {spec.portfolio.gross_exposure}")
    print(f"  long_short:     {spec.portfolio.long_short}  NYSE_bps: {spec.portfolio.use_nyse_breakpoints}")
    if spec.portfolio.supporting_quote:
        q = spec.portfolio.supporting_quote
        print(f"  quote p.{q.page}: {q.text[:200]!r}{'…' if len(q.text) > 200 else ''}")

    print("\nREBALANCE")
    print(f"  frequency:            {spec.rebalance.frequency}")
    print(f"  execution_lag_days:   {spec.rebalance.execution_lag_days}")
    print(f"  holding_period_months:{spec.rebalance.holding_period_months}")
    print(f"  signal_date / exec:   {spec.rebalance.signal_date_convention} / {spec.rebalance.execution_date_convention}")
    if spec.rebalance.supporting_quote:
        q = spec.rebalance.supporting_quote
        print(f"  quote p.{q.page}: {q.text[:200]!r}{'…' if len(q.text) > 200 else ''}")

    print(f"\nAMBIGUITIES ({len(spec.ambiguities)})")
    for a in spec.ambiguities:
        print(f"  [{a.sensitivity_priority:6s}] {a.parameter}")
        print(f"           default: {a.default_chosen!r}  alts: {list(a.alternatives)}")
        print(f"           reason: {a.reason}")

    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
