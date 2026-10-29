# Data model

Four kinds of data pass through the tool: the profile (what to do), the frames inside the pipeline, the files it writes (cleaned output, rejects, reports) and the fixtures it is tested on.

## Profile

A YAML file validated by pydantic v2 models in `cleandata.core.profile`. Every model forbids unknown keys, so a typo fails with its YAML path. Every field, its default and an example are in the [profile reference](profile-reference.md); the JSON Schema is [profile.schema.json](profile.schema.json).

| Model (YAML path) | Holds |
|---|---|
| `Profile` (root) | `version` (always 1), `name`, `description`, the sections below, `columns` (output name to rule, in output order) and `keep_unlisted` |
| `InputOptions` (`input`) | encoding, delimiter, quote character, header row, footer rows, sheets, sheet column, empty rows and columns, null tokens, locale |
| `Locale` (`input.locale`, partial override per column) | decimal and thousands separators, date and datetime patterns, true and false values |
| `HeaderOptions` (`headers`) | normalisation, rename map, dropped names |
| `TextOptions` (`text`, the same keys per column) | Unicode form, trim, collapse whitespace, remove control characters, case |
| `ColumnRule` (`columns.<name>`) | type, optional, null tokens, text overrides, value map, strip, locale, `on_invalid`, the validation rules (`required`, `min`, `max`, `regex`, `allowed`) and `missing` |
| `MissingRule` | strategy (`keep`, `drop`, `constant`, `median`, `ffill`) and the constant value |
| `DedupeOptions` (`dedupe`) | `exact`, `keys` (a list of `KeyRule`: columns, keep, order_by) and `fuzzy` (`FuzzyRule`: block_on, fields with scorer and weight, threshold, action, cluster column, maximum block size) |
| `QualityGate` (`quality`) | `max_reject_rate` |
| `OutputOptions` (`output`) | `sort_by`, CSV writer settings, XLSX sheet name |
| `ReportOptions` (`report`) | title, examples per rule, preview rows, redacted columns, banner, footer |

`Profile.canonical()` returns the profile that reads the tool's own canonical CSV output back unchanged: the output's encoding and delimiter, header row 1, ISO dates, `.` as the decimal separator without grouping, `true` and `false`, no maps or strips, no fuzzy section (its flags are already in the cluster column). The idempotence property tests clean the output again with it.

## Frames in the pipeline

