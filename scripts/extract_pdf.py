"""Extract text, tables, images, figures, and full-page renders from a PDF.

Produces in papers/extracted/<stem>/:
  text.txt              prose with [[IMAGE: ...]] / [[FIGURE: ...]] markers
  manifest.json         structured index: per-page blocks, images, figures, tables
  images/p##_#.png      raster images embedded in the PDF (logos etc; junk filtered)
  figures/p##_<k>_N.png cropped exhibits detected from captions (works for vector art)
  pages/page_##.png     full-page renders (fallback for Claude vision)
  tables/p##_#.tsv      pdfplumber table extracts

Most quant-paper charts are vector art — invisible to page.get_images(). The
figures/ folder crops them out of page renders by locating "Exhibit N:" captions.
"""
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("papers/pdfs/aqr-streaky-returns.pdf")
OUT = Path("papers/extracted") / PDF.stem
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "images").mkdir(exist_ok=True)
(OUT / "figures").mkdir(exist_ok=True)
(OUT / "pages").mkdir(exist_ok=True)
(OUT / "tables").mkdir(exist_ok=True)

CAPTION_RE = re.compile(r"^\s*(Exhibit|Figure|Table)\s+(\d+)", re.IGNORECASE)
PAGE_RENDER_DPI = 150
FIGURE_CROP_DPI = 200
MIN_RASTER_PX = 150  # skip extracted rasters with any dim below this (logos/fragments)
FOOTER_MARGIN_PT = 40

doc = fitz.open(PDF)
print(f"[pymupdf] {PDF.name}: {len(doc)} pages")

manifest = {"pdf": str(PDF), "num_pages": len(doc), "pages": []}
interleaved_text = []

for page_idx, page in enumerate(doc):
    page_num = page_idx + 1
    page_entry = {"page": page_num, "text_blocks": [], "images": [], "tables": []}

    page_render = OUT / "pages" / f"page_{page_num:02d}.png"
    pix = page.get_pixmap(dpi=PAGE_RENDER_DPI)
    pix.save(page_render)
    page_entry["page_render"] = str(page_render.relative_to(OUT))

    blocks = page.get_text("blocks")
    text_blocks = [
        {"kind": "text", "y0": b[1], "y1": b[3], "x0": b[0], "x1": b[2], "text": b[4].strip()}
        for b in blocks
        if b[6] == 0 and b[4].strip()
    ]

    image_items = []
    for img_idx, info in enumerate(page.get_image_info(xrefs=True)):
        xref = info.get("xref", 0)
        if not xref:
            continue
        bbox = info.get("bbox", (0, 0, 0, 0))
        try:
            pm = fitz.Pixmap(doc, xref)
            if pm.width < MIN_RASTER_PX or pm.height < MIN_RASTER_PX:
                continue  # skip logo/icon fragments
            if pm.n - pm.alpha >= 4:
                pm = fitz.Pixmap(fitz.csRGB, pm)
            fname = f"p{page_num:02d}_{img_idx}.png"
            out_path = OUT / "images" / fname
            pm.save(out_path)
        except Exception as e:
            print(f"  [warn] image p{page_num}_{img_idx}: {e}")
            continue
        image_items.append(
            {
                "kind": "image",
                "y0": bbox[1],
                "y1": bbox[3],
                "x0": bbox[0],
                "x1": bbox[2],
                "filename": fname,
                "path": str(out_path.relative_to(OUT)),
            }
        )

    captions = []
    for b in text_blocks:
        m = CAPTION_RE.match(b["text"])
        if m:
            captions.append(
                {
                    "kind": m.group(1).lower(),
                    "num": m.group(2),
                    "y": b["y0"],
                    "label": m.group(0).strip(),
                    "text": b["text"].split("\n")[0][:160],
                }
            )
    captions.sort(key=lambda c: c["y"])

    figure_items = []
    page_height = page.rect.height
    page_width = page.rect.width
    for i, cap in enumerate(captions):
        y_top = max(0, cap["y"] - 4)
        y_bot = captions[i + 1]["y"] - 4 if i + 1 < len(captions) else page_height - FOOTER_MARGIN_PT
        if y_bot - y_top < 60:
            continue  # too small to be a figure
        clip = fitz.Rect(0, y_top, page_width, y_bot)
        fig_pix = page.get_pixmap(dpi=FIGURE_CROP_DPI, clip=clip)
        fname = f"p{page_num:02d}_{cap['kind']}_{cap['num']}.png"
        fig_path = OUT / "figures" / fname
        fig_pix.save(fig_path)
        figure_items.append(
            {
                "kind": "figure",
                "y0": y_top,
                "y1": y_bot,
                "x0": 0,
                "x1": page_width,
                "filename": fname,
                "path": str(fig_path.relative_to(OUT)),
                "caption": cap["text"],
                "label": cap["label"],
            }
        )

    combined = sorted(text_blocks + image_items + figure_items, key=lambda b: (b["y0"], b["x0"]))

    seen_figures = set()
    for block in combined:
        if block["kind"] == "text":
            text = block["text"]
            page_entry["text_blocks"].append(
                {"x0": block["x0"], "y0": block["y0"], "x1": block["x1"], "y1": block["y1"], "text": text}
            )
            interleaved_text.append(text)
        elif block["kind"] == "figure":
            if block["filename"] in seen_figures:
                continue
            seen_figures.add(block["filename"])
            interleaved_text.append(
                f"[[FIGURE: {block['filename']} | page {page_num} | {block['label']}]]"
            )
            page_entry.setdefault("figures", []).append(
                {
                    "filename": block["filename"],
                    "path": block["path"],
                    "bbox": [block["x0"], block["y0"], block["x1"], block["y1"]],
                    "caption": block["caption"],
                    "label": block["label"],
                }
            )
        else:
            interleaved_text.append(f"[[IMAGE: {block['filename']} | page {page_num}]]")
            page_entry["images"].append(
                {
                    "filename": block["filename"],
                    "path": block["path"],
                    "bbox": [block["x0"], block["y0"], block["x1"], block["y1"]],
                }
            )

    interleaved_text.append(f"\n===== END PAGE {page_num} =====\n")
    manifest["pages"].append(page_entry)

doc.close()

with pdfplumber.open(PDF) as pdf:
    for page_idx, page in enumerate(pdf.pages):
        page_num = page_idx + 1
        tables = page.extract_tables()
        for tbl_idx, t in enumerate(tables):
            fname = f"p{page_num:02d}_{tbl_idx}.tsv"
            path = OUT / "tables" / fname
            lines = ["\t".join(cell or "" for cell in row) for row in t]
            path.write_text("\n".join(lines))
            manifest["pages"][page_idx]["tables"].append(
                {"filename": fname, "path": str(path.relative_to(OUT)), "rows": len(t), "cols": len(t[0]) if t else 0}
            )

(OUT / "text.txt").write_text("\n\n".join(interleaved_text))
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

n_imgs = sum(len(p["images"]) for p in manifest["pages"])
n_figs = sum(len(p.get("figures", [])) for p in manifest["pages"])
n_tbls = sum(len(p["tables"]) for p in manifest["pages"])
print(f"[done] {n_figs} figures (cropped), {n_imgs} raster images, {n_tbls} tables, {len(manifest['pages'])} page renders")
print(f"[done] manifest: {OUT / 'manifest.json'}")
