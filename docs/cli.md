# Command line

`clean-data` has three commands: `profile` looks at a file before any profile exists, `init` writes a commented profile skeleton, and `clean` cleans a file with a profile. In a clone, run them with `uv run` in front; after `uv tool install` the command is on the `PATH`.

```text
clean-data [--log-level debug|info|warning|error] [--quiet] [--log-file PATH] [--version] COMMAND ...
```

Every block marked as runnable on this page is executed by `tests/docs/test_run_blocks.py` from the repository root with the stated exit code, so the examples stay correct. The commands are the same in PowerShell and bash; only line continuations differ (a trailing backtick in PowerShell, a trailing backslash in bash).

## Global options

| Option | Effect |
|---|---|
| `--log-level` | `debug`, `info` (default), `warning` or `error`. Logs go to stderr as `LEVEL: message`: one line per stage with counts, plus warnings and errors. Tracebacks appear only at `debug`. |
| `--quiet` | The same as `--log-level error`; the block that `--explain` prints still appears. |
| `--log-file PATH` | Also writes every message at debug level, with ISO timestamps, to this file. The folder must exist: a path in a missing folder is a usage error (exit 2). |
| `--version` | Prints `clean-data 0.1.0 (Python 3.13, pandas 3.1.0, extras: fuzzy, parquet)` with the installed values and exits 0. |

Group options come before the command. stdout carries only results (the `profile` table, the `clean` summary line, the `init` YAML), so it can be piped or redirected; everything else goes to stderr.

<!-- run exit=0 -->
```bash
uv run clean-data --version
uv run clean-data --quiet clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml --dry-run --report out/quiet.json
```

## `profile`

```text
clean-data profile FILE [--sheet NAME]... [--encoding ENC] [--delimiter D] [--header-row N|auto]
                        [--max-rows N] [--json PATH] [--html PATH]
```

Needs no profile. Prints the file's base name, size and SHA-256; the encoding with the method that found it (BOM, valid UTF-8, detected with a confidence, or given); the delimiter with its consistency share; the header row; the counts of records, empty, short, malformed and duplicate records; for workbooks, every sheet with its detected header row. Per column: the raw and the normalised header, non-empty and distinct counts, null-like tokens seen, and the type guess with the share of values it parses, plus notes on ambiguity (`03/04/2025` read day-first and month-first; `38.500` read as 38.5 in English and 38500 in German style). Per file: repeated headers, empty columns and a locale vote. `--json` and `--html` write the full analysis, with top values, lengths and whitespace counts per column. `--max-rows` (default 200,000) bounds the work, and the output says when it stopped early. The options `--sheet`, `--encoding`, `--delimiter` and `--header-row` override what the analysis would find; `--header-row` takes a 1-based row number written with ASCII digits, or `auto`. A date or datetime guess labelled `(native)` means that the values are native Excel dates or datetimes, not text matched by a pattern.

<!-- run exit=0 -->
```bash
uv run clean-data profile fixtures/01_survey_export_de.csv
uv run clean-data profile fixtures/04_legacy_partner_latin1.txt --json out/legacy-profile.json --html out/legacy-profile.html
uv run clean-data profile fixtures/03_wave_workbook.xlsx --sheet "Wave *"
```

The Latin-1 file has no BOM and is not valid UTF-8, so the encoding is detected and the run logs the warning `encoding latin-1 was detected (confidence …); pin it with input.encoding`. Exit codes: 0, 2 (bad option), 3 (file missing or unreadable).

## `init`

```text
clean-data init FILE [--out PATH|-] [--force] [the read options of profile]
```

Runs the same analysis and writes a profile skeleton with comments: `input` with the detected encoding, delimiter, header row, sheets and the voted locale; `headers` with an empty `rename` whose comment lists every normalised name, and `drop` for empty columns; one `columns` entry per non-empty column with the guessed type (a type needs 95 % of the non-null values, else `string`), per-column date patterns where they differ from the global ones, and comments with counts, examples and ambiguity notes; `dedupe: {exact: true}`. It adds no validation rules and no missing-value strategies: those are the analyst's decisions. The skeleton is validated before it is written. Default `--out -` (stdout); an existing file needs `--force`, else exit 2. An explicit `--header-row` is kept in the skeleton of a workbook as well. A file without usable columns (no records, or every column empty) is an input error, exit 3. The first comment lines of the skeleton point to [profile-reference.md](profile-reference.md).

<!-- run exit=0 -->
```bash
uv run clean-data init fixtures/01_survey_export_de.csv --out my-survey.yaml
uv run clean-data clean fixtures/01_survey_export_de.csv --profile my-survey.yaml --dry-run --report out/first-look.html
```

<!-- run exit=2 -->
```bash
# my-survey.yaml exists after the block above; without --force init refuses to replace it
uv run clean-data init fixtures/01_survey_export_de.csv --out profiles/01_survey.yaml
```

## `clean`

```text
clean-data clean FILE --profile PATH --report PATH [--report PATH] [--out PATH] [--rejects PATH]
                      [--dry-run] [--strict] [--explain] [--chunked] [--chunk-size N]
```

