# Paper extraction overrides

This directory holds human-curated patches for papers whose PDFs A1 cannot
cleanly extract from. Each override is bound to a verbatim-quote SHA-256
checksum so if the PDF is replaced and the evidence no longer matches, the
override silently disables.

## When to add an entry

Only when the PDF itself is unrecoverable for the field you're patching — for
example:

- **Lo-MacKinlay 1988** — text stream is byte-scrambled across the methodology
  section; pdfplumber and pypdfium2 both fail. Override the VR(q) headline
  claim with values transcribed from the abstract (which is non-scrambled).
- **Asness-Moskowitz-Pedersen 2013** — Table I cells are stored with mirrored
  character order. Override the US-momentum P3-P1 headline_claim with values
  transcribed by hand.

If A1 is just struggling with prompt design, fix the prompt instead. This
mechanism is the exit hatch when the data layer is genuinely unrecoverable.

## File format

One `.yaml` file per paper. Each file is a list of `PaperExtractionOverride`
entries — typically just one per paper unless multiple fields need patching.

```yaml
# lo_mackinlay_1988.yaml
- paper_id_hint: lo_mackinlay_1988
  paper_quote_checksum: <SHA-256 hex of verbatim quote from a non-scrambled span>
  kind: headline_claim
  field_path: headline_claim
  override_value:
    kind: variance_ratio_statistic
    q: 4
    vr_value: 1.30
    t_stat: 7.51
    window_label: "September 6, 1962 – December 26, 1985"
    paper_location: "Table 2, q=4, weekly equal-weighted size-portfolio 1"
    null_hypothesis: "random walk (VR(q) = 1 for all q)"
    supporting_quote:
      text: "<the verbatim quote whose checksum is above>"
      page: 16
      verified: true
      match_confidence: 1.0
  reason: |
    PDF text stream is byte-scrambled across the methodology section.
    pdfplumber and pypdfium2 both fail to recover the table cells. Override
    is bound to a verbatim quote from the abstract (page 1), which is
    non-scrambled. If the PDF is replaced and the abstract changes, this
    override silently disables.
```

## Computing the checksum

```python
from src.specs import compute_quote_checksum
from src.pdf.parser import parse_pdf

# Read the PDF and find a verbatim quote from a NON-scrambled span. The
# verifier will re-compute this digest against the parsed text on every
# extraction, so the quote must remain in the PDF byte-for-byte.
pdf = parse_pdf("data/papers/lo_mackinlay_1988_stock_market_prices_do_not_follow_random_walks.pdf")
verbatim = "..."  # paste the exact verbatim span from the PDF
print(compute_quote_checksum(verbatim))
```

Paste the resulting hex digest into the YAML under `paper_quote_checksum`.

## Why bind to a quote, not a paper_id

If the override were keyed on `paper_id`, replacing the PDF (e.g. with a new
copy from JSTOR that has different OCR) wouldn't invalidate the curated
values — the override would silently apply to a different document. Binding
to a quote checksum means: the override fires only when the SAME bytes are
still in the PDF. Replace the PDF, override disables; the user has to
re-verify the quote and recompute the checksum.

## Loading priority

`extract_and_verify` reads every `.yaml` file in this directory at extraction
time and applies matching overrides AFTER A1 emits the spec but BEFORE the
PortfolioSpec validator runs. Two safety properties:

1. A1's output is what the override patches; if A1 emits something good, the
   override no-ops (the value the override would set equals the value A1
   produced).
2. The override applies BEFORE the PortfolioSpec long-bucket validator, so
   override values must be in canonical form (long_bucket=n_buckets,
   short_bucket=1 for long-short specs). The override file is the contract;
   typos surface as validation errors at extract time.
