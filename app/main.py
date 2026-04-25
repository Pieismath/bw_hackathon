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

def _engine_weighting_fallback(spec: ReplicationSpec) -> tuple[ReplicationSpec, str | None]:
    """Substitute portfolio.weighting when the engine doesn't implement it.

    Today src/engine/portfolio.py supports `equal` and `value`. Papers that
    A1 extracts as `signal_weighted` (proportional-to-prediction-strength)
    raise NotImplementedError at the engine. Substitute `equal` so the
    pipeline completes; flag the swap so the verdict strip discloses it.
    """
    w = spec.portfolio.weighting
    if w in ("equal", "value"):
        return spec, None
    flag = (
        f"engine fallback: portfolio.weighting='{w}' is not yet implemented "
        f"in the canonical engine; substituting 'equal' as a structured proxy. "
        f"The replicated weights are NOT proportional to the paper's signal — "
        f"treat the headline number accordingly."
    )
    new_pf = spec.portfolio.model_copy(update={"weighting": "equal"})
    return spec.model_copy(update={"portfolio": new_pf}), flag


def _engine_lookback_clamp(
    spec: ReplicationSpec, window_info: dict | None
) -> tuple[ReplicationSpec, str | None]:
    """Clamp `signal.lookback_months` if it's so large vs. the available data
    panel that the engine would produce zero signal observations.

    The variance_ratio signal needs `lookback_months + skip_months + 12`
    months of history strictly before the first formation date can fire
    (see `_compute_variance_ratio` in src/engine/signals.py). The
    past_return signal needs `lookback_months + skip_months`. When the
    window between the data panel's start and `spec.end_date` is shorter
    than that, every formation returns an empty signal and the engine
    raises `RuntimeError("no return observations produced")`.

    We clamp `lookback_months` so the FIRST formation date inside the
    spec window can fire — leaving at least `MIN_USABLE_OBS=24` months
    of usable signal between burn-in completion and `end_date`. Floor at
    24 (the variance_ratio validator minimum). Flag the substitution so
    the UI banner discloses it. This guarantees the engine returns at
    least *something* on long-lookback specs that A1 sometimes proposes
    (e.g. AQR streaks with `lookback_months=360`).
    """
    sig = spec.signal
    if sig.lookback_months is None or window_info is None:
        return spec, None
    if sig.kind not in ("past_return", "variance_ratio"):
        return spec, None
    from datetime import date as _date

    panel_start_iso = window_info.get("data_panel_start")
    if not panel_start_iso:
        return spec, None
    panel_start = _date.fromisoformat(panel_start_iso)
    end_date = spec.end_date

    available_months = (end_date.year - panel_start.year) * 12 + (
        end_date.month - panel_start.month
    )
    extra_burnin = 12 if sig.kind == "variance_ratio" else 0
    skip = sig.skip_months or 0
    needed = sig.lookback_months + skip + extra_burnin
    MIN_USABLE_OBS = 24
    LB_FLOOR = 24

    if available_months >= needed + MIN_USABLE_OBS:
        return spec, None  # already fits

    new_lookback = max(available_months - skip - extra_burnin - MIN_USABLE_OBS, LB_FLOOR)
    if new_lookback >= sig.lookback_months:
        return spec, None  # nothing to clamp
    flag = (
        f"engine fallback: signal.lookback_months={sig.lookback_months} "
        f"({sig.kind}) requires {needed}m of pre-formation history but the "
        f"data panel only spans {available_months}m before spec.end_date "
        f"({end_date}). Clamped to {new_lookback}m so the engine produces "
        f"≥{MIN_USABLE_OBS} signal observations. The signal definition is "
        f"the same; the lookback window is shorter (noisier estimate) — "
        f"treat the headline number as concept-faithful, not parameter-"
        f"faithful to the paper."
    )
    new_signal = sig.model_copy(update={"lookback_months": new_lookback})
    return spec.model_copy(update={"signal": new_signal}), flag


