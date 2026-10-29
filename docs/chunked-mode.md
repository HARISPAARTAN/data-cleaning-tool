# Chunked mode

`clean --chunked [--chunk-size N]` streams a CSV-family file in chunks of N records (default 100,000) instead of reading it whole. Use it for files that do not fit in memory. It is opt-in, never chosen automatically, and it never approximates: a profile that needs the whole file is refused before any data is read.

```bash
uv run clean-data clean fixtures/05_panel_large.csv --profile profiles/05_large.yaml --chunked --chunk-size 7000 \
  --out out/large.csv --rejects out/large-rejects.csv --report out/large.json
```

(Fixture 5 is not committed; `uv run python tools/make_data.py --large` writes it.)

## What runs per chunk

Stages 1 to 8 run per chunk, with this state carried from chunk to chunk:

- the global `_source_row` counter, so row numbers are those of the whole file;
- digest sets for exact and key de-duplication: 16-byte BLAKE2b digests of the canonical row and of each key, the same digests the in-memory driver uses, so a copy 7,001 records after its original is found across chunk boundaries;
- the last non-null value per `ffill` column;
- streaming statistics (count, nulls, min, max, mean, distinct up to 10,000) and the example buffers of every rule.

The cleaned output and the rejects go to temporary files. The `--strict` and quality-gate decisions fall at the end, when all counts are known; only on success is the output moved into place. The reports are written last, so the writability of the report folders is checked before any data is read and before the outputs are moved into place (exit 5). A last chunk with no records, which happens when the record count is a multiple of N, is tolerated, and a header-only input gives exit 0 and a cleaned file with just its header row, in memory and chunked alike.

## Not available in chunked mode

| Not available | Why | Alternative |
|---|---|---|
| Excel input; XLSX output or rejects | openpyxl reads whole sheets; the workbook writer cannot append | an in-memory run (a sheet holds at most 1,048,575 data rows; a larger result is exit 5); CSV or Parquet output, CSV rejects |
| `input.drop_empty_columns: true` | needs a full scan | `headers.drop` |
| a key rule with `keep: last` or `order_by` | needs whole groups | `keep: first` on a pre-sorted file |
| `missing: median` | needs the whole column | `constant` with the median from an in-memory dry run on a sample |
| fuzzy de-duplication | pairwise work within blocks | an in-memory run |
| `output.sort_by` | needs all rows | sort downstream |

The run stops with exit 2 and lists every reason at once. The survey profile, for example:

```text
ERROR: the profile cannot run with --chunked:
  input.drop_empty_columns needs a full scan; list the columns in headers.drop
  dedupe.key.1: keep last or order_by needs whole groups; keep first on a sorted file
  columns.household_income.missing: median needs the whole column; use a constant
  columns.satisfaction.missing: median needs the whole column; use a constant
```

`--explain` lists the same reasons in its plan. `profiles/05_large.yaml` is an example of a chunk-compatible profile: no fuzzy section, the key rule keeps the first row without `order_by`, and points are filled with a constant.

## Reports in chunked mode

`run.mode` is `chunked` and `run.chunk_size` holds N. Count, nulls, min, max and mean are exact; distinct is exact up to 10,000 and then reported as capped (`distinct_capped: true`); median and top values are left out (`null`). Everything else (row accounting, rules, examples, rejects) is the same as in memory.

## Same result as in memory

For a chunk-compatible profile, chunked and in-memory runs write byte-identical output and rejects and the same row accounting, by construction (the same stage functions and the same digest-based de-duplication in both drivers) and by test: fixture 5 with `--chunk-size 7000`, and a property test over generated files with chunk sizes 1 to 50.

## Memory

One chunk (raw and typed) plus the digest sets. A digest costs roughly 100 bytes per distinct row or key in CPython, so the exact set grows by about 1 GB per 10 million distinct rows. This is an estimate, not a measurement; turn off `dedupe.exact` or key rules you do not need for very large files.

## Parquet

Parquet output in chunked mode uses pyarrow's `ParquetWriter`: one row group per chunk, with the schema fixed from the profile types before the first chunk, so every row group has the same column types.
