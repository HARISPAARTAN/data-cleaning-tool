# ADR 0003: fuzzy matching as an extra, flag by default

- Date: 2026-10-29
- Status: accepted

## Context

Partner files contain the same person twice with a typo or with first and last name swapped. Exact and key rules miss those. Fuzzy matching needs a compiled string-similarity library, works pairwise and therefore only in memory, and a false merge of two different panelists is costly: one of them disappears from the panel data.

## Decision

Fuzzy de-duplication uses rapidfuzz as the optional extra `fuzzy`. A profile section `dedupe.fuzzy` requires blocking (`block_on`, rows compared only within equal blocks), scores fields with a rapidfuzz scorer (default `token_sort_ratio`) on casefolded, accent-folded, whitespace-collapsed text, takes the weighted mean over the fields that are present on both sides, and matches at a score of at least the threshold (default 90, inclusive; the weighted score is rounded to 9 decimals before it is compared). Matches join into clusters with union-find. The default action is `flag`: every row stays and gets a cluster id, so a person reviews the clusters first; `remove` is opt-in. Blocks above `max_block_size` are skipped with a warning. Scores are computed per block with `process.cdist` and no `score_cutoff`, which would set low field scores to 0 and distort the mean.

Rejected: rapidfuzz as a core dependency (only partner profiles need it), `remove` by default (silent false merges), matching without blocking (quadratic work on the whole file).

## Consequences

- The default install has no compiled string-matching dependency; a profile with `dedupe.fuzzy` stops with exit 2 and the install command when the extra is missing, and the core-only CI job checks that message.
- Fuzzy matching is not available in chunked mode (ADR 0005).
- Fixture 2 plants 15 near-duplicate pairs and 20 decoy pairs; the test requires exactly the 15 clusters and no decoy at the threshold of 90.
