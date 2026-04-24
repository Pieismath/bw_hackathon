"""PDF parser — pdfplumber-backed, page-aware.

Every text span carries its page number via the `_pages_text: dict[int, str]`
mapping; the quote verifier relies on this to report where a match was found.
Tables are returned as `{page, rows}` dicts with row/column structure rather
than flat text, so downstream tooling can scan specific cells (e.g. "Table I
Panel B row J=6 column K=6"). When pdfplumber can't detect table boundaries
(e.g. the JT paper's spatially-laid-out tables with no borders), `get_tables`
returns an empty list — the table content is still recoverable from the
page text, and the empty list signals honestly that extraction was not
able to impose structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass
class ParsedPDF:
    """Page-aware wrapper over a pdfplumber extraction.

    Prefer `parse_pdf(path)` over constructing directly — this dataclass is
    exposed so tests and agents can reason about its shape, not so callers
    build it from scratch.
    """

    path: Path
    _pages_text: dict[int, str] = field(default_factory=dict)
    _tables: list[dict] = field(default_factory=list)

    @property
    def n_pages(self) -> int:
        return len(self._pages_text)

    @property
    def full_text(self) -> str:
        """All pages concatenated in order, separated by blank lines."""
        return "\n\n".join(
            self._pages_text[i] for i in sorted(self._pages_text)
        )

    def get_text_by_page(self, page_num: int) -> str:
        """Extracted text on page `page_num` (1-indexed). Raises if OOB."""
        if page_num not in self._pages_text:
            raise KeyError(
                f"page {page_num} not in parsed PDF (n_pages={self.n_pages})"
            )
        return self._pages_text[page_num]

    def get_tables(self) -> list[dict]:
        """Structured tables list. Each element: {page: int, rows: list[list[str]]}.

        May be empty if the PDF has no detectable bordered tables. In that
        case the table content is still present in the page text.
        """
        return list(self._tables)

    def iter_pages(self):
        """Yield (page_num, text) pairs in page order."""
        for i in sorted(self._pages_text):
            yield i, self._pages_text[i]


def parse_pdf(path: Path | str) -> ParsedPDF:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    pages_text: dict[int, str] = {}
    tables: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages_text[i] = text
            page_tables = page.extract_tables() or []
            for t in page_tables:
                if t:
                    # Each row is a list of cell strings; pdfplumber may
                    # emit None for empty cells — normalize to "".
                    rows = [[(cell or "") for cell in row] for row in t]
                    tables.append({"page": i, "rows": rows})
    return ParsedPDF(path=path, _pages_text=pages_text, _tables=tables)
