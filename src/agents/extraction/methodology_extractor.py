"""A1 — Methodology Extractor.

Consumes a ParsedPDF, returns a ReplicationSpec. The contract A1 honors
(every quote verbatim, ≤400 chars, single page, unstated ⇒ AmbiguityFlag)
is defined entirely in src/agents/prompts/methodology_extractor.md — this
Python module is a thin runner that formats the PDF for the model, calls
Claude with forced structured output, and hands the validated
ReplicationSpec back to the caller.

A2 (Extraction Verifier, next step) re-reads A1's output, runs each
`supporting_quote` through `src.pdf.quote_verifier.verify(..., expected_page=q.page)`
with `expected_page` pinned to the extractor's claimed page, and flags
fabricated or misaligned quotes for retry. A1 itself does not self-verify.
"""

from __future__ import annotations

from src.pdf.parser import ParsedPDF
from src.specs import ReplicationSpec
from src.utils.llm import call_claude, load_prompt

MODEL = "claude-sonnet-4-6"
PROMPT_NAME = "methodology_extractor"


def format_pdf_as_user_content(pdf: ParsedPDF) -> str:
    """Format a ParsedPDF using the `[Page N]` convention pinned in the prompt.

    Each page is prefixed by its `[Page N]` tag on its own line; pages are
    separated by blank lines. The model must cite the integer after `Page`
    in `supporting_quote.page`.
    """
    blocks = []
    for page_num, text in pdf.iter_pages():
        blocks.append(f"[Page {page_num}]\n{text.rstrip()}")
    return "\n\n".join(blocks)


def extract_methodology(
    pdf: ParsedPDF,
    use_cache: bool = True,
    max_retries: int = 3,
) -> ReplicationSpec:
    """Run A1 end-to-end: PDF → ReplicationSpec.

    The LLM wrapper caches on (model, system_prompt, user_content, schema),
    so re-running A1 on the same PDF with the same prompt hits disk after
    the first call. Changing the prompt or the ReplicationSpec schema
    invalidates the cache automatically.
    """
    system_prompt = load_prompt(PROMPT_NAME)
    user_content = format_pdf_as_user_content(pdf)
    return call_claude(
        model=MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        response_schema=ReplicationSpec,
        max_retries=max_retries,
        use_cache=use_cache,
    )
