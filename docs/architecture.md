# Architecture

![The input file and the profile feed eight stages in a chain; a ledger, statistics and the explain log collect events from every stage; the outputs are the cleaned file, the rejects file and the JSON report rendered to HTML](architecture.svg)

## Components

```text
src/cleandata/
  __init__.py      package docstring and the version from importlib.metadata
  __main__.py      python -m cleandata
  cli.py           the click group, logging setup, the three commands, exceptions to exit codes
  errors.py        the exception classes, each with its exit code
  core/
    profile.py     pydantic models of the profile, cross-checks, per-column plans, canonical()
    sniff.py       encoding, delimiter and header-row detection
    reader.py      stage 1: CSV on the stdlib csv module, XLSX on openpyxl (read-only, cached values)
    headers.py     stage 2: empty records, header normalisation, rename and drop, sheet column
    text.py        stage 3: Unicode, control and zero-width characters, whitespace, null tokens, case, map
    coerce.py      stage 4: numbers, integers, booleans, dates and datetimes with locale settings
    validate.py    stage 5: required, min, max, regex, allowed and the type marks
    dedupe.py      stage 6: exact digests, key rules, then fuzzy
    fuzzy.py       blocked pairwise scores and union-find clusters (imports rapidfuzz lazily)
    missing.py     stage 7: drop, constant, median, ffill
    stats.py       statistics before and after, in memory and streaming
    writer.py      stage 8: canonical CSV, XLSX (a regular in-memory openpyxl workbook), Parquet (imports pyarrow lazily), atomic replacement
    ledger.py      stage events, row accounting, the rejects collector
    frame.py       frame helpers and the stage context
    pipeline.py    the in-memory and chunked drivers, the chunk check, the explain log
    analyse.py     the analysis behind profile and init
  report/
    build.py       the JSON report with a fixed key order
    html.py        renders a report to one self-contained HTML file
    report.css     the report styles with the portfolio-kit colour tokens
    templates/report.html.j2
  skeleton/
    init.py        renders the profile skeleton and validates it before writing
    templates/profile.yaml.j2
```

`cli.py` is the only module that knows about click and logging handlers, and the only one that ends the process with an exit code; everything under `core` and `report` is plain functions on frames and dictionaries, which is what the unit tests call and what `mypy --strict` checks.

## Stages

Stages 3 to 7 are functions `(frame, context) -> (frame, events)`, listed in `STAGES` in `pipeline.py`; stages 6 and 7 also update the state that is carried from chunk to chunk (the digest sets, the last `ffill` values). Stage 1 is a generator that yields raw chunks (`chunks()` of the CSV and workbook sources in `reader.py`), stage 2 has its own signature (`structure_stage(frame, header plan, width, drop_empty_rows)`) and stage 8 writes through the sinks of `writer.py`. The events (rejects, fills, coercion counts, examples, warnings) feed `ledger.py` (row accounting and the rejects collector), `stats.py` and the explain log (its own logger, so `--explain` prints to stderr even under `--quiet`). One test per stage asserts its contract from this table.

| # | Stage | Does | May change | Must not change |
|---|---|---|---|---|
| 1 | read | decode, split records, find the header row, skip footer rows, read the matched sheets, number the rows; malformed records go to rejects | builds the raw frame | — |
| 2 | structure | drop empty records (and empty columns if enabled); normalise and number headers; `rename`, `drop`; add the sheet column; a non-optional profile column that is absent is exit 3; unlisted columns are dropped with a warning unless `keep_unlisted` | names, column set, empty rows | cell values |
| 3 | normalise | Unicode form, control and zero-width characters, whitespace, null tokens, case, `map`, on string cells | string values, nulls | names, rows, native Excel values |
| 4 | types | coerce each column with its locale; mark invalid cells, or null them under `on_invalid: null` | types, values | names, rows |
| 5 | validate | `required`, `min`, `max`, `regex`, `allowed` on valid cells; rows with an invalid mark or a failed rule go to rejects | row set | values, names |
| 6 | dedupe | exact, then the key rules in order, then fuzzy | row set; `flag` adds the cluster column | values |
| 7 | missing | `drop` rows go to rejects; `constant`, `median`, `ffill` fill nulls (a `median` column that is entirely null fills nothing and gives a warning, one per column) | nulls, row set (`drop`) | non-null values, names |
| 8 | write | `sort_by`; column order (profile columns, kept unlisted columns, sheet column, cluster column); canonical serialisation; atomic writes of output, rejects and reports | files | the frame |

