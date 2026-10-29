# ADR 0004: Parquet as an extra

- Date: 2026-10-29
- Status: accepted

## Context

The brief asks for CSV, Excel or Parquet output. Parquet needs pyarrow, whose wheel for Python 3.13 is 28.5 MB on Windows and 53.9 MB on manylinux (PyPI, version 26.0.0). The analysts work in CSV and Excel, and pandas 3 runs without pyarrow. pandas 3 stores strings differently with and without pyarrow, which must not change the bytes the tool writes.

## Decision

Parquet output is the optional extra `parquet` (pyarrow 26 or later). `writer.py` imports pyarrow only when `--out` ends in `.parquet`; without the extra the run stops before reading data with exit 2 and the install command. The schema is fixed from the profile types (string, int64, float64, bool, date32, timestamp[us]) with snappy compression; chunked runs write one row group per chunk with `ParquetWriter`. Cells inside the pipeline are plain Python values, so CSV output is byte-identical with and without pyarrow installed.

Rejected: pyarrow as a core dependency (a large download for a format most users of the tool do not need), fastparquet (another dependency with less type control).

## Consequences

- `uv tool install "data-cleaning-tool[fuzzy,parquet] @ <wheel URL>"` installs everything; the default install stays small.
- The core-only CI job runs the golden tests without pyarrow and compares with the same golden files as the all-extras jobs.
- A round-trip property test reads the Parquet output back and compares types and values.
