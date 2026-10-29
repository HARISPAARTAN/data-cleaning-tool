# Reports

`clean` writes a JSON report, an HTML report or both (`--report` once or twice); `profile --json` and `--html` write the analysis in the same two forms. The HTML is rendered from the JSON, so both always hold the same facts. The demo run's reports are on Pages: [report.html](https://harispaartan.github.io/data-cleaning-tool/demo/report.html) and [report.json](https://harispaartan.github.io/data-cleaning-tool/demo/report.json).

## Reproducible reports

With the environment variable `SOURCE_DATE_EPOCH` set (seconds since 1970), `run.started_at` is that instant and `run.duration_s` is `null`, so two runs of the same input, profile and installed packages write byte-identical reports. A `SOURCE_DATE_EPOCH` that is not a whole number of seconds is a usage error (exit 2). Floats are rounded to 6 significant digits, keys always appear in the order below, and input files are named by base name only. CI uses this to rebuild the demo and compare it byte for byte (`tools/build_demo.py --check`).

## JSON report of `clean`

Top-level keys, in this order:

| Key | Content |
|---|---|
| `schema_version` | `1`. A breaking change of this layout gets a new number. |
| `kind` | `clean` (the analysis of `profile` has `profile`). |
| `title` | `report.title` or the profile name; a dry run has the same title (`run.dry_run` and the badge of the HTML report mark it). |
| `tool` | `name` (`clean-data`), `distribution` (`data-cleaning-tool`), `version`, `python` (major.minor) and `packages`: the installed versions of pandas, pydantic, click, openpyxl, charset-normalizer, PyYAML, Jinja2, rapidfuzz and pyarrow (`null` when an extra is not installed). |
| `run` | `started_at` (UTC, `YYYY-MM-DDTHH:MM:SSZ`), `duration_s` (seconds, or `null` with `SOURCE_DATE_EPOCH`), `mode` (`in_memory` or `chunked`), `chunk_size` (`null` in memory), `dry_run`, `strict`, `status` (`ok`, `failed_strict` or `failed_gate`) and `exit_code`. |
| `input` | `name` (base name), `size` (bytes), `sha256`, `kind` (`csv` or `xlsx`), `encoding` (`name`, `method` of `bom`, `utf-8`, `detected` or `given`, and `confidence` for a detected encoding), `delimiter` (`value` and the consistency `share` when it was sniffed), `header_row`, `sheets` (per sheet read: `name`, `header_row`, `records`), `ignored_sheets`, `records`, `footer_skipped`, `short_records` and `malformed`. |
| `profile` | `name`, `file` (base name) and `sha256` of the profile file. |
| `headers` | One entry per input column and per added column: `position` (0-based, `null` for added columns), `raw`, `normalised`, `final` and `action` (`kept`, `renamed`, `kept_unlisted`, `dropped`, `dropped_unlisted`, `dropped_empty`, `absent_optional` or `sheet_column`). |
| `rows` | `read`, `empty`, `rejected`, `duplicates`, `out`, `reject_rate` (`rejected / (read - empty)`), `max_reject_rate` (the gate or `null`) and `by_rule` (removed rows per first failing rule). `read = out + rejected + duplicates + empty` holds in every report. |
| `columns` | One entry per output column (see below). |
| `rules` | One entry per validation rule: `id` (`age.max`), `kind`, `column`, `parameters`, `checked`, `failed` and up to `report.examples_per_rule` `examples` such as `r118 "130"`. A row can fail several rules, so the counts can add up to more than the rejected rows. |
| `dedupe` | `exact` (`checked`, `removed`, or `null` when off), `keys` (per rule: `id`, `columns`, `keep`, `order_by`, `checked`, `removed`) and `fuzzy` (see below, or `null`). |
| `rejects` | `file` (base name or `null`), `rows`, `by_stage` (`read`, `validate`, `dedupe`, `missing`), `by_rule`, `by_column` and `preview`: the first `report.preview_rows` rejects rows with `source_row`, `stage`, `rule`, `detail` and the raw `values`. |
| `warnings` | Notices that `--strict` turns into exit 4: `code`, `message` and `count`. |
| `notes` | Informational notices (empty columns dropped, repeated headers numbered, sheets not matched). |
| `explain` | With `--explain`: the lines of the explain log; else `null`. |
| `banner`, `footer` | `report.banner` and `report.footer` of the profile, or `null`. |

### `columns.<name>`

