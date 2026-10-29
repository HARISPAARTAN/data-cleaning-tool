# Profile reference

A profile is one YAML file that says how to read, clean, check and write one kind of input file. It is validated with pydantic before any data is read: every key below is the only spelling accepted, an unknown key fails with its path (`columns.age.mx: Extra inputs are not permitted`), and the cross-checks at the end of this page run on the whole file. Editors that understand JSON Schema complete and check the keys when the first line is:

```text
# yaml-language-server: $schema=../docs/profile.schema.json
```

`docs/profile.schema.json` is generated from the models (`uv run python tools/export_schema.py`), and CI fails when it is out of date. PyYAML follows YAML 1.1, so `yes`, `no`, `on`, `off`, `y` and `n` must be quoted wherever they are values: `true_values: ["yes", "y"]`.

Every YAML block on this page is checked by `tests/docs/test_profile_docs.py`: a block without `version` is merged into a minimal profile (`version: 1`, `name: docs`, a string column `respondent_id`) and must validate.

Contents: [top level](#top-level) · [input](#input) · [headers](#headers) · [text](#text) · [columns](#columns) · [dedupe](#dedupe) · [quality](#quality) · [output](#output) · [report](#report) · [cross-checks](#cross-checks)

## Top level

### `version`

Required. Always `1`. A later schema gets a new number, so an old profile never changes meaning silently.

### `name`

Required, not empty. A short identifier shown in the logs, the explain plan and the report (`profile.name`).

### `description`

Default `""`. Free text for the people who maintain the profile; not used by the tool.

### `input`

How the file is read: encoding, delimiter, header row, sheets, empty rows, null tokens and the number and date locale. See [input](#input).

### `headers`

How header names become column names: normalisation, `rename` and `drop`. See [headers](#headers).

### `text`

Default text cleaning for every string cell; each column may override it. See [text](#text).

### `columns`

Required, at least one entry. A mapping from output column name (after header normalisation and `rename`) to its rule. The order of the entries is the order of the output columns. See [columns](#columns).

### `keep_unlisted`

Default `false`. With `false`, input columns that are not under `columns` (and not in `headers.drop`) are dropped with the warning `structure.unlisted_dropped` (which `--strict` turns into exit 4). With `true` they are kept as strings after the profile columns, in input order, and cleaned with the default `text` settings.

```yaml
keep_unlisted: true
```

### `dedupe`

Exact, key and fuzzy de-duplication. See [dedupe](#dedupe).

### `quality`

The reject-rate gate. See [quality](#quality).

### `output`

Sort order and the CSV and XLSX writer settings. The format itself follows the extension of `--out`. See [output](#output).

### `report`

Title, examples, preview rows, redaction, banner and footer of the reports. See [report](#report).

## Input

### `input.encoding`

Default `auto`. Any Python codec name (`utf-8`, `utf-8-sig`, `cp1252`, `latin-1`, `utf-16`). With `auto`: a byte order mark decides (UTF-8 BOM gives `utf-8-sig`, a UTF-16 BOM gives `utf-16`); otherwise the first 1 MiB is tried as strict UTF-8 and, if it decodes, the file is read as strict UTF-8 (a later invalid byte is exit 3 with its offset); otherwise charset-normalizer picks the best match, and a Western single-byte result becomes `cp1252` when the sample holds bytes 0x80 to 0x9F and `latin-1` when it does not. A detected encoding is a warning (`read.encoding_detected`) that `--strict` turns into exit 4, so scheduled jobs pin the encoding.

```yaml
input:
  encoding: latin-1
```

### `input.delimiter`

Default `auto`. One of `","`, `";"`, `"\t"` (or the word `tab`) and `"|"`. With `auto`, the first 50 records of the first 64 KiB are parsed with each candidate; the score is the share of records with the most common field count, candidates whose common count is below 2 drop out, and the highest score wins (then the larger field count, then the order above). No candidate means a single-column file and the warning `read.single_column`. Ignored for Excel input.

```yaml
input:
  delimiter: ";"
```

### `input.quotechar`

Default `'"'`. One character that quotes fields containing the delimiter, the quote itself or line breaks. Quoted fields may span lines; the record keeps the row number of its first line.

### `input.header_row`

Default `1`. The 1-based record (CSV) or sheet row (Excel) that holds the header; the rows above it are skipped. `auto` takes the first of the first 20 records whose non-empty cell count equals the maximum among them and whose non-empty cells are all text that parses as neither a number nor a date. Not found is exit 3.

```yaml
input:
  header_row: auto
```

### `input.skip_footer_rows`

Default `0`. The number of records at the end of the file, or of each sheet, that are left out (totals, signatures). They are counted in the report as `input.footer_skipped`, not as records read.

```yaml
input:
  skip_footer_rows: 1
```

### `input.sheets`

Default: the first sheet. Excel only: a list of sheet names or `fnmatch` patterns (`Wave *`, case-sensitive). Sheets that match are read in workbook order and stacked; each sheet finds its own header row, columns are matched by header name, and a column missing from one sheet is null there. Sheets that are not read are listed in the report (note `read.sheets_ignored`). No match is exit 3 with the list of sheet names.

```yaml
input:
  sheets: ["Wave *"]
```

### `input.sheet_column`

Default: none. Excel only: the name of an extra string column that holds each row's sheet name. It is written after the profile and unlisted columns and may be used in `dedupe` keys and `output.sort_by`.

```yaml
input:
  sheet_column: source_sheet
```

### `input.drop_empty_rows`

Default `true`. Records whose cells are all empty (after trimming) are dropped and counted as `rows.empty`; they are not written to the rejects file.

### `input.drop_empty_columns`

Default `false`. With `true`, input columns that are empty in every record and not listed under `columns` are dropped (note `structure.empty_columns_dropped`). Needs a full scan, so it is not available with `--chunked`; use `headers.drop` there.

```yaml
input:
  drop_empty_columns: true
```

### `input.null_tokens`

Default `[""]`. Strings that mean "no value" in every column, compared after trimming and case-insensitively. The empty string is always a null token. Columns can add their own with `columns.<name>.null_tokens`.

```yaml
input:
  null_tokens: ["", "k.A.", "n/a", "-"]
```

### `input.locale`

The number, date and boolean conventions of the file. Each column can override single keys with `columns.<name>.locale`.

```yaml
input:
  locale:
    decimal: ","
    thousands: "."
    date_formats: ["%d.%m.%Y", "%Y-%m-%d"]
```

#### `input.locale.decimal`

Default `"."`. The decimal separator, `"."` or `","`.

#### `input.locale.thousands`

Default: none. The grouping separator, one of `","`, `"."`, `" "` and `"'"`, or left out for no grouping. With a separator set, the integer part must be plain digits or correctly grouped in threes (`1.234.567`); under German settings `1.5` is invalid, not 15. It must differ from `decimal`.

#### `input.locale.date_formats`

Default `["%Y-%m-%d"]`. `strptime` patterns tried in this order with a strict full match; the first pattern that matches is counted in the report. Impossible dates (`31.02.2025`) are invalid. A native Excel datetime at midnight becomes a date; one with a time of day is invalid in a date column. Each pattern must round-trip a sample date, so `%d.%m` without a year is rejected.

#### `input.locale.datetime_formats`

Default `["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]`. The same for datetime columns. Datetimes are naive (no time zone), with microsecond resolution.

```yaml
input:
  locale:
    datetime_formats: ["%d.%m.%Y %H:%M"]
```

#### `input.locale.true_values`

Default `["true", "yes", "y", "1"]`. Strings read as `true` in boolean columns, case-insensitive. Native Excel booleans pass as they are.

```yaml
input:
  locale:
    true_values: ["ja", "j", "1", "true"]
    false_values: ["nein", "n", "0", "false"]
```

#### `input.locale.false_values`

Default `["false", "no", "n", "0"]`. Strings read as `false`. A value may not be in both lists.

## Headers

### `headers.normalise`

Default `snake_case`. `snake_case` applies, in this order: NFKC; `ä` to `ae`, `ö` to `oe`, `ü` to `ue`, `ß` to `ss`, other accents stripped; `€` to `eur`, `£` to `gbp`, `$` to `usd`, `%` to `pct`, `#` to `no`, `&` to `and`; camelCase and acronym boundaries split (`PartnerID` becomes `partner_id`); lower case; runs of other characters become `_`, trimmed; an empty header becomes `column_<position>`; a leading digit gets `c_`; repeated names get `_2`, `_3`, … from left to right, skipping names already taken. The rule is idempotent on its own output. `keep` uses the headers as written (after trimming), still numbering repeats. `clean-data profile` prints the normalised name of every header.

### `headers.rename`

Default `{}`. A mapping from normalised input name to output name. Keys are the normalised names (not the raw headers), targets must be snake_case and unique. A key that matches no input header is ignored, so one profile serves files with and without an optional column. Two input columns that end up with the same name stop the run with exit 2 and both positions named.

```yaml
headers:
  rename: {welle: wave, startdatum: start_date}
columns:
  wave: {type: integer}
  start_date: {type: date}
```

### `headers.drop`

Default `[]`. Normalised input names to leave out without a warning. Use it for known extra columns and, in chunked mode, for empty ones.

```yaml
headers:
  drop: ["column_14"]
```

## Text

The default cleaning of every string cell, applied in stage 3 in this order: Unicode form; control characters (C0 and C1 except tab and line feed) and zero-width characters (U+200B to U+200D, U+2060, U+FEFF) removed; U+00A0, U+202F and U+2009 become spaces; trim; whitespace runs collapsed; null tokens; case; `map`. Native Excel numbers, booleans and dates are not text and pass unchanged.

### `text.unicode`

Default `NFC`. `NFC`, `NFKC` or `none`. NFC joins a base letter and a combining mark (`u` plus U+0308) into one code point (`ü`), so the same name typed on two systems compares equal. NFKC also folds compatibility forms such as ligatures and full-width digits.

### `text.trim`

Default `true`. Removes leading and trailing whitespace.

### `text.collapse_whitespace`

Default `true`. Collapses every run of whitespace, line breaks included, to one space. Set it to `false` on free-text columns whose line breaks matter; carriage returns are still removed with the control characters, so `\r\n` becomes `\n`.

### `text.remove_control`

Default `true`. Removes control and zero-width characters as listed above.

### `text.case`

Default `keep`. `keep`, `lower`, `upper` or `title` (Python's `str.title`). Applied after null tokens, before `map`.

```yaml
text:
  unicode: NFKC
  case: keep
```

## Columns

Each entry under `columns` is one output column. The key is the name after header normalisation and `rename`; a non-optional column that is absent from the file is exit 3.

### `columns.<name>.type`

Required. `string`, `integer`, `number`, `boolean`, `date` or `datetime`. Coercion happens in stage 4 with the column's locale: numbers lose `strip` substrings and all whitespace, take an optional sign, follow `decimal` and `thousands`, allow an exponent only without grouping; an integer is a number with a zero fraction (`45.000,00` gives 45000; `12,5` is invalid); booleans use the true and false lists; dates and datetimes the pattern lists. A value that does not parse is invalid (see `on_invalid`). String columns have no type rule.

```yaml
columns:
  age: {type: integer}
  household_income: {type: number}
  opted_in: {type: boolean}
```

### `columns.<name>.optional`

Default `false`. With `true` the column may be missing from the file (or from some sheets): it is then written as an all-null column.

```yaml
columns:
  mode: {type: string, optional: true}
```

### `columns.<name>.null_tokens`

Default `[]`. Extra null tokens for this column, added to `input.null_tokens`.

```yaml
columns:
  age: {type: integer, null_tokens: ["keine Angabe"]}
```

### `columns.<name>.unicode`

Default: `text.unicode`. Overrides the Unicode form for this column.

### `columns.<name>.trim`

Default: `text.trim`.

### `columns.<name>.collapse_whitespace`

Default: `text.collapse_whitespace`.

```yaml
columns:
  interviewer_note: {type: string, collapse_whitespace: false}
```

### `columns.<name>.remove_control`

Default: `text.remove_control`.

### `columns.<name>.case`

Default: `text.case`.

```yaml
columns:
  respondent_id: {type: string, case: upper}
```

### `columns.<name>.map`

Default `{}`. Replaces whole cell values: keys and cells are compared with `casefold()` after the other text steps, and the target is written as given. A map must be idempotent (a target may not itself be a key with another target), so cleaning cleaned data changes nothing. Mapping happens before type coercion and validation.

```yaml
columns:
  country:
    type: string
    map: {deutschland: DE, de: DE, österreich: AT}
```

### `columns.<name>.strip`

Default `[]`. Substrings removed before a number is parsed (currency signs, units). Only number and integer columns use it.

```yaml
columns:
  amount_gbp: {type: number, strip: ["£"]}
```

### `columns.<name>.locale`

Default: `input.locale`. Overrides single locale keys for this column; keys left out are inherited.

```yaml
columns:
  paid_on:
    type: date
    locale: {date_formats: ["%d/%m/%Y"]}
```

#### `columns.<name>.locale.decimal`

Overrides `input.locale.decimal`.

#### `columns.<name>.locale.thousands`

Overrides `input.locale.thousands`.

#### `columns.<name>.locale.date_formats`

Overrides `input.locale.date_formats`.

#### `columns.<name>.locale.datetime_formats`

Overrides `input.locale.datetime_formats`.

#### `columns.<name>.locale.true_values`

Overrides `input.locale.true_values`.

#### `columns.<name>.locale.false_values`

Overrides `input.locale.false_values`.

### `columns.<name>.on_invalid`

Default `reject`. What happens to a cell that does not parse: `reject` sends the row to the rejects file with the rule `<name>.type`; `null` keeps the row and empties the cell, counted as `coercion.nulled` in the report (and a `--strict` failure). The default never loses a value silently.

```yaml
columns:
  total_score: {type: number, on_invalid: "null"}
```

### `columns.<name>.required`

Default `false`. With `true` a null cell (after null tokens and coercion) fails the rule `<name>.required`.

### `columns.<name>.min`

Default: none. Inclusive lower bound for integer, number, date and datetime columns, written in the column's type (`16`, `0.5`, `2025-01-01`). Fails as `<name>.min`.

```yaml
columns:
  age: {type: integer, min: 16, max: 99}
  start_date: {type: date, min: 2025-01-01}
```

### `columns.<name>.max`

Default: none. Inclusive upper bound, as `min`. Fails as `<name>.max`.

### `columns.<name>.regex`

Default: none. A Python regular expression that the normalised string must match completely (`re.fullmatch`), so `^` and `$` are optional. Fails as `<name>.regex`. Quote it in single quotes in YAML so backslashes stay as written.

```yaml
columns:
  respondent_id: {type: string, regex: '^NG-\d{6}$'}
```

### `columns.<name>.allowed`

Default: none. The list of allowed values, compared after normalisation, `map` and coercion, so `[1, 2]` on an integer column accepts `"02"`. Fails as `<name>.allowed`.

```yaml
columns:
  gender: {type: string, allowed: [female, male, diverse, not_stated]}
```

### `columns.<name>.missing`

Default `keep`. What to do with null cells after validation and de-duplication; either a strategy name or a mapping with `strategy` and `value`.

```yaml
columns:
  household_income: {type: number, missing: median}
  points: {type: integer, missing: {strategy: constant, value: 0}}
```

#### `columns.<name>.missing.strategy`

Default `keep`. `keep` leaves nulls; `drop` sends rows with a null here to the rejects file (rule `<name>.missing`); `constant` writes `value`; `median` writes `statistics.median` of the kept values for number columns and `statistics.median_low` for integer columns (always an observed value); `ffill` writes the previous non-null value in source order, and leading nulls stay. A `median` column that is null everywhere stays null and gets the warning `missing.all_null`, one warning for each such column. `median` needs the whole column and is not available with `--chunked`.

#### `columns.<name>.missing.value`

Default: none. The fill value of `constant`, written in the column's type. Only `constant` takes a value.

## Dedupe

De-duplication runs in stage 6, after validation, in this order: exact, then the key rules in order, then fuzzy. Removed rows go to the rejects file with `_detail` "duplicate of r17" and count as `rows.duplicates`; rows removed by the profile's own rules never fail `--strict`.

### `dedupe.exact`

Default `true`. Two rows are exact duplicates when their canonical CSV strings are equal over all output columns (null is a sentinel, never equal to `""`); the first occurrence stays. Detection uses 16-byte BLAKE2b digests in both drivers, so chunked runs find copies across chunk boundaries.

### `dedupe.keys`

Default `[]`. Key rules, applied in order; rule `n` (1-based) is reported as `dedupe.key.<n>`.

```yaml
dedupe:
  keys:
    - {columns: [respondent_id], keep: last, order_by: respondent_id}
```

#### `dedupe.keys[].columns`

Required, at least one. Rows with equal canonical values in all these columns are duplicates. A row with a null in any key column is never a key duplicate.

#### `dedupe.keys[].keep`

Default `first`. Which row of a group stays: `first` or `last`, in source order or in `order_by` order. `last` needs whole groups and is not available with `--chunked`.

#### `dedupe.keys[].order_by`

Default: none. A column that orders each group (ascending, nulls last, stable) before `keep` picks; the output still keeps source order. Not available with `--chunked`.

### `dedupe.fuzzy`

Default: none. Fuzzy de-duplication with the optional extra `fuzzy` (rapidfuzz). A profile with this section stops with exit 2 when the extra is missing, checked before any data is read, and it needs an in-memory run. Pairs are compared only within a block; per field, the scorer compares the casefolded, accent-folded, whitespace-collapsed join of the field's columns (scores 0 to 100); the pair score is the weighted mean of the field scores, leaving out fields that are null on either side and renormalising the weights; a pair with no comparable field never matches. Matches join transitively into clusters, and the first member in source order is the one that stays.

```yaml
columns:
  first_name: {type: string}
  last_name: {type: string}
  country: {type: string}
  year_of_birth: {type: integer}
dedupe:
  fuzzy:
    block_on: [country, year_of_birth]
    fields: [{columns: [first_name, last_name], scorer: token_sort_ratio}]
    threshold: 90
    action: flag
```

#### `dedupe.fuzzy.block_on`

Required, at least one column. Only rows with equal values in all these columns are compared; nulls never block together. Blocking keeps the pairwise work small.

#### `dedupe.fuzzy.fields`

Required, at least one. The fields that are scored.

##### `dedupe.fuzzy.fields[].columns`

Required. The columns joined with a space into the field's text.

##### `dedupe.fuzzy.fields[].scorer`

Default `token_sort_ratio`. `ratio`, `token_sort_ratio` (word order ignored, so swapped first and last names score 100) or `token_set_ratio`.

##### `dedupe.fuzzy.fields[].weight`

Default `1.0`, greater than 0. The field's weight in the mean.

#### `dedupe.fuzzy.threshold`

Default `90`, from 0 to 100. A pair matches when its score is at least the threshold (inclusive). The weighted score is rounded to 9 decimals before it is compared, so floating-point noise never decides a pair that sits exactly on the threshold.

#### `dedupe.fuzzy.action`

Default `flag`. `flag` keeps every row and writes the cluster id (`f0001`, `f0002`, … in order of each cluster's first member) to `cluster_column`, so a person reviews the clusters first; `remove` sends every member except the first to the rejects file with `_detail` "matches r17, score 93.8; kept r5" (rule `dedupe.fuzzy`).

#### `dedupe.fuzzy.cluster_column`

Default `dup_cluster`. The column written by `flag`, last in the output. It may not be a profile column.

#### `dedupe.fuzzy.max_block_size`

Default `2000`, at least 2. A larger block is skipped with the warning `dedupe.fuzzy_block_skipped` (a `--strict` failure); choose a finer `block_on`.

## Quality

### `quality.max_reject_rate`

Default: none. From 0 to 1. When `rejected / (read − empty)` is above it, the run fails the gate: exit 4, `run.status: failed_gate`, the rejects file and the reports are written, the cleaned output is not. Duplicates do not count as rejects.

```yaml
quality:
  max_reject_rate: 0.05
```

## Output

The output format follows the extension of `--out`: `.csv`, `.xlsx` or `.parquet` (extra `parquet`). Columns are written in this order: profile columns, kept unlisted columns, the sheet column, the cluster column.

### `output.sort_by`

Default `[]`. Columns to sort the output by (ascending, nulls last, stable). Without it the output keeps source order. Not available with `--chunked`.

```yaml
output:
  sort_by: [respondent_id]
```

### `output.csv`

The CSV writer. The canonical form (the default) is UTF-8 without a BOM, comma, `"` quoting only where needed, `\n` line ends on every system, null as an empty field, dates `YYYY-MM-DD`, datetimes `YYYY-MM-DDTHH:MM:SS`, integers without decimals, numbers as Python's shortest round-trip form, booleans `true` and `false`. A cell that starts with `=`, `+`, `-` or `@` is written as it is, because a cleaning tool must not alter values.

#### `output.csv.delimiter`

Default `","`. One of `","`, `";"`, `"\t"` (or `tab`) and `"|"`.

#### `output.csv.encoding`

Default `utf-8`. Any Python codec; a value the codec cannot encode stops the run with exit 5, and no partial file is left behind.

#### `output.csv.bom`

Default `false`. Writes a UTF-8 byte order mark, which some Excel versions need to open UTF-8 files correctly.

```yaml
output:
  csv: {delimiter: ";", encoding: utf-8, bom: true}
```

### `output.xlsx`

The XLSX writer: one sheet with a bold frozen header, an autofilter, column widths from the content (at most 50), native numbers and dates, date format `yyyy-mm-dd` (`yyyy-mm-dd hh:mm:ss` for datetimes). The workbook is a regular in-memory openpyxl workbook, not a write-only one, so XLSX output is not available with `--chunked`, and more than 1,048,575 data rows (the header takes one of Excel's 1,048,576 rows) is exit 5. Every string cell, the header cells and error-like text such as `#N/A` included, is stored as text, never as a formula or an error value. The bytes of an XLSX file differ from run to run because the file records timestamps; CSV output is reproducible byte for byte.

#### `output.xlsx.sheet_name`

Default `cleaned`. 1 to 31 characters, none of `[ ] : * ? / \`.

```yaml
output:
  xlsx: {sheet_name: cleaned}
```

## Report

### `report.title`

Default: the profile `name`. The title of both reports; a dry run has the same title, and the HTML report marks it with a "Dry run" badge.

### `report.examples_per_rule`

Default `5`, from 0 to 50. How many examples (`r118 "130"`) each rule, coercion and normalisation block keeps for the report and `--explain`.

### `report.preview_rows`

Default `20`. How many rejects rows the report shows (`rejects.preview`); the rejects file always has all of them.

### `report.redact`

Default `[]`. Columns whose values the reports show redacted: the first character, then `***`, then for e-mail-like values the domain (`a***@mail.example`). The cleaned output and the rejects file are not redacted. A redacted column of any type reports no `min`, `max`, `mean` or `median` in the statistics, and the `top` values of a redacted string column are masked the same way.

```yaml
columns:
  email: {type: string}
report:
  redact: [email]
```

### `report.banner`

Default: none. A line shown at the top of the HTML report (the demo profiles use "Demo with invented data for a fictional company").

### `report.footer`

Default: none. A line shown at the bottom of the HTML report.

## Cross-checks

After the keys are validated, the whole profile is checked, and every problem is listed with its path in one error (exit 2):

- column names may not be empty or start with `_` (the rejects file's meta columns start with `_`);
- `dedupe.keys[].columns`, `order_by`, `dedupe.fuzzy.block_on`, `dedupe.fuzzy.fields[].columns`, `output.sort_by` and `report.redact` must name profile columns or the sheet column;
- `dedupe.fuzzy.cluster_column` may not be a profile column;
- `headers.rename` targets must be snake_case and unique;
- `decimal` and `thousands` must differ, every date and datetime pattern must round-trip a sample date, and no value may be both true and false;
- `min` and `max` only on integer, number, date and datetime columns, and every bound, allowed value and constant fill must parse as the column's type;
- a `regex` must compile;
- `median` needs an integer or number column, and only `constant` takes a `value`;
- a `map` must be idempotent.

`--chunked` adds its own check before any data is read: a profile with a whole-frame strategy (`median` fill, fuzzy matching, `keep: last`, `order_by`, `sort_by` or `drop_empty_columns`) and any Excel input or output is refused with exit 2; see [chunked mode](chunked-mode.md). The extras are checked before any data is read as well: `fuzzy` for a profile with `dedupe.fuzzy`, `parquet` for a `.parquet` output.
