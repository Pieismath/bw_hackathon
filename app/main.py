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
import traceback
from datetime import date
from pathlib import Path

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
    Returns the same VerifiedReplicationSpec shape the demo bundle uses.
    """
    try:
        from src.agents.extraction import extract_and_verify

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
        from src.data import DefeatBetaYahooSource, PointInTimeDataStore
        from src.engine import run_backtest

        spec = ReplicationSpec.model_validate(body.spec)

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
        result = run_backtest(spec, store, transaction_cost_bps=body.transaction_cost_bps)
        src_.close()
        return {"backtest": result.model_dump(mode="json")}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "hint": "Engine execution failed; see server logs."},
        )


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(status_code=500, content={"error": "frontend not built"})
    return FileResponse(idx)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