The reader yields raw frames of object dtype: CSV cells are `str`, Excel cells keep their native `int`, `float`, `bool` and `datetime` values. Two meta columns travel with every row: `_source_row`, the 1-based physical row with the header counted (it matches Excel's row number and, for single-line records, the line in a text editor), and `_source_sheet` for workbooks. After stage 4 each profile column holds Python values of its type (`str`, `int`, `float`, `bool`, `datetime.date`, `datetime.datetime`) or `None`; pandas only carries them, so the bytes written do not depend on whether pyarrow is installed.

## Cleaned output

Columns in this order: the profile columns (in profile order), the kept unlisted columns (in input order), the sheet column (at its profile position when the profile lists it as a column), the fuzzy cluster column. The canonical CSV form is UTF-8 without a BOM, comma, `"` quoting only where needed, LF line ends on every system, a header row, null as an empty field, dates `YYYY-MM-DD`, datetimes `YYYY-MM-DDTHH:MM:SS` (written with `isoformat()`, so a value with microseconds keeps them: `YYYY-MM-DDTHH:MM:SS.ffffff`), integers without decimals, numbers as Python's shortest round-trip form (`45000.0`, `1234.56`) and booleans `true` and `false`. A value that starts with `=`, `+`, `-` or `@` is written as it is. XLSX keeps native numbers and dates and always stores strings as text (the header row and values such as `#N/A` or `=1+1` stay text); it is written from a regular in-memory openpyxl workbook with at most 1,048,575 data rows (a larger result is exit 5). XLSX bytes are not reproducible because the file carries timestamps; the CSV bytes are. Parquet uses the types string, int64, float64, bool, date32 and timestamp[us] with snappy compression.

## Rejects file

Every removed row except empty records (which are only counted), in source order:

| Column | Content |
|---|---|
| `_source_row` | the row number as above |
| `_source_sheet` | the sheet name (workbook input only) |
| `_stage` | `read`, `validate`, `dedupe` or `missing` |
| `_rule` | the first failing rule id: `age.max`, `start_date.type`, `read.malformed`, `dedupe.exact`, `dedupe.key.1`, `dedupe.fuzzy`, `household_income.missing` |
| `_detail` | all failures of the row joined with `; ` (`age.max: 130 > 99`, `start_date.type: "31.02.2025" not a date (…)`, `respondent_id.required: empty`), `duplicate of r902` for exact and key duplicates, `matches r17, score 93.8; kept r5` for fuzzy removal, or the raw line of a malformed record (cut at 500 characters) |
| data columns | the output columns without the cluster column, with the raw values as read, so the analyst sees what arrived |

The rejects file holds `rejected + duplicates` rows. Its columns depend only on the header and the profile, never on the values; a property test checks that. A fuzzy-removed row has one `_detail` item, `matches r17, score 93.8; kept r5`: the `; kept r5` belongs to the text of that item and is not a second failure. In `flag` mode nothing is removed, so nothing is written to the rejects file.

## Reports

The JSON report has a fixed key order and `schema_version: 1`; the HTML report is rendered from it. Every field is described in [report.md](report.md). The parts that matter most to readers: `rows` (the row accounting `read = out + rejected + duplicates + empty`, asserted in every run, and the reject rate `rejected / (read - empty)` that the quality gate uses), `columns` (statistics before and after, coercion counts per pattern, fills and changed cells), `rules` (checked, failed, examples), `dedupe` and `rejects`. Columns in `report.redact` appear masked in examples, top values and the rejects preview, and whatever their type they report no `min`, `max`, `mean` or `median`; the cleaned file and the rejects file are not redacted.

## Fixtures and expected outcomes

`uv run python tools/make_data.py` (seed 20261029) writes fixtures 1 to 4 into `fixtures/` and, with `--large`, fixture 5. Names are drawn with fixed seeds from the pools in `tools/name_pool.py` (common first names, last names and towns for en_GB, en_IE, de_DE, nl_NL, es_ES, fr_FR and da_DK, combined at random, so no record describes a real person); e-mail addresses are `<first>.<last><n>@inbox.example`, `@mail.example` or `@post.example`, ASCII-folded; respondent ids are `NG-` plus six digits, panel references `PA-` or `PB-` plus five digits; only cities and countries, no addresses. Defects are planted at exact counts on disjoint, seeded row sets, and the generator writes `fixtures/expected/<nn>.json` from its planting log: row accounting, counts per rule, fills, cluster members and every rejected row. That oracle is independent of the cleaning code. `fixtures/MANIFEST.json` holds the SHA-256 of every fixture and expected file, and for the fixtures also their size (`bytes`) and whether they are committed. `make_data.py --check` regenerates everything into a temporary folder and compares.

| # | File | Profile | Expected outcome |
|---|---|---|---|
| 1 | `01_survey_export_de.csv`: UTF-8 with BOM, semicolon, CRLF, 13 mostly German headers plus an empty one from a trailing semicolon, 1,026 records | `01_survey.yaml` | read 1,026 · empty 8 · rejected 18 (10 `start_date.type`, 1 `respondent_id.required`, 2 `respondent_id.regex`, 3 `age.max`, 2 `age.min`) · duplicates 18 (12 exact, 6 by key; the later submission stays) · out 982 rows × 13 columns; 20 incomes get the median 68000.0, 15 satisfaction scores the lower median 6; exit 0 (reject rate 1.8 % under the 5 % gate), exit 4 with `--strict` |
| 2 | `02_partner_respondents.csv`: UTF-8, comma, LF, 10 columns (`PartnerID`, `PanelistRef`, `First Name`, …), 2,480 records | `02_partner.yaml` | read 2,480 · rejected 17 (7 `country.allowed`, 2 `year_of_birth.min`, 2 `year_of_birth.max`, 6 `email.regex`) · duplicates 65 (25 exact, 40 by key) · 15 fuzzy clusters flagged with 30 rows, no decoy pair flagged · out 2,398 rows × 11 columns (with `dup_cluster`); without the `fuzzy` extra exit 2 naming it |
| 3 | `03_wave_workbook.xlsx`: sheets `Read me`, `Wave 1`, `Wave 2`, `Wave 3`, `Lookup`; per wave a title block in rows 1 to 3, the header in row 4 and a `Total` row at the end | `03_waves.yaml` | 3 sheets read, 2 ignored (note) · read 907 · empty 2 · 3 footer rows skipped · duplicates 5 (by key) · rejected 0 · 3 `total_score` cells nulled (fails `--strict`) · `mode` null in 600 rows · out 900 rows × 7 columns; `header_row: auto` finds row 4 on all three sheets |
| 4 | `04_legacy_partner_latin1.txt`: ISO-8859-1, tab, CRLF, 8 columns plus a trailing tab on every line, 602 records | `04_legacy.yaml` | read 602 · rejected 2 (`read.malformed`) · 4 short records padded (warning, fails `--strict`) · out 600 rows × 8 columns in UTF-8, every name equal to the generator's string, note line breaks kept; `profile` without overrides detects latin-1 with a warning, and the tab |
| 5 | `05_panel_large.csv`: the partner layout, 50,000 records, about 4.5 MB; written at test time by a session fixture and by `make_data.py --large`, not committed (its SHA-256 is in the manifest) | `05_large.yaml` (chunk-compatible) | read 50,000 · rejected 250 (`country.allowed`) · duplicates 1,500 (1,000 exact copies, each 7,001 records after its original so copies cross chunk boundaries, and 500 by key) · 1,000 points filled with 0 · out 48,250 rows × 10 columns; `--chunked --chunk-size 7000` writes byte-identical output and rejects |

Golden outputs `tests/golden/<nn>.cleaned.csv` and `<nn>.rejects.csv` for fixtures 1 to 4 are the regression oracle next to the planted counts. They change only through `uv run pytest tests/fixtures --update-golden`, and the diff is reviewed before it is committed.