def _engine_kind_fallback(spec: ReplicationSpec) -> tuple[ReplicationSpec, str | None]:
    """If `signal.kind` is one the engine can't run today, substitute the
    closest structured kind that lets the pipeline complete.

    Today the engine only implements `past_return`. Papers whose A1 emits
    `custom` (e.g. transformer-output, learned-model signals) or
    `fundamental_ratio` would otherwise stall at the engine stage and leave
    the Backtest / Robustness / Diagnosis tabs empty. We substitute
    `kind='past_return'` keeping the original lookback/skip/direction —
    a structured proxy that respects the paper's window and direction even
    if it can't reproduce the model output. The substitution is flagged via
    a data_quality_flag so the UI banner / scorecards make the swap honest.

    Returns (possibly-substituted spec, flag message or None).
    """
    sig = spec.signal
    if sig.kind == "past_return":
        if sig.lookback_months is None:
            # Spec validation normally forbids this, but a model_copy elsewhere
            # could have produced it. Fall back to 12-month momentum.
            new_signal = sig.model_copy(update={"lookback_months": 12})
            flag = (
                "engine fallback: signal.kind='past_return' had lookback_months=None; "
                "defaulting to 12-month momentum as a structured proxy."
            )
            return spec.model_copy(update={"signal": new_signal}), flag
        return spec, None
    if sig.kind == "variance_ratio":
        # variance_ratio is natively implemented in src/engine/signals.py
        # — no proxy substitution. Default lookback to 60 months (5 years
        # = 49 overlapping 12-month observations) when A1 didn't supply
        # one; the spec validator requires >= 24 so we pick a value safely
        # above the floor.
        if sig.lookback_months is None or sig.lookback_months < 24:
            new_signal = sig.model_copy(update={"lookback_months": 60})
            flag = (
                "engine fallback: signal.kind='variance_ratio' had "
                f"lookback_months={sig.lookback_months}; defaulting to 60 "
                "(5-year window, 49 rolling-12m observations) — sufficient "
                "for stable variance estimation."
            )
            return spec.model_copy(update={"signal": new_signal}), flag
        return spec, None
    # Non-past_return kinds (custom, fundamental_ratio): substitute a
    # past_return proxy. Many learned/custom signals don't carry a months
    # lookback at all (daily streaks, transformer outputs); when missing,
    # default to 12 months — the most common momentum window in the
    # cross-section literature — and let the data_quality_flag make the
    # substitution explicit.
    proxy_lookback = sig.lookback_months if sig.lookback_months is not None else 12
    flag = (
        f"engine fallback: signal.kind='{sig.kind}' is not yet implemented in "
        f"the canonical engine; substituting past_return with "
        f"lookback_months={proxy_lookback}"
        f"{' (default — paper did not specify a months window)' if sig.lookback_months is None else ''}, "
        f"skip_months={sig.skip_months}, direction={sig.direction} as a "
        f"structured proxy. The replicated strategy is NOT the paper's exact "
        f"signal — treat the headline number as a coverage-shaped lower "
        f"bound, not a like-for-like number."
    )
    new_signal = sig.model_copy(
        update={"kind": "past_return", "lookback_months": proxy_lookback}
    )
    return spec.model_copy(update={"signal": new_signal}), flag


def _flag_window_substitution(result, window_info: dict | None):
    """Append the date-substitution note to BacktestResult.data_quality_flags
    so any downstream consumer (UI banner, robustness, D2, exported reports)
    sees the substitution without having to read clip_note/window_info
    separately. Idempotent: returns the result unchanged if no substitution
    or clip happened.
    """
    if not window_info or not (window_info.get("substituted") or window_info.get("clipped")):
        return result
    msg = window_info.get("message") or ""
    flags = list(getattr(result, "data_quality_flags", ()) or ())
    if msg and msg not in flags:
        flags.append(msg)
    return result.model_copy(update={"data_quality_flags": tuple(flags)})


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


