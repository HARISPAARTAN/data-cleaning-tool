# Changelog

All notable changes to this project are listed here. Versions follow semantic versioning.

## v0.1.0 (2026-10-29)

First release.

- Commands `profile` (analysis before any profile exists), `init` (a commented profile skeleton) and `clean`.
- Profiles in YAML with pydantic validation, cross-checks and a JSON Schema for editor completion (`docs/profile.schema.json`).
- Reading CSV, TSV and TXT in any Python codec (BOM, strict UTF-8, then detection with a warning) and XLSX with several sheets, header offsets and footer rows; delimiter and header-row sniffing; exact source row numbers.
- Eight stages: read, structure, normalise, types, validate, dedupe, missing, write; row accounting `read = out + rejected + duplicates + empty` asserted on every run.
- German and English number and date formats, booleans, datetimes; invalid values rejected by default.
- Exact, key-based and fuzzy de-duplication (extra `fuzzy`, flag by default); missing-value strategies keep, drop, constant, median and ffill.
- Rejects file with source row, stage, rule and detail; JSON and self-contained HTML reports; `--dry-run`, `--strict`, `--explain`.
- Chunked mode for large CSV files, byte-identical to in-memory runs for chunk-compatible profiles.
- Output as canonical CSV, XLSX or Parquet (extra `parquet`).
- Five seeded fixtures with an independent oracle, golden outputs, property tests, CI on Ubuntu and Windows with Python 3.11 to 3.13, and a recorded demo on GitHub Pages.