"Before" statistics are taken after stage 2, "after" statistics on the frame written in stage 8. Why this order: normalisation comes before exact de-duplication, so rows that differ only in whitespace or Unicode form are duplicates; validation comes before de-duplication, so `keep: last` never prefers an invalid record over a valid one; imputation comes after de-duplication, so medians and forward fills come from the kept rows ([ADR 0002](adr/0002-stage-order.md)).

## Frames

`reader.py` yields raw frames of object dtype: CSV cells are `str`, Excel cells keep native `int`, `float`, `bool` and `datetime`. `_source_row` (1-based physical row with the header counted) and `_source_sheet` travel with every row, so every reject, example and explain line names the row a person finds in the file. The reader is our own, on the stdlib `csv` module, rather than `pandas.read_csv`: exact record numbers for every reject, one policy for short and malformed records, the same behaviour in chunks, and no type inference before the profile says what a column is ([ADR 0001](adr/0001-own-csv-reader.md)).

## Two drivers

`pipeline.py` has two drivers that call the same stage functions. The in-memory driver runs all eight stages on one frame. The chunked driver runs stages 1 to 8 per chunk with carried state: the global row counter, digest sets for exact and key de-duplication (16-byte BLAKE2b digests, as in memory), the last non-null value per `ffill` column, streaming statistics and example buffers. Operations that need the whole file are refused before any data is read, so a chunked run never approximates ([chunked mode](chunked-mode.md), [ADR 0005](adr/0005-chunked-mode-allow-list.md)). For a chunk-compatible profile both drivers write byte-identical output and rejects; a fixture test on 50,000 records and a property test on chunk sizes 1 to 50 check that.

## Determinism

- The canonical CSV writer is our own on the stdlib `csv` module (not `DataFrame.to_csv`): fixed formats for every type, `\n` line ends, so the bytes are the same on Windows and Linux and in both drivers. The XLSX writer is not reproducible byte for byte, because the file carries timestamps.
- Cells are Python values, not pandas extension types, so the bytes do not depend on whether pyarrow is installed; the core-only CI job compares its outputs with the golden files of the all-extras jobs.
- Every file is written to a temporary file in the target folder and moved into place with `os.replace`; a failed or interrupted run leaves no partial file.
- With `SOURCE_DATE_EPOCH` set, reports carry that timestamp and no duration, so they reproduce byte for byte (a value that is not a whole number of seconds is a usage error); the Pages demo is rebuilt and compared in CI.

## Optional extras

Fuzzy matching (rapidfuzz) and Parquet (pyarrow) are extras, imported only when a profile or `--out` needs them ([ADR 0003](adr/0003-fuzzy-extra-flag-default.md), [ADR 0004](adr/0004-parquet-extra.md)). A run that needs a missing extra stops before reading data with exit 2 and names the install command.

## Errors and exit codes

Expected failures are exceptions from `errors.py`, each with its exit code: `UsageError` and `ProfileError` 2, `InputError` 3, `OutputError` 5. Usage errors include a `--log-file` in a missing folder and a `--header-row` that is not a row number written in ASCII digits or `auto`; `init` on a file without usable columns is an input error (exit 3). Quality failures (`--strict`, the reject-rate gate) are not exceptions but a run status (`failed_strict`, `failed_gate`) that `cli.py` turns into exit 4 after the rejects file and the reports are written. Anything else is a bug, exit 1, with the traceback at `--log-level debug`. Ctrl+C removes temporary files and exits 130.
