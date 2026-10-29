# ADR 0002: stage order

- Date: 2026-10-29
- Status: accepted

## Context

Cleaning steps interact. Whether two rows are duplicates depends on whether their whitespace and Unicode forms were normalised first; whether `keep: last` keeps the right record depends on whether invalid records were removed first; a median depends on which rows are counted.

## Decision

The eight stages run in a fixed order: read, structure, normalise, types, validate, dedupe, missing, write.

- Normalisation before exact de-duplication, so rows that differ only in whitespace, no-break spaces or NFD umlauts are duplicates (fixture 1 plants four such copies).
- Types before validation, so `min`, `max` and `allowed` compare values, not strings, and `allowed: [1, 2]` accepts `"02"`.
- Validation before de-duplication, so `keep: last` never prefers an invalid later submission over a valid earlier one.
- Missing values after de-duplication, so medians and forward fills come from the kept rows and a duplicate never shifts a median.
- "Before" statistics after the structure stage, on the raw values under their final names; "after" statistics on the frame that is written.

Rejected: de-duplication first (whitespace variants survive and invalid records can win), imputation first (duplicates and invalid rows bias medians).

## Consequences

- Each stage has a contract (what it may and must not change), and one pipeline test per stage asserts it.
- A row failing several rules gives one rejects row with every failure in `_detail`; the report counts each failure.
- A profile cannot fill a value and then validate the filled value; filled values are computed from valid rows only.
