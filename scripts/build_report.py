"""Build outputs/report.json from the cached Phase 1-4 artifacts.

Run after any change to the underlying artifacts (rerun a scorecard,
update D2, etc.). The UI fetches outputs/report.json on page load.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.synthesis import build_report, write_report


def main() -> None:
    out_path = write_report("outputs", "report.json")
    payload = build_report("outputs")

    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    print()
    print("=" * 72)
    print("TOP-LEVEL STRUCTURE")
    print("=" * 72)
    for key in payload:
        v = payload[key]
        if isinstance(v, dict):
            print(f"  {key}: dict with {len(v)} keys: {list(v.keys())[:6]}"
                  f"{'...' if len(v) > 6 else ''}")
        elif isinstance(v, list):
            print(f"  {key}: list of {len(v)}")
        else:
            print(f"  {key}: {type(v).__name__} = {v!r:.80s}")

    if payload.get("meta", {}).get("missing_optional"):
        print()
        print("missing optional inputs:",
              payload["meta"]["missing_optional"])


if __name__ == "__main__":
    main()
