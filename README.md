# Data-Cleaning Tool

[![CI](https://github.com/HARISPAARTAN/data-cleaning-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/HARISPAARTAN/data-cleaning-tool/actions/workflows/ci.yml)
[![Pages](https://github.com/HARISPAARTAN/data-cleaning-tool/actions/workflows/pages.yml/badge.svg)](https://harispaartan.github.io/data-cleaning-tool/)
[![Release](https://img.shields.io/github/v/release/HARISPAARTAN/data-cleaning-tool)](https://github.com/HARISPAARTAN/data-cleaning-tool/releases)

Cleans messy CSV and Excel exports reproducibly from a YAML profile, with a rejects file and an HTML report.

Deutsche Fassung: [README.de.md](README.de.md)

## Status

- Version 0.1.0. Commands `profile`, `init` and `clean`; input CSV, TSV, TXT and XLSX; output CSV, XLSX and Parquet (extra `parquet`); fuzzy de-duplication as the extra `fuzzy`.
- Verified locally on Windows 11 with Python 3.13 and the locked packages: the whole test suite passes (unit, CLI, fixtures, property and docs tests), the five fixtures give exactly the outcomes in [docs/data-model.md](docs/data-model.md), and chunked and in-memory runs of the 50,000-record fixture write byte-identical files.
- CI on Ubuntu 24.04 and Windows Server 2025 with Python 3.11, 3.12 and 3.13 has not run yet; this line changes when it has.

## Client brief

> We are a market-research panel in Manchester. Every week survey exports from our survey platform and respondent files from partner panels arrive as CSV and Excel, and no two look alike: commas or semicolons, German or English number formats, three date formats in one column, odd column names, the same respondent twice, blanks, and one partner's old system still sends Latin-1 text files. Our analysts clean them by hand in Excel, each a little differently. We want one tool we can run from the command line on Windows laptops and on our Linux server that cleans a file the same way every time from a rules file we can edit: duplicates, missing values, column names, types. It should put rejected rows in a separate file with the reason, tell us what it changed, and export CSV, Excel or Parquet. Some files are too big for Excel.

Rebuilt from memory as a demo. The original client's name and data are not used.

## Live demo

The [usage page](https://harispaartan.github.io/data-cleaning-tool/) shows a recorded run on the survey fixture: the four commands with their output, the first records before and after cleaning, and links to the [HTML report](https://harispaartan.github.io/data-cleaning-tool/demo/report.html), the JSON report, the profile, the input and both output files.

![The HTML cleaning report of the survey fixture: KPI tiles, the row flow and the header mapping](docs/screenshots/report.png)

## What it does

- **Reads files the same way every time:** encoding (BOM, strict UTF-8, then detection with a warning) and delimiter sniffing, quoted multi-line fields, header rows below a title block, footer rows, several Excel sheets stacked with a sheet column, short and malformed records.
- **Headers:** snake_case with umlauts, currency signs and camelCase handled (`Haushaltseinkommen (€)` becomes `haushaltseinkommen_eur`), repeated names numbered, then a rename map.
- **Text:** Unicode form, control and zero-width characters, no-break spaces, whitespace, null tokens such as `k.A.`, case, and value maps (`Deutschland`, `de` and `DE` all become `DE`).
- **Types with locales:** German and English numbers (`45.000,00`, `1,250`), dates in several patterns, datetimes and booleans; a value that does not parse is rejected, never guessed.
- **Validation:** required, minimum, maximum, full-match regex and allowed values; every failing row goes to the rejects file with all its failures.
- **De-duplication:** exact (after normalisation), by key with keep first or last and an order column, and fuzzy within blocks (extra `fuzzy`, flags clusters for review by default).
- **Missing values per column:** keep, drop, constant, median (lower median for integers) or forward fill.
- **Reports:** a JSON report with stable keys and a self-contained HTML report with before and after statistics, row accounting and examples of every rule.
- **Chunked mode** for CSV files larger than memory, byte-identical to an in-memory run for profiles that allow it.
- **Outputs:** canonical CSV (identical bytes on Windows and Linux), XLSX with a frozen header and filters, Parquet.

## How it works

![The eight pipeline stages from input file and profile to cleaned file, rejects and reports](docs/architecture.svg)

The profile is validated first; then eight stages run in order: **read**, **structure** (empty records, header names, rename and drop), **normalise** (text), **types**, **validate**, **dedupe**, **missing** and **write**. Each stage is a pure function from a frame to a frame plus events; the events feed the row ledger (`read = out + rejected + duplicates + empty`, checked on every run), the statistics and the explain log. Normalisation comes before exact de-duplication, so rows that differ only in whitespace are duplicates; validation comes before de-duplication, so `keep: last` never prefers an invalid record; missing values are filled last, from the kept rows ([ADR 0002](docs/adr/0002-stage-order.md)).

- `--dry-run` runs every stage and writes only the reports.
- `--strict` turns every non-duplicate reject and every warning into exit 4, for scheduled jobs.
- `--explain` logs the resolved plan and examples of every rule to stderr and adds them to the reports.

More in [docs/architecture.md](docs/architecture.md).

## Data model

A profile in short (the full survey profile is [profiles/01_survey.yaml](profiles/01_survey.yaml)):

```yaml
version: 1
name: survey-de
input: {encoding: utf-8-sig, delimiter: ";", locale: {decimal: ",", thousands: "."}}
headers: {rename: {alter: age}}
columns:
  respondent_id: {type: string, case: upper, required: true, regex: '^NG-\d{6}$'}
  age: {type: integer, min: 16, max: 99}
dedupe: {exact: true, keys: [{columns: [respondent_id], keep: last}]}
quality: {max_reject_rate: 0.05}
```

Every key is described in the [profile reference](docs/profile-reference.md), and [docs/profile.schema.json](docs/profile.schema.json) gives editors completion. The rejects file holds every removed row with `_source_row`, `_stage`, `_rule` and `_detail`, then the raw values as read; the JSON report is described field by field in [docs/report.md](docs/report.md). Fixtures, rejects columns and report sections: [docs/data-model.md](docs/data-model.md).

## Getting started

Install the command from the release wheel with [uv](https://docs.astral.sh/uv/), extras included:

```bash
uv tool install "data-cleaning-tool[fuzzy,parquet] @ https://github.com/HARISPAARTAN/data-cleaning-tool/releases/download/v0.1.0/data_cleaning_tool-0.1.0-py3-none-any.whl"
```

Or clone the repository, which also has the fixtures and profiles:

```bash
git clone https://github.com/HARISPAARTAN/data-cleaning-tool.git
cd data-cleaning-tool
uv sync --all-extras
```

Look at a file, write a profile skeleton, then clean (bash):

<!-- run exit=0 -->
```bash
uv run clean-data profile fixtures/01_survey_export_de.csv
uv run clean-data init fixtures/01_survey_export_de.csv --out my-survey.yaml
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml \
  --out out/cleaned.csv --rejects out/rejects.csv --report out/report.html --report out/report.json
```

The same in PowerShell:

<!-- run exit=0 -->
```powershell
uv run clean-data profile fixtures/01_survey_export_de.csv
uv run clean-data init fixtures/01_survey_export_de.csv --out my-survey.yaml
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml `
  --out out/cleaned.csv --rejects out/rejects.csv --report out/report.html --report out/report.json
```

The last command prints `read 1026 · empty 8 · rejected 18 · duplicates 18 · out 982 · exit 0`. Run the tests with `uv run pytest`; the soft timing check on the 50,000-record fixture is part of that run and only warns. [docs/cli.md](docs/cli.md) lists every option.

| Exit | Meaning |
|---|---|
| 0 | success |
| 1 | internal error |
| 2 | usage or profile error |
| 3 | input error |
| 4 | quality gate (`--strict` or `quality.max_reject_rate`) |
| 5 | output error |
| 130 | interrupted |

## Project layout

```text
src/cleandata/        the package: cli.py, core/ (one module per stage), report/, skeleton/
profiles/             the profiles of the five fixtures
fixtures/             four generated fixtures, their expected outcomes and MANIFEST.json
tests/                unit, cli, fixtures, property, docs and timing tests; golden outputs
tools/                make_data.py, build_demo.py, export_schema.py, sync_kit.py and the checks
docs/                 brief, data model, architecture, profile reference, CLI, reports, handover, ADRs
web/                  the Pages site: usage page, demo run, vendored portfolio-kit
```

## Decisions

- [ADR 0001: own CSV reader on the standard library](docs/adr/0001-own-csv-reader.md)
- [ADR 0002: stage order](docs/adr/0002-stage-order.md)
- [ADR 0003: fuzzy matching as an extra, flag by default](docs/adr/0003-fuzzy-extra-flag-default.md)
- [ADR 0004: Parquet as an extra](docs/adr/0004-parquet-extra.md)
- [ADR 0005: chunked mode with an allow-list](docs/adr/0005-chunked-mode-allow-list.md)

## Compared with the original delivery

The original work was a cleaning script per client file. The rebuild adds a written brief with acceptance criteria, one validated profile schema instead of code changes per file, a rejects file and reports for every run, unit, CLI, fixture and property tests, CI on Windows and Linux, a recorded demo and handover documentation.

## Licence and data note

MIT, see [LICENSE](LICENSE). Every fixture is generated by [tools/make_data.py](tools/make_data.py) with a fixed seed: first and last names are drawn at random from short lists of common names per country ([tools/name_pool.py](tools/name_pool.py)), e-mail addresses use the reserved top-level domain `.example`, and Northgate Panel Research is a fictional company. No real person, company or address appears in this repository.