| Option | Effect |
|---|---|
| `--profile` | The YAML profile ([reference](profile-reference.md)). Validated before any data is read. |
| `--out` | The cleaned file: `.csv`, `.xlsx` or `.parquet` (extra `parquet`, checked before any data is read). Required unless `--dry-run`; when given it is checked either way. |
| `--rejects` | Every removed row with its reason: `.csv` or `.xlsx`. |
| `--report` | `.html` or `.json`, given once or twice. Required. |
| `--dry-run` | Runs every stage and writes only the reports, with `run.dry_run: true` and a "Dry run" badge in the HTML report. Exit codes are those of a real run. |
| `--strict` | Exit 4 on any row rejected for a non-duplicate reason, any cell nulled by `on_invalid: null`, padded short records, a detected encoding, unlisted columns not in `headers.drop`, a skipped fuzzy block or any other warning. |
| `--explain` | Prints the resolved plan and one block per stage and rule with counts and examples on stderr, whatever the log level (`--quiet` included), and adds an Explain section to both reports. stdout is unchanged. |
| `--chunked` | Streams a CSV file in chunks; only for chunk-compatible profiles ([chunked mode](chunked-mode.md)). A profile with a whole-frame strategy (median fill, fuzzy matching, `keep: last`, `order_by`, `sort_by` or `drop_empty_columns`) and any Excel input or output are refused with exit 2 before any data is read. |
| `--chunk-size` | Records per chunk, default 100,000. |

Every file is written to a temporary file in the target folder and moved into place, so a failed run never leaves half a file; existing files are replaced. CSV output is reproducible byte for byte; XLSX files are not, because the workbook records timestamps. stdout gets one summary line:

```text
read 1026 · empty 8 · rejected 18 · duplicates 18 · out 982 · exit 0
```

<!-- run exit=0 -->
```bash
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml \
  --out out/cleaned.csv --rejects out/rejects.csv --report out/report.html --report out/report.json
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml \
  --dry-run --explain --report out/dry-run.html
uv run clean-data clean fixtures/03_wave_workbook.xlsx --profile profiles/03_waves.yaml \
  --out out/waves.xlsx --rejects out/waves-rejects.xlsx --report out/waves.html
uv run clean-data clean fixtures/04_legacy_partner_latin1.txt --profile profiles/04_legacy.yaml \
  --out out/legacy.csv --rejects out/legacy-rejects.csv --report out/legacy.json
```

The same first command in PowerShell:

<!-- run exit=0 -->
```powershell
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml `
  --out out/cleaned.csv --rejects out/rejects.csv --report out/report.html --report out/report.json
```

`--strict` on the survey file fails, because it rejects 18 rows for non-duplicate reasons; the rejects file and the report are written, the cleaned file is not:

<!-- run exit=4 -->
```bash
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml --strict \
  --out out/strict.csv --rejects out/strict-rejects.csv --report out/strict.json
```

The survey profile uses operations that need the whole file, so `--chunked` stops before reading data and lists every reason:

<!-- run exit=2 -->
```bash
uv run clean-data clean fixtures/01_survey_export_de.csv --profile profiles/01_survey.yaml --chunked \
  --out out/chunked.csv --report out/chunked.json
```

A missing input file is an input error:

<!-- run exit=3 -->
```bash
uv run clean-data clean fixtures/does-not-exist.csv --profile profiles/01_survey.yaml --dry-run --report out/none.json
```

Fixture 2 needs the `fuzzy` extra (`uv sync --all-extras` installs it):

```bash
uv run clean-data clean fixtures/02_partner_respondents.csv --profile profiles/02_partner.yaml \
  --out out/partner.parquet --rejects out/partner-rejects.csv --report out/partner.html
```

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | success; a run without `--strict` may have rejects |
| 1 | internal error (a bug); the message suggests `--log-level debug` |
| 2 | usage or profile error: bad option, YAML or schema error (each with its path), a profile that cannot run chunked, a missing extra, `--out` equal to the input, `init` target exists without `--force`, a `--log-file` in a missing folder, a `SOURCE_DATE_EPOCH` that is not a whole number of seconds |
| 3 | input error: file missing or unreadable, undecodable with the chosen encoding (byte offset given), no matching sheet, header row not found, a non-optional profile column absent, `init` on a file without usable columns |
| 4 | quality gate: a `--strict` violation or `quality.max_reject_rate` exceeded; the cleaned output is not written, the rejects file and the reports are (`run.status` `failed_strict` or `failed_gate`) |
| 5 | output error: target not writable or a directory, a value the output encoding cannot hold, Excel row limit (1,048,575 data rows) exceeded |
| 130 | interrupted (Ctrl+C); temporary files removed |

## Logging

At the default level every stage logs one line, for example:

```text
INFO: read: 1026 records from 01_survey_export_de.csv
INFO: structure: 8 empty records dropped, 13 output columns
INFO: normalise: 2723 cells changed
INFO: types: 10 invalid cells, 0 set to null
INFO: validate: 18 rows rejected
INFO: dedupe: 18 rows removed (12 exact, 6 by key, 0 fuzzy)
INFO: missing: 35 cells filled, 0 rows dropped
INFO: write: 982 rows written to cleaned.csv
```

Reports and logs name input files by base name, never by full path. `--explain` adds lines such as:

```text
[types]    household_income number(decimal=",", thousands=".")  parsed 998 · null 20 · invalid 0
           r2 "45.000,00" -> 45000.0 · r3 "38.500" -> 38500.0
[validate] age.max (<= 99)  failed 3 · r118 "130" · r544 "150" · r871 "101"
[dedupe]   dedupe.key.1 [respondent_id] keep last by completed_at  removed 6 · r40 superseded by r902
```

`r118` is the source row: the 1-based row of the file with the header counted, which matches Excel's row number and, for single-line records, the line in a text editor.