| Key | Content |
|---|---|
| `type` | The profile type; `string` for kept unlisted, sheet and cluster columns. |
| `source` | `profile`, `unlisted`, `sheet` or `cluster`. |
| `before` | Taken after stage 2 on the raw values: `non_empty`, `null_like` (null tokens seen), `distinct` and `distinct_capped` (exact up to 10,000), `top` (five most frequent values with counts), `min_length`, `max_length`, `whitespace` (cells with leading, trailing or repeated whitespace), `non_nfc` and `control` (cells with control or zero-width characters). |
| `after` | Taken on the written frame: `count` (non-null), `nulls`, `distinct`, `distinct_capped`, `min`, `max`, `mean` and `median` (numbers and integers), `top` (strings and booleans) and `filled`. In chunked mode `median` and `top` are `null` and `distinct` stops at 10,000. |
| `coercion` | `parsed`, `null`, `invalid`, `nulled` (by `on_invalid: null`) and `patterns` (values per date pattern, `native` for Excel values); `null` for unlisted, sheet and cluster columns. |
| `missing` | `strategy`, the fill `value` (typed: `68000.0`, `6`) and `filled`; `null` when the strategy is `keep`. |
| `changed_cells` | Output cells whose canonical string differs from the raw value as read. |

### `dedupe.fuzzy`

`threshold`, `action`, `block_on`, `cluster_column`, `blocks` (blocks with at least one row), `blocks_compared` (blocks with two rows or more), `skipped_blocks` (above `max_block_size`), `clustered_rows`, `removed` (with `action: remove`) and `clusters`: per cluster its `id` (`f0001`), the `members` (`label`, `source_row`, `source_sheet`) and the pair `scores` that joined them (`a`, `b`, `score`). The HTML report lists the first 50 clusters.

### Redaction

Columns in `report.redact` appear in every example, top value and preview row as the first character, `***` and, for e-mail-like values, the domain: `a***@mail.example`. The cleaned file and the rejects file are not redacted; they are the client's data. A redacted column of any type reports no `min`, `max`, `mean` or `median` in `after`, and its `top` values, before and after, are masked the same way.

## HTML report

One self-contained file: the CSS, a two-line script that applies the stored theme and the theme button are inline, nothing is loaded from another host, and the page reads fine without JavaScript, so it works as an e-mail attachment. It uses the portfolio-kit colour tokens (a unit test keeps them equal to `web/kit/tokens.css`): dark by default, light when the system prefers light, the button switches and remembers the choice (`localStorage` key `hh-theme`, shared with the usage page), and print uses the light colours. Sections, in order:

1. Title with input, profile, version and mode, and badges: "Dry run" for a dry run, and "Passed" or "Failed" (strict or quality gate).
2. KPI tiles: read, out, rejected, duplicates, cells changed, columns.
3. Row flow: a stacked bar of kept, rejected by stage, duplicates and empty records, with the reject rate and the gate.
4. Warnings and notes.
5. Header mapping: raw, normalised, final name and action per column.
6. Columns before and after, with a `<details>` per column for top values, pattern counts and the fill value.
7. Rules with their examples.
8. Duplicates: exact and key counts, fuzzy blocks and the first 50 clusters with scores.
9. Rejects by stage, rule and column, and the first `report.preview_rows` rows.
10. Explain (with `--explain`).
11. Run info: versions, hashes, timestamp.

Tables scroll inside their own container at 390 px, and header cells carry `scope`. Large numbers print with plain digits (`12,345,678`), never with an exponent.

## Analysis of `profile`

`profile --json` writes, in this order: `schema_version`, `kind` (`profile`), `title` ("Profile of" and the file name), `tool`, `input` (as above), `records` (`analysed`, `empty`, `short`, `malformed`, `duplicates` and `stopped_early` at `--max-rows`), `sheets` (every sheet of a workbook with `name`, `header_row`, `records` and `selected`), `columns`, `file_notes` (`duplicate_headers`, `empty_columns`, `duplicate_records`, the `locale` vote with its `votes`, and the `date_formats` and `datetime_formats` found), `warnings` (ambiguity notes with the code `profile.note`), `banner` and `footer` (`null`).

Each entry of `columns` has `position`, `raw`, `normalised`, the `before` statistics of the clean report (`non_empty` to `control`), `candidates` (per type the share of non-null values it parses), the `guess`, a few `examples` and the `notes` for that column. `--html` renders the same with the report template.
