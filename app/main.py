"""MVP FastAPI backend for the Paper Replication Machine.

Exposes what Phase 1 and Phase 2 produce:
  - /api/demo                 — instant bundle (spec + verification + critique
                                + backtest) so the UI renders without needing
                                the Anthropic key or the parquet cache.
  - /api/papers               — list previously uploaded PDFs.
  - POST /api/papers          — upload a PDF (multipart).
  - POST /api/extract         — run A1 + A2 on an uploaded PDF.
  - POST /api/critique        — run A3 on an uploaded PDF (needs prior A1 cache).
  - POST /api/backtest        — run the engine on a supplied ReplicationSpec.
  - /                         — serves the static SPA (app/static/index.html).
"""

from __future__ import annotations

import json
import math
import traceback
from datetime import date
from pathlib import Path


def _sanitize_for_json(obj):
    """Recursively replace NaN / +/-Infinity with None.

    Starlette's JSONResponse uses stdlib json with allow_nan=False, so any
    NaN or Infinity (e.g. cost_threshold_bps=inf when alpha survives every
    cost level, or a NaN t-stat from a degenerate window) raises ValueError
    during response rendering. JSON's null is the cleanest substitute; the
    frontend's fmtNum already treats null as '—'.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.specs import ReplicationSpec

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
PAPERS_DIR = Path("data/papers")
OUTPUTS_DIR = Path("outputs")
PAPERS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Paper Replication Machine — MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _catchall_json_handler(request, exc):
    """Belt-and-suspenders: anything that escapes a per-handler try/except
    becomes a structured JSON 500 instead of starlette's default HTML page.
    The frontend's safeJson parser will then surface the real error in the
    Activity log rather than the literal string 'Internal Server Error'.
    """
    traceback.print_exc()
    tb = traceback.format_exc().splitlines()[-1]
    return JSONResponse(
        status_code=500,
        content={
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": tb,
            "hint": "Unhandled exception escaped the endpoint — check server console for the full traceback.",
        },
    )


# ---------------------------------------------------------------------------
# Demo endpoint — always works, no external deps.
# ---------------------------------------------------------------------------

@app.get("/api/demo")
def get_demo():
    from app.demo_fixture import build_demo_bundle
    return build_demo_bundle()


# ---------------------------------------------------------------------------
# Paper uploads
# ---------------------------------------------------------------------------

@app.get("/api/papers")
def list_papers():
    papers = []
    for p in sorted(PAPERS_DIR.glob("*.pdf")):
        stat = p.stat()
        papers.append({
            "paper_id": p.stem,
            "filename": p.name,
            "size_bytes": stat.st_size,
            "uploaded_at": stat.st_mtime,
        })
    return {"papers": papers}


@app.post("/api/papers")
async def upload_paper(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only .pdf files are supported")
    dest = PAPERS_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"paper_id": dest.stem, "filename": dest.name, "size_bytes": len(content)}


# ---------------------------------------------------------------------------
# Phase 2 — extraction + verification + critique
# ---------------------------------------------------------------------------

class PaperIdBody(BaseModel):
    paper_id: str
    use_cache: bool = True
    use_llm_check: bool = True


def _load_pdf(paper_id: str):
    pdf_path = PAPERS_DIR / f"{paper_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, f"paper {paper_id} not found")
    from src.pdf.parser import parse_pdf
    return parse_pdf(pdf_path)


@app.post("/api/extract")
def extract(body: PaperIdBody):
    """Run A1 (methodology extractor) + A2 (verifier) on an uploaded PDF.

    Requires ANTHROPIC_API_KEY unless the bundle is already cached on disk.
    Returns the same VerifiedReplicationSpec shape the demo bundle uses, plus
    a `paper_claim` projection of `verified.spec.headline_claim` (when A1
    extracted one) so the frontend can fire D2 without re-reading the spec.
    """
    try:
        from src.agents.extraction import extract_and_verify
        from app.demo_fixture import _bundle_paper_claim

        pdf = _load_pdf(body.paper_id)
        verified = extract_and_verify(
            pdf,
            max_retries=3,
            use_cache=body.use_cache,
            use_llm_check=body.use_llm_check,
        )
        return {
            "paper_id": body.paper_id,
            "paper_title": verified.spec.paper_title,
            "verified_spec": verified.model_dump(mode="json"),
            "paper_claim": _bundle_paper_claim(verified.spec.headline_claim),
            "n_pages": pdf.n_pages,
        }
    except HTTPException as he:
        return JSONResponse(
            status_code=he.status_code,
            content={"error": he.detail, "hint": "Upload the PDF again or pick an existing paper_id."},
        )
    except BaseException as e:
        traceback.print_exc()
        tb = traceback.format_exc().splitlines()[-1]
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "traceback_tail": tb,
                "hint": "Likely missing ANTHROPIC_API_KEY, network error, or PDF parse failure. Check server console.",
            },
        )


@app.post("/api/critique")
def critique(body: PaperIdBody):
    """Run A3 (adversarial reviewer) on an uploaded PDF. Requires ANTHROPIC_API_KEY."""
    try:
        from src.agents.extraction import extract_and_verify
        from src.agents.extraction.adversarial_reviewer import review

        pdf = _load_pdf(body.paper_id)
        verified = extract_and_verify(pdf, use_cache=body.use_cache)
        critique_obj = review(pdf, verified.spec, verified.report, use_cache=body.use_cache)
        return {
            "paper_id": body.paper_id,
            "critique": critique_obj.model_dump(mode="json"),
        }
    except HTTPException as he:
        return JSONResponse(
            status_code=he.status_code,
            content={"error": he.detail, "hint": "Upload the PDF again or pick an existing paper_id."},
        )
    except BaseException as e:
        traceback.print_exc()
        tb = traceback.format_exc().splitlines()[-1]
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "traceback_tail": tb,
                "hint": "Likely missing ANTHROPIC_API_KEY.",
            },
        )


# ---------------------------------------------------------------------------
# Phase 1 — backtest engine
# ---------------------------------------------------------------------------

def _clip_spec_to_data_window(spec: ReplicationSpec) -> tuple[ReplicationSpec, str | None, dict | None]:
    """If spec.start_date / end_date fall outside the parquet panel, clip them.

    A1 often extracts the paper's original window (e.g. 1965–1989 for JT-1993),
    but the defeatbeta panel only begins 1994-11-30. Running the engine with
    an out-of-range window raises "no price data in requested window".
    We peek at the prices parquet, intersect windows, and rebuild the spec
    with model_copy. Returns (possibly-clipped spec, human-readable note,
    structured window_info dict for the UI).
    """
    from datetime import date
    import duckdb

    prices_path = Path(
        "data/cache/hf_datasets/defeatbeta/datasets--defeatbeta--yahoo-finance-data/"
        "snapshots"
    )
    try:
        snap = next(p for p in prices_path.iterdir() if p.is_dir())
        parquet = snap / "data" / "stock_prices.parquet"
        con = duckdb.connect(":memory:")
        row = con.execute(
            f"SELECT MIN(report_date), MAX(report_date) FROM read_parquet('{parquet}')"
        ).fetchone()
        con.close()
        data_min = date.fromisoformat(row[0])
        data_max = date.fromisoformat(row[1])
    except Exception:
        return spec, None, None

    orig_start, orig_end = spec.start_date, spec.end_date
    new_start = max(orig_start, data_min)
    new_end = min(orig_end, data_max)
    window_info = {
        "paper_start": orig_start.isoformat(),
        "paper_end": orig_end.isoformat(),
        "data_panel_start": data_min.isoformat(),
        "data_panel_end": data_max.isoformat(),
        "engine_start": orig_start.isoformat(),
        "engine_end": orig_end.isoformat(),
        "substituted": False,
        "clipped": False,
        "overlap_kind": "in_range",
        "message": "",
    }

    if new_start >= new_end:
        # Paper's window is fully outside the panel (classic case: JT-1993 at
        # 1965–1989 vs. defeatbeta starting 1994-11-30). Substitute the
        # full panel so the pipeline still produces a result; downstream
        # tabs will surface this as a data_quality_flag.
        sub_start = date(max(data_min.year, 1995), 1, 1)
        sub_note = (
            f"original spec window {orig_start} → {orig_end} does not "
            f"overlap data panel {data_min} → {data_max}; substituting "
            f"{sub_start} → {data_max} (post-publication out-of-sample run)"
        )
        window_info.update(
            engine_start=sub_start.isoformat(),
            engine_end=data_max.isoformat(),
            substituted=True,
            overlap_kind="no_overlap_post_publication_oos",
            message=sub_note,
        )
        return (
            spec.model_copy(update={"start_date": sub_start, "end_date": data_max}),
            sub_note,
            window_info,
        )
    if new_start == orig_start and new_end == orig_end:
        return spec, None, window_info
    note = (
        f"clipped spec window from {orig_start} → {orig_end} to "
        f"{new_start} → {new_end} (data panel starts {data_min}, ends {data_max})"
    )
    window_info.update(
        engine_start=new_start.isoformat(),
        engine_end=new_end.isoformat(),
        clipped=True,
        overlap_kind="partial_overlap_clipped",
        message=note,
    )
    return (
        spec.model_copy(update={"start_date": new_start, "end_date": new_end}),
        note,
        window_info,
    )


class BacktestBody(BaseModel):
    spec: dict
    transaction_cost_bps: float = 0.0


@app.post("/api/backtest")
def backtest(body: BacktestBody):
    """Run the canonical backtest engine on a ReplicationSpec.

    Requires a local defeatbeta parquet cache at data/cache/hf_datasets/.
    Falls back to a clear error if data is unavailable.
    """
    try:
        from pydantic import ValidationError
        from src.data import DefeatBetaYahooSource, PointInTimeDataStore
        from src.engine import run_backtest

        try:
            spec = ReplicationSpec.model_validate(body.spec)
        except ValidationError as ve:
            return JSONResponse(
                status_code=422,
                content={
                    "error": f"Spec failed validation: {ve.errors()[0].get('msg', str(ve))}",
                    "hint": "The extracted or dialed spec has an illegal field combination (e.g. holding_period_months requires frequency='monthly'). Adjust the dial or re-run extraction.",
                },
            )

        cache_root = Path("data/cache/hf_datasets")
        if not cache_root.exists():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "parquet data cache not found at data/cache/hf_datasets/",
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        src_ = DefeatBetaYahooSource(cache_root=cache_root)
        store = PointInTimeDataStore(
            sources={"defeatbeta_yahoo": src_},
            cache_dir=Path("data/cache/query_cache"),
        )
        spec, clip_note, window_info = _clip_spec_to_data_window(spec)
        result = run_backtest(spec, store, transaction_cost_bps=body.transaction_cost_bps)
        src_.close()
        return _sanitize_for_json({
            "backtest": result.model_dump(mode="json"),
            "clip_note": clip_note,
            "window_info": window_info,
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "hint": "Engine execution failed; see server logs."},
        )


# ---------------------------------------------------------------------------
# Phase 4 — robustness battery + D3 judgment
# ---------------------------------------------------------------------------

class RobustnessBody(BaseModel):
    spec: dict
    families: list[str] | None = None
    transaction_cost_bps: float = 0.0


@app.post("/api/robustness")
def robustness(body: RobustnessBody):
    """Run the robustness battery + D3 (Robustness Adversary) judgment.

    Requires a local defeatbeta parquet cache at data/cache/hf_datasets/. The
    D3 judgment additionally requires ANTHROPIC_API_KEY (live LLM call).
    """
    try:
        from pydantic import ValidationError
        from src.agents.validation import judge, run_backtest_cached
        from src.data import DefeatBetaYahooSource, PointInTimeDataStore
        from src.robustness import run_battery
        from src.specs import PaperClaim, SupportingQuote

        try:
            spec = ReplicationSpec.model_validate(body.spec)
        except ValidationError as ve:
            return JSONResponse(
                status_code=422,
                content={
                    "error": f"Spec failed validation: {ve.errors()[0].get('msg', str(ve))}",
                    "hint": "The dialed spec has an illegal field combination. Adjust a dial or reload the demo.",
                },
            )

        cache_root = Path("data/cache/hf_datasets")
        if not cache_root.exists():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "parquet data cache not found at data/cache/hf_datasets/",
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        src_ = DefeatBetaYahooSource(cache_root=cache_root)
        store = PointInTimeDataStore(
            sources={"defeatbeta_yahoo": src_},
            cache_dir=Path("data/cache/query_cache"),
        )
        spec, clip_note, window_info = _clip_spec_to_data_window(spec)
        scorecard = run_battery(spec, store, families=body.families)
        baseline = run_backtest_cached(
            spec, store, transaction_cost_bps=body.transaction_cost_bps
        )
        # D3 needs a PaperClaim for context. If the caller didn't supply one
        # (the body schema doesn't model it), fabricate a minimal placeholder
        # so the judgment can still ground its narrative in the scorecard.
        placeholder_claim = PaperClaim(
            claim_id="placeholder_headline",
            metric="monthly_long_short_return",
            claimed_value=baseline.mean_return,
            claimed_tstat=baseline.alpha_tstat,
            claimed_units="decimal_per_month",
            paper_location="caller_did_not_supply_claim",
            supporting_quote=SupportingQuote(
                text="placeholder claim — caller did not supply paper claim",
                page=1,
                verified=False,
                match_confidence=0.0,
            ),
        )
        # D3 LLM call. Retry once on Anthropic 529 (overload); if it still
        # fails, return the scorecard alone so the user keeps the 90s of
        # battery work — the deterministic part is what costs time, the LLM
        # narrative is a wrapper.
        from anthropic import APIStatusError
        judgment = None
        judgment_error = None
        for attempt in (1, 2):
            try:
                judgment = judge(scorecard, baseline, placeholder_claim)
                break
            except APIStatusError as ae:
                if attempt == 1 and getattr(ae, "status_code", None) in (429, 529):
                    import time
                    time.sleep(20)
                    continue
                judgment_error = f"D3 LLM unavailable: {type(ae).__name__} {getattr(ae, 'status_code', '?')}"
                break
        src_.close()
        return _sanitize_for_json({
            "scorecard": scorecard.model_dump(mode="json"),
            "judgment": judgment.model_dump(mode="json") if judgment else None,
            "judgment_error": judgment_error,
            "clip_note": clip_note,
            "window_info": window_info,
        })
    except BaseException as e:
        traceback.print_exc()
        tb = traceback.format_exc().splitlines()[-1]
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "traceback_tail": tb,
                "hint": "Likely missing ANTHROPIC_API_KEY (D3 LLM call), missing parquet cache, or engine failure. Check server console.",
            },
        )


# ---------------------------------------------------------------------------
# Phase 4 — D2 divergence diagnosis
# ---------------------------------------------------------------------------

class DiagnoseBody(BaseModel):
    spec: dict
    paper_claim_monthly_return: float
    paper_claim_tstat: float | None = None
    max_experiments: int | None = None
    transaction_cost_bps: float = 0.0


@app.post("/api/diagnose")
def diagnose_endpoint(body: DiagnoseBody):
    """Run D2 (Divergence Diagnostician) on a paper-vs-replication gap.

    Requires a local defeatbeta parquet cache at data/cache/hf_datasets/ and
    ANTHROPIC_API_KEY (D2 makes proposer + summarizer LLM calls).
    """
    try:
        from pydantic import ValidationError
        from src.agents.validation import (
            MAX_EXPERIMENTS_DEFAULT,
            diagnose,
            run_backtest_cached,
        )
        from src.data import DefeatBetaYahooSource, PointInTimeDataStore
        from src.specs import PaperClaim, SupportingQuote

        try:
            spec = ReplicationSpec.model_validate(body.spec)
        except ValidationError as ve:
            return JSONResponse(
                status_code=422,
                content={
                    "error": f"Spec failed validation: {ve.errors()[0].get('msg', str(ve))}",
                    "hint": "The dialed spec has an illegal field combination. Adjust a dial or reload the demo.",
                },
            )

        cache_root = Path("data/cache/hf_datasets")
        if not cache_root.exists():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "parquet data cache not found at data/cache/hf_datasets/",
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        src_ = DefeatBetaYahooSource(cache_root=cache_root)
        store = PointInTimeDataStore(
            sources={"defeatbeta_yahoo": src_},
            cache_dir=Path("data/cache/query_cache"),
        )
        spec, clip_note, window_info = _clip_spec_to_data_window(spec)
        baseline = run_backtest_cached(
            spec, store, transaction_cost_bps=body.transaction_cost_bps
        )
        claim = PaperClaim(
            claim_id="caller_supplied_headline",
            metric="monthly_long_short_return",
            claimed_value=body.paper_claim_monthly_return,
            claimed_tstat=body.paper_claim_tstat,
            claimed_units="decimal_per_month",
            paper_location="caller_supplied",
            supporting_quote=SupportingQuote(
                text="caller-supplied paper claim — no quote provided via API",
                page=1,
                verified=False,
                match_confidence=0.0,
            ),
        )
        max_exp = (
            body.max_experiments
            if body.max_experiments is not None
            else MAX_EXPERIMENTS_DEFAULT
        )
        # D2 makes proposer + summarizer LLM calls; retry once on Anthropic
        # 529. D2 itself already early-exits gracefully on intra-loop
        # OverloadedError (sets diagnosis.early_exit=True), so the only
        # failure mode here is the *first* LLM call hitting overload.
        from anthropic import APIStatusError
        diagnosis = None
        diagnosis_error = None
        for attempt in (1, 2):
            try:
                diagnosis = diagnose(
                    spec=spec,
                    baseline_result=baseline,
                    claim=claim,
                    store=store,
                    max_experiments=max_exp,
                    transaction_cost_bps=body.transaction_cost_bps,
                )
                break
            except APIStatusError as ae:
                if attempt == 1 and getattr(ae, "status_code", None) in (429, 529):
                    import time
                    time.sleep(20)
                    continue
                diagnosis_error = f"D2 LLM unavailable: {type(ae).__name__} {getattr(ae, 'status_code', '?')}"
                break
        src_.close()
        if diagnosis is None:
            return _sanitize_for_json({
                "diagnosis": None,
                "n_experiments": 0,
                "diagnosis_error": diagnosis_error,
                "clip_note": clip_note,
                "window_info": window_info,
            })
        return _sanitize_for_json({
            "diagnosis": diagnosis.model_dump(mode="json"),
            "n_experiments": diagnosis.experiments_run,
            "clip_note": clip_note,
            "window_info": window_info,
        })
    except BaseException as e:
        traceback.print_exc()
        tb = traceback.format_exc().splitlines()[-1]
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "traceback_tail": tb,
                "hint": "Likely missing ANTHROPIC_API_KEY, missing parquet cache, or engine failure on a mutated spec. Check server console.",
            },
        )
# ---------------------------------------------------------------------------
# Phase 5 — consolidated report (E1 aggregator)
# ---------------------------------------------------------------------------

REPORT_PATH = OUTPUTS_DIR / "report.json"

# Build once on startup if cached artifacts are present; rebuild on demand
# via /api/report/regenerate. The aggregator is pure-Python and finishes
# in <100ms, so we accept the rebuild cost for fresh demos.
def _build_report_if_possible() -> dict | None:
    try:
        from src.agents.synthesis import build_report
        return build_report(OUTPUTS_DIR)
    except FileNotFoundError:
        return None
    except Exception:
        traceback.print_exc()
        return None


@app.get("/api/report")
def get_report():
    """Return the consolidated Phase 1-4 report payload built by E1.

    Builds in-memory on each request (cheap, deterministic). The on-disk
    `outputs/report.json` is also kept in sync so the static fallback in
    the SPA can read it directly without going through FastAPI."""
    payload = _build_report_if_possible()
    if payload is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "report unavailable",
                "hint": (
                    "Run scripts/run_jt_full_pipeline.py and "
                    "scripts/run_phase4_postfix.py to populate outputs/, "
                    "then GET /api/report/regenerate."
                ),
            },
        )
    # Also persist to disk for offline / cached SPA use.
    try:
        REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    except OSError:
        pass
    return JSONResponse(content=payload)


@app.post("/api/report/regenerate")
def regenerate_report():
    """Force-rebuild outputs/report.json from current cached artifacts.

    Used when the underlying outputs/*.json have been refreshed (e.g.,
    after rerunning the battery on a tweaked spec). Frontend can then
    re-fetch /api/report.
    """
    payload = _build_report_if_possible()
    if payload is None:
        return JSONResponse(
            status_code=503,
            content={"error": "report unavailable", "hint": "outputs/ artifacts missing"},
        )
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str))
    return {
        "ok": True,
        "bytes": REPORT_PATH.stat().st_size,
        "path": str(REPORT_PATH),
    }


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # Also serve outputs/report.json directly so the SPA can fall back to
    # static fetch (handy for `python -m http.server` outside FastAPI).
    if OUTPUTS_DIR.exists():
        app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")


@app.get("/")
def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(status_code=500, content={"error": "frontend not built"})
    return FileResponse(idx)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
