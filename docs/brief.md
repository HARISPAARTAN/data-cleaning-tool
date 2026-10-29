# Brief

## Client brief

Northgate Panel Research is a fictional market-research panel in Manchester, UK. The brief, as the job post would read:

> We are a market-research panel in Manchester. Every week survey exports from our survey platform and respondent files from partner panels arrive as CSV and Excel, and no two look alike: commas or semicolons, German or English number formats, three date formats in one column, odd column names, the same respondent twice, blanks, and one partner's old system still sends Latin-1 text files. Our analysts clean them by hand in Excel, each a little differently. We want one tool we can run from the command line on Windows laptops and on our Linux server that cleans a file the same way every time from a rules file we can edit: duplicates, missing values, column names, types. It should put rejected rows in a separate file with the reason, tell us what it changed, and export CSV, Excel or Parquet. Some files are too big for Excel.

Rebuilt from memory as a demo. The original client's name and data are not used.

## Goal

One command-line tool that turns inconsistent survey exports and partner files into clean, typed, de-duplicated tables, driven by a YAML profile the client can read and edit. Every removed row lands in a rejects file with its reason, every change is summarised in an HTML and a JSON report, and the same input with the same profile gives byte-identical CSV output on Windows and Linux. Rules only: no learned models, nothing guessed.

## Users and roles

| Role | Uses | Needs |
|---|---|---|
| Data operations analyst | `profile`, `init`, `clean --dry-run --explain`, then `clean` | a readable profile, examples of every change, rejects with reasons |
| Panel manager | HTML report, rejects file | row accounting at a glance, no code |
| Data engineer | `clean --strict` in a scheduled job, JSON report | documented exit codes, stable JSON keys, no prompts |
| Portfolio reviewer | Pages usage page and demo report | the tool understood in a minute without installing it |

No accounts, no network access, no telemetry: the tool reads and writes local files only, and reports name input files by base name, never by full path.

## Scope

In: the commands `profile`, `init` and `clean`; CSV-family input (`.csv`, `.txt`, `.tsv`; comma, semicolon, tab or pipe) in any Python codec, and Excel `.xlsx` and `.xlsm` with several sheets and header offsets; encoding, delimiter and header-row sniffing; header normalisation with a rename map; Unicode and whitespace normalisation; type coercion with German and English number and date formats; exact, key-based and fuzzy (extra) de-duplication; missing-value strategies; validation rules with a rejects file; HTML and JSON reports; chunked mode for large CSV files; output CSV, XLSX or Parquet (extra); five fixtures from a seeded generator; the Pages demo; documentation; CI; release v0.1.0 with the wheel.

Out: `.xls`, `.ods`, JSON, XML and database sources; cross-column rules, expressions and derived columns; pseudonymisation of personal data (the reports only redact their own examples); time zones; rule learning or OCR; a GUI or web app; scheduling (the [handover](handover.md) gives the command lines); publication on PyPI; a Docker image.

## Acceptance criteria

1. On a fresh clone on Windows and Ubuntu, `uv sync --locked --all-extras` and every command in the READMEs and in [cli.md](cli.md) work as written; a test runs each marked block with its stated exit code.
2. The five fixtures give exactly the outcomes listed in the [data model](data-model.md#fixtures-and-expected-outcomes), checked against an oracle that the generator writes from its own planting log, independent of the cleaning code.
3. The same input with the same profile writes byte-identical cleaned and rejects CSV files on Windows and Linux, in memory and, for a chunk-compatible profile, in chunked mode.
4. Cleaning cleaned output again with the canonical profile changes nothing: no rejects, no duplicates, the same bytes (property tests).
5. Every removed row is in the rejects file with its source row, stage, rule and detail; empty records are counted. `read = out + rejected + duplicates + empty` holds in every run and every report.
6. A value that does not parse is rejected by default, never guessed; `on_invalid: null` empties it and counts it, and `--strict` fails on it.
7. `--strict` and `quality.max_reject_rate` stop with exit 4 and write the rejects file and the reports but not the cleaned file.
8. A profile with a typo fails with its YAML path before any data is read; a profile that cannot run chunked fails with every reason before any data is read.
9. The HTML report is one self-contained file that loads nothing from another host, reads without JavaScript, works at 390 px and meets AA contrast in both themes.
10. CI is green on Ubuntu 24.04 and Windows Server 2025 with Python 3.11, 3.12 and 3.13; mypy strict is clean on `core` and `report`; ruff is clean.
11. The documentation covers every profile field, every command and option, every exit code and every report field, and the handover explains how to add a partner profile, pin encodings, schedule runs, refresh fixtures and release.