def _build_store_for_spec(spec: ReplicationSpec):
    """Choose the data store implementation based on `spec.universe.name`.

    AQR-style factor papers (variance_ratio + factor universe) need the
    Ken French factor library, which goes back to 1926-07 and has no
    survivorship bias. Stock-level papers stay on defeatbeta_yahoo.

    Returns (store, store_kind) where store_kind is "ken_french_factors"
    or "defeatbeta" so callers can adjust window-clip ranges and error
    messages. Raises HTTPException via the caller's try/except if the
    requested data isn't available locally.
    """
    from src.factor_compare.store import (
        KEN_FRENCH_UNIVERSE_NAME,
        KenFrenchFactorStore,
    )
    if spec.universe.name == KEN_FRENCH_UNIVERSE_NAME:
        return KenFrenchFactorStore(), "ken_french_factors"
    from src.data import DefeatBetaYahooSource, PointInTimeDataStore
    cache_root = Path("data/cache/hf_datasets")
    if not cache_root.exists():
        raise FileNotFoundError(
            "parquet data cache not found at data/cache/hf_datasets/"
        )
    src_ = DefeatBetaYahooSource(cache_root=cache_root)
    store = PointInTimeDataStore(
        sources={"defeatbeta_yahoo": src_},
        cache_dir=Path("data/cache/query_cache"),
    )
    return store, "defeatbeta"


def _clip_spec_for_kf(spec: ReplicationSpec) -> tuple[ReplicationSpec, str | None, dict | None]:
    """Window-clip variant for ken_french_factors universes. The KF panel
    starts 1963-07 (FF5 inception — RMW/CMA need it) and runs to the
    present, so most paper windows fit. We still clip when end_date
    exceeds the latest available factor data.
    """
    from datetime import date as _date
    # Inception of the joint 6-factor panel: FF5 RMW/CMA daily start
    # 1963-07-01. Mom and FF3 go back further but the rectangular panel
    # is bound by the latest-starting factor.
    data_min = _date(1963, 7, 31)
    data_max = _date.today()
    orig_start, orig_end = spec.start_date, spec.end_date
    new_start = max(orig_start, data_min)
    new_end = min(orig_end, data_max)
    window_info = {
        "paper_start": orig_start.isoformat(),
        "paper_end": orig_end.isoformat(),
        "data_panel_start": data_min.isoformat(),
        "data_panel_end": data_max.isoformat(),
        "engine_start": new_start.isoformat(),
        "engine_end": new_end.isoformat(),
        "substituted": False,
        "clipped": new_start != orig_start or new_end != orig_end,
        "overlap_kind": "in_range" if new_start == orig_start and new_end == orig_end else "partial_overlap_clipped",
        "message": "",
    }
    if new_start >= new_end:
        # Paper window entirely outside KF panel — substitute the full panel.
        window_info.update(
            engine_start=data_min.isoformat(),
            engine_end=data_max.isoformat(),
            substituted=True,
            overlap_kind="no_overlap_post_publication_oos",
            message=(
                f"original spec window {orig_start} → {orig_end} does not "
                f"overlap KF factor panel {data_min} → {data_max}; "
                f"substituting full panel"
            ),
        )
        return (
            spec.model_copy(update={"start_date": data_min, "end_date": data_max}),
            window_info["message"],
            window_info,
        )
    if not window_info["clipped"]:
        return spec, None, window_info
    note = (
        f"clipped spec window from {orig_start} → {orig_end} to "
        f"{new_start} → {new_end} (KF factor panel: {data_min} → {data_max})"
    )
    window_info["message"] = note
    return (
        spec.model_copy(update={"start_date": new_start, "end_date": new_end}),
        note,
        window_info,
    )


def _prepare_spec_window(spec: ReplicationSpec, store_kind: str) -> tuple[ReplicationSpec, str | None, dict | None]:
    """Dispatch to the right window-clip helper for the chosen store."""
    if store_kind == "ken_french_factors":
        return _clip_spec_for_kf(spec)
    return _clip_spec_to_data_window(spec)


