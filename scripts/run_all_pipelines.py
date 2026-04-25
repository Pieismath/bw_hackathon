"""Run _run_pipeline on each PDF in data/papers and dump bundles for audit.

Synchronous wrapper around app.main._run_pipeline so we can iterate over
every paper, capture the resulting bundle, and inspect for issues. Uses
existing LLM cache (use_cache=True everywhere inside _run_pipeline) so
no new API calls are needed when entries are present.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.main import _run_pipeline, PIPELINE_JOBS, PIPELINE_LOCK  # noqa: E402

PAPERS_DIR = REPO / "data" / "papers"
OUT_DIR = REPO / "outputs" / "audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    pdfs = sorted(PAPERS_DIR.glob("*.pdf"))
    print(f"Found {len(pdfs)} papers", flush=True)

    selected = sys.argv[1:] if len(sys.argv) > 1 else [p.stem for p in pdfs]

    for stem in selected:
        print(f"\n{'='*72}\n{stem}\n{'='*72}", flush=True)
        job_id = f"audit_{stem[:20]}"
        with PIPELINE_LOCK:
            PIPELINE_JOBS[job_id] = {
                "job_id": job_id,
                "paper_id": stem,
                "stage": "parse",
                "status": "active",
                "done": False,
                "error": None,
            }
        try:
            _run_pipeline(job_id, stem)
        except BaseException as e:
            print(f"  TOPLEVEL EXCEPTION: {type(e).__name__}: {e}")

        job = PIPELINE_JOBS.get(job_id, {})
        out = OUT_DIR / f"{stem}.json"
        out.write_text(json.dumps(job, indent=2, default=str))
        status = "OK" if job.get("status") == "succeeded" else "FAIL"
        err = job.get("error") or ""
        print(f"  -> {status}  stage={job.get('stage')}  err={err}")
        print(f"  -> wrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
