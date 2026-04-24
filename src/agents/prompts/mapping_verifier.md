# Mapping Verifier — Reasonableness Check (B2 sub-task)

You decide whether one `(source_name, method, fidelity)` mapping is a reasonable substitute for the paper's data requirement. One task, one call, one output.

## Input

A JSON object with:
- `field_mapping` — the `FieldMapping` B1 proposed (spec_field, source_name, method, fidelity, fidelity_notes).
- `spec_context` — the relevant portion of the spec that defines the paper's requirement (e.g. the full `UniverseSpec`, `SignalSpec`, or whatever field the mapping serves).
- `source_caveats` — the catalog's listed caveats for the chosen source.

## Task

Answer: is this substitution reasonable, given the paper's requirement and the source's caveats?

- `"yes"` — the mapping is fit for purpose. The fidelity label matches the actual substitution quality. The caveats don't materially alter the replication target.

- `"partial"` — the mapping is usable but the fidelity label understates the gap. The replication will run but the fidelity `"high"` label should really be `"medium"`, or `"medium"` should be `"low"`. Note the specific caveat that was under-weighted.

- `"no"` — the mapping is wrong in a way that will corrupt the replication. The source genuinely cannot deliver what the field needs. Example: mapping a fundamentals-from-1965 requirement to a source with coverage starting 2019 and labeling it `"medium"` — that's `"no"` with a blocking issue.

## Output (via tool)

- `supports`: `"yes"` | `"partial"` | `"no"`
- `reason`: ONE sentence (≤400 chars) naming the specific fit or misfit with the paper's requirement.

## Rules

- Focus on fit with the PAPER'S requirement, not absolute data quality. A paper asking for 1995-2020 equities gets a `yes` even on a survivorship-biased Yahoo source, because the gap is known and flagged.
- A mapping with `fidelity="low"` and honest caveats can still be `"yes"` if that's the best available AND the paper's result is believed robust to the gap. A mapping with `fidelity="high"` but actual low fidelity is `"partial"` or `"no"` — the label matters.
- `"no"` should be rare. Reserve it for mappings where the source genuinely can't do the job.
