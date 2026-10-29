# ADR 0005: chunked mode with an allow-list

- Date: 2026-10-29
- Status: accepted

## Context

Some files are too big for Excel and for comfortable in-memory processing. Several operations need the whole file: medians, `keep: last` and `order_by` in key rules, fuzzy matching, sorting, dropping empty columns, and Excel input and output. Approximating them in chunks (an approximate median, a per-chunk `keep: last`) would give results that differ from an in-memory run of the same profile without telling anyone.

## Decision

Chunked mode is explicit: `clean --chunked [--chunk-size N]`, never chosen by file size. Before any data is read, the profile and the targets are checked against the list of operations chunked mode cannot do; the run stops with exit 2 and lists every reason with its alternative. A key rule with `keep: first` and no `order_by` is allowed, because the first row of a key is decided by the digest set that is carried. Allowed profiles run the same stage functions per chunk with carried state (row counter, exact and key digest sets, `ffill` state, streaming statistics, example buffers), and the strict and gate decisions fall at the end with outputs written to temporary files.

Rejected: automatic chunking by size (results would depend on file size), approximate medians and per-chunk key rules (results would differ from in-memory runs), chunking with whole-file fallbacks (memory use would become unpredictable).

## Consequences

- For a chunk-compatible profile, chunked and in-memory runs write byte-identical output and rejects, by construction and by test (fixture 5 at chunk size 7,000; a property test with chunk sizes 1 to 50).
- Reports in chunked mode leave out median and top values and cap distinct counts at 10,000; everything else is exact.
- Memory grows with the digest sets, roughly 100 bytes per distinct row or key; the documentation gives this as an estimate, not a measured figure.
