# ADR 0001: own CSV reader on the standard library

- Date: 2026-10-29
- Status: accepted

## Context

Every reject, example and explain line must name the row a person finds in the file: the physical row with the header counted, which is Excel's row number and, for single-line records, the editor's line number. Quoted fields may span lines, records may be short or long, header rows sit below title blocks and footer rows must be skipped. Chunked mode must behave exactly like an in-memory run. `pandas.read_csv` infers types unless told otherwise, reports bad lines through a callback without a uniform policy across engines, and its chunked reader keeps one continuous index but counts parsed records, not physical lines, so a quoted field that spans lines or a skipped blank line shifts every later number and `_source_row` cannot be derived from it.

## Decision

`reader.py` reads CSV-family files with the standard library `csv` module, on a decoded text stream with `newline=""`, and builds raw frames of object dtype itself: every cell is the string as read, `_source_row` is the physical line where the record starts, short records are padded with nulls (warning `read.short_record`), longer records are accepted when the extra fields are empty and otherwise rejected as `read.malformed` with the raw line. The same generator of records feeds both drivers; the in-memory driver takes one chunk of all records. Workbooks are read with openpyxl in read-only mode with cached formula values, which keeps native numbers, booleans and datetimes.

Rejected: `pandas.read_csv` with `dtype=str` and `on_bad_lines` (row numbers, malformed-record policy and chunk behaviour would each need work-arounds), polars (another frame library next to pandas, which the other Python projects use).

## Consequences

- Row numbers, the malformed-record policy and chunking are in one module and tested there.
- No type is guessed before the profile says what a column is; `profile` and `init` guess only to propose a profile.
- Reading is slower than the C parser of pandas; fixture 5 (50,000 records) stays within the soft timing limits, and the timing job warns if that changes.
- The canonical writer is the counterpart: also on the `csv` module, so read and write follow the same quoting rules.