def _coerce_portfolio_for_factor_universe(spec: ReplicationSpec) -> tuple[ReplicationSpec, str | None]:
    """The Ken French factor universe has only 6 names — pd.qcut can't
    form quintiles or deciles on a 6-element cross-section. Coerce the
    portfolio to a tercile sort (n_buckets=3) when the spec targets the
    KF universe but A1 / the dials chose a larger bucket count.

    Equal weighting is also enforced because factor portfolios don't
    have market caps; value-weighting them is undefined. The KF store
    will raise on get_market_cap_panel anyway, but we coerce here so
    the failure mode is a flag rather than an exception.
    """
    if spec.universe.name != "ken_french_factors":
        return spec, None
    p = spec.portfolio
    changes: list[str] = []
    new_p = p
    if p.n_buckets > 3:
        new_p = new_p.model_copy(update={
            "n_buckets": 3,
            "construction": "tercile",
            "long_bucket": 3,
            "short_bucket": 1 if p.long_short else None,
        })
        changes.append(f"n_buckets {p.n_buckets} → 3 (tercile)")
    if p.weighting == "value":
        new_p = new_p.model_copy(update={"weighting": "equal"})
        changes.append("weighting value → equal (factor portfolios have no market cap)")
    if not changes:
        return spec, None
    flag = (
        "ken_french_factors universe has 6 names; portfolio coerced — "
        + "; ".join(changes)
        + ". Honest framing: AQR sorts 153 JKP factors into terciles (~50 "
        "per bucket); we sort 6 KF factors into terciles (2 per bucket). "
        "Concept-faithful, statistical-power-faithful is not."
    )
    return spec.model_copy(update={"portfolio": new_p}), flag


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

        try:
            store, store_kind = _build_store_for_spec(spec)
        except FileNotFoundError as e:
            return JSONResponse(
                status_code=503,
                content={
                    "error": str(e),
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        spec, clip_note, window_info = _prepare_spec_window(spec, store_kind)
        spec, kind_fallback_msg = _engine_kind_fallback(spec)
        spec, lookback_clamp_msg = _engine_lookback_clamp(spec, window_info)
        spec, portfolio_coercion_msg = _coerce_portfolio_for_factor_universe(spec)
        result = run_backtest(spec, store, transaction_cost_bps=body.transaction_cost_bps)
        result = _flag_window_substitution(result, window_info)
        extra_flags = [m for m in (kind_fallback_msg, lookback_clamp_msg, portfolio_coercion_msg) if m]
        if extra_flags:
            flags = list(result.data_quality_flags) + extra_flags
            result = result.model_copy(update={"data_quality_flags": tuple(flags)})
        store.close()
        return _sanitize_for_json({
            "backtest": result.model_dump(mode="json"),
            "clip_note": clip_note,
            "window_info": window_info,
            "kind_fallback": kind_fallback_msg,
            "store_kind": store_kind,
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

        try:
            store, store_kind = _build_store_for_spec(spec)
        except FileNotFoundError as e:
            return JSONResponse(
                status_code=503,
                content={
                    "error": str(e),
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        spec, clip_note, window_info = _prepare_spec_window(spec, store_kind)
        spec, kind_fallback_msg = _engine_kind_fallback(spec)
        spec, lookback_clamp_msg = _engine_lookback_clamp(spec, window_info)
        spec, portfolio_coercion_msg = _coerce_portfolio_for_factor_universe(spec)
        scorecard = run_battery(spec, store, families=body.families)
        baseline = run_backtest_cached(
            spec, store, transaction_cost_bps=body.transaction_cost_bps
        )
        baseline = _flag_window_substitution(baseline, window_info)
        extra_flags = [m for m in (kind_fallback_msg, lookback_clamp_msg, portfolio_coercion_msg) if m]
        if extra_flags:
            flags = list(baseline.data_quality_flags) + extra_flags
            baseline = baseline.model_copy(update={"data_quality_flags": tuple(flags)})
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
        store.close()
        return _sanitize_for_json({
            "scorecard": scorecard.model_dump(mode="json"),
            "judgment": judgment.model_dump(mode="json") if judgment else None,
            "judgment_error": judgment_error,
            "clip_note": clip_note,
            "window_info": window_info,
            "kind_fallback": kind_fallback_msg,
            "store_kind": store_kind,
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
# Factor comparison — Ken French realized factor vs paper claim
# ---------------------------------------------------------------------------

class FactorCompareBody(BaseModel):
    spec: dict
    headline_claim: dict | None = None  # optional HeadlineClaim payload


@app.post("/api/factor_compare")
def factor_compare(body: FactorCompareBody):
    """Compute the realized Ken French factor return over the paper's window
    and compare against the paper's claimed headline.

    Pure deterministic computation: reads CSVs from data/ken-french/. No
    LLM, no engine reruns, no parquet cache required. Always succeeds —
    `verdict` indicates whether a clean factor mapping + window overlap
    were possible.
    """
    try:
        from src.factor_compare import compare_to_kf_factor
        from src.specs import HeadlineClaim

        spec = ReplicationSpec.model_validate(body.spec)
        claim = (
            HeadlineClaim.model_validate(body.headline_claim)
            if body.headline_claim
            else None
        )
        if claim is None and getattr(spec, "headline_claim", None) is not None:
            claim = spec.headline_claim
        comparison = compare_to_kf_factor(spec, claim)
        return _sanitize_for_json({"comparison": comparison.model_dump(mode="json")})
    except BaseException as e:
        traceback.print_exc()
        tb = traceback.format_exc().splitlines()[-1]
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "traceback_tail": tb,
                "hint": "KF CSVs may be missing from data/ken-french/, or the spec failed validation.",
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

        try:
            store, store_kind = _build_store_for_spec(spec)
        except FileNotFoundError as e:
            return JSONResponse(
                status_code=503,
                content={
                    "error": str(e),
                    "hint": "Run scripts/pull_defeatbeta.py to populate it, or use /api/demo.",
                },
            )

        spec, clip_note, window_info = _prepare_spec_window(spec, store_kind)
        spec, kind_fallback_msg = _engine_kind_fallback(spec)
        spec, lookback_clamp_msg = _engine_lookback_clamp(spec, window_info)
        spec, portfolio_coercion_msg = _coerce_portfolio_for_factor_universe(spec)
        baseline = run_backtest_cached(
            spec, store, transaction_cost_bps=body.transaction_cost_bps
        )
        baseline = _flag_window_substitution(baseline, window_info)
        extra_flags = [m for m in (kind_fallback_msg, lookback_clamp_msg, portfolio_coercion_msg) if m]
        if extra_flags:
            flags = list(baseline.data_quality_flags) + extra_flags
            baseline = baseline.model_copy(update={"data_quality_flags": tuple(flags)})
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
        store.close()
        if diagnosis is None:
            return _sanitize_for_json({
                "diagnosis": None,
                "n_experiments": 0,
                "diagnosis_error": diagnosis_error,
                "clip_note": clip_note,
                "window_info": window_info,
                "kind_fallback": kind_fallback_msg,
            })
        return _sanitize_for_json({
            "diagnosis": diagnosis.model_dump(mode="json"),
            "n_experiments": diagnosis.experiments_run,
            "clip_note": clip_note,
            "window_info": window_info,
            "kind_fallback": kind_fallback_msg,
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
# Phase 5 polish — pipeline job tracking
#   /api/pipeline/start   POST {paper_id} → {job_id}
#   /api/pipeline/status/{job_id}  GET → {stage, status, done, error?}
# Frontend stepper polls /status until done. The pipeline runs in a worker
# thread; the registry lives in-process (single uvicorn worker).
# ---------------------------------------------------------------------------

import threading
import uuid
from datetime import datetime, timezone

PIPELINE_JOBS: dict[str, dict] = {}
PIPELINE_LOCK = threading.Lock()


def _set_stage(job_id: str, stage: str, status: str = "active") -> None:
    with PIPELINE_LOCK:
        if job_id not in PIPELINE_JOBS:
            return
        PIPELINE_JOBS[job_id].update(
            stage=stage,
            status=status,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


def _set_partial(job_id: str, bundle: dict) -> None:
    """Stash the in-progress bundle so the SPA can render each stage's
    output as soon as it's available — rather than waiting for the whole
    pipeline. The frontend's poll loop reads `result` on every status hit.
    """
    with PIPELINE_LOCK:
        if job_id not in PIPELINE_JOBS:
            return
        PIPELINE_JOBS[job_id]["result"] = _sanitize_for_json(dict(bundle))


def _finish_job(job_id: str, *, error: str | None = None, result: dict | None = None) -> None:
    with PIPELINE_LOCK:
        if job_id not in PIPELINE_JOBS:
            return
        PIPELINE_JOBS[job_id].update(
            done=True,
            status="failed" if error else "succeeded",
            error=error,
            result=result,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def _run_pipeline(job_id: str, paper_id: str) -> None:
    """Background worker: PDF → A1 → A2 → A3 → B1+B2 → engine → D2 → battery → D3.

    Every agent stage transition is reflected in PIPELINE_JOBS[job_id]['stage']
    so the frontend stepper updates in real time. Each stage that fails marks
    the job done with the error message and stops; downstream stages are
    skipped. On success, the live results are stowed in
    PIPELINE_JOBS[job_id]['result'] as a bundle the SPA can render directly
    via applyBundle() — bypassing the static outputs/jt_*.json report aggregator.
    """
    bundle: dict = {"paper_id": paper_id, "paper_claim": None}
    try:
        from src.agents.extraction import (
            extract_and_verify, fold_high_severity_into_spec, review,
        )
        from src.agents.implementation import map_data, verify_mapping
        from src.agents.validation import diagnose, judge
        from src.agents.validation.divergence_diagnostician import run_backtest_cached
        from src.data import DefeatBetaYahooSource, PointInTimeDataStore
        from src.robustness import run_battery
        from src.specs import PaperClaim, SupportingQuote
        from app.demo_fixture import _bundle_paper_claim

        _set_stage(job_id, "parse", "active")
        pdf = _load_pdf(paper_id)
        _set_stage(job_id, "parse", "done")

        _set_stage(job_id, "a1", "active")
        verified = extract_and_verify(pdf, max_retries=3, use_cache=True)
        spec = verified.spec
        bundle["paper_title"] = spec.paper_title
        bundle["verified_spec"] = verified.model_dump(mode="json")
        # If A1 extracted a headline claim, surface it on the bundle so the
        # verdict strip's left column ("PAPER CLAIMED +x%/mo") populates on the
        # first run, not just after the user clicks "Load demo" and the static
        # JT report's claim repopulates by side-effect.
        bundle["paper_claim"] = _bundle_paper_claim(spec.headline_claim)
        _set_stage(job_id, "a1", "done")
        _set_partial(job_id, bundle)

        _set_stage(job_id, "a2", "done")  # A2 is folded into extract_and_verify

        _set_stage(job_id, "a3", "active")
        critique = review(pdf, spec, verification_report=verified.report, use_cache=True)
        spec_with_critique = fold_high_severity_into_spec(spec, critique)
        bundle["critique"] = critique.model_dump(mode="json")
        _set_stage(job_id, "a3", "done")
        _set_partial(job_id, bundle)

        _set_stage(job_id, "b", "active")
        mapping = map_data(spec_with_critique, use_cache=True)
        verified_mapping = verify_mapping(
            mapping, spec_with_critique, use_llm_check=True, use_cache=True,
        )
        bundle["mapping"] = verified_mapping.model_dump(mode="json")
        _set_stage(job_id, "b", "done")
        _set_partial(job_id, bundle)

        # Dispatch to the right data store BEFORE clipping / fallbacks so
        # KF-factor papers (universe.name='ken_french_factors') don't get
        # wrongly routed through the defeatbeta panel — that mismatch was
        # silently turning the AQR streaks pipeline into a no-op
        # (panel start 1994-11 vs spec start 1973-01 ⇒ window substitution
        # to 1995-2026, then signal lookup against the wrong universe ⇒
        # zero return observations).
        store, store_kind = _build_store_for_spec(spec_with_critique)
        clipped, _clip_note, window_info = _prepare_spec_window(
            spec_with_critique, store_kind
        )
        bundle["window_info"] = window_info
        # Substitute signal.kind / portfolio.weighting if the engine can't run
        # them (e.g. 'custom' / 'signal_weighted') so the rest of the pipeline
        # (battery, D2, D3) still produces output the SPA can render. The
        # structured-proxy substitution(s) are flagged below.
        clipped, kind_fallback_msg = _engine_kind_fallback(clipped)
        clipped, lookback_clamp_msg = _engine_lookback_clamp(clipped, window_info)
        clipped, weighting_fallback_msg = _engine_weighting_fallback(clipped)
        clipped, portfolio_coercion_msg = _coerce_portfolio_for_factor_universe(clipped)
        # Track the store for finally-block close (only DefeatBeta needs it)
        src = getattr(store, "_defeatbeta_src", None)
        try:
            _set_stage(job_id, "engine", "active")
            baseline = run_backtest_cached(clipped, store, transaction_cost_bps=0.0)
            baseline = _flag_window_substitution(baseline, window_info)
            extra_flags = [
                m for m in (
                    kind_fallback_msg, lookback_clamp_msg,
                    weighting_fallback_msg, portfolio_coercion_msg,
                ) if m
            ]
            if extra_flags:
                flags = list(baseline.data_quality_flags) + extra_flags
                baseline = baseline.model_copy(update={"data_quality_flags": tuple(flags)})
            bundle["backtest"] = baseline.model_dump(mode="json")
            _set_stage(job_id, "engine", "done")
            _set_partial(job_id, bundle)

            placeholder_claim = PaperClaim(
                claim_id=f"{paper_id}_baseline",
                metric="long_short_monthly_return",
                claimed_value=0.0,
                claimed_tstat=None,
                claimed_units="fraction_per_month",
                paper_location="N/A (live upload, placeholder claim)",
                supporting_quote=SupportingQuote(text="placeholder", page=1),
            )

            _set_stage(job_id, "d2", "active")
            diagnosis = diagnose(
                spec=clipped, baseline_result=baseline,
                claim=placeholder_claim, store=store,
                max_experiments=3, use_cache=True,
            )
            bundle["diagnosis"] = diagnosis.model_dump(mode="json")
            _set_stage(job_id, "d2", "done")
            _set_partial(job_id, bundle)

            _set_stage(job_id, "battery", "active")
            scorecard = run_battery(
                clipped, store,
                families=("costs", "liquidity", "capacity", "data_quality"),
            )
            # Stash the scorecard immediately so the SPA's Robustness tab
            # renders even if D3's LLM judgment fails (usage cap, 529s, etc.).
            bundle["robustness"] = {
                "scorecard": scorecard.model_dump(mode="json"),
                "judgment": None,
            }
            _set_stage(job_id, "battery", "done")
            _set_partial(job_id, bundle)

            _set_stage(job_id, "d3", "active")
            judgment = judge(
                scorecard=scorecard, baseline=baseline, claim=placeholder_claim,
                use_cache=True,
            )
            bundle["robustness"]["judgment"] = judgment.model_dump(mode="json")
            _set_stage(job_id, "d3", "done")
            _set_partial(job_id, bundle)
        finally:
            # Only the defeatbeta-backed PointInTimeDataStore needs explicit
            # close (parquet handles); KenFrenchFactorStore is in-memory.
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        _finish_job(job_id, result=_sanitize_for_json(bundle))

    except BaseException as e:
        traceback.print_exc()
        # Persist whatever stages succeeded so the SPA can render the partial
        # result (e.g. extracted spec + critique + mapping) even though the
        # engine raised. Better than silently falling back to the JT report.
        _finish_job(
            job_id,
            error=f"{type(e).__name__}: {e}",
            result=_sanitize_for_json(bundle) if len(bundle) > 1 else None,
        )


@app.post("/api/pipeline/start")
def pipeline_start(body: PaperIdBody):
    """Kick off the full pipeline in a background thread; return a job_id."""
    job_id = uuid.uuid4().hex[:12]
    with PIPELINE_LOCK:
        PIPELINE_JOBS[job_id] = {
            "job_id": job_id,
            "paper_id": body.paper_id,
            "stage": "parse",
            "status": "active",
            "done": False,
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    threading.Thread(
        target=_run_pipeline, args=(job_id, body.paper_id), daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/pipeline/status/{job_id}")
def pipeline_status(job_id: str):
    with PIPELINE_LOCK:
        job = PIPELINE_JOBS.get(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "unknown job_id", "hint": "POST /api/pipeline/start first."},
        )
    return job


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
