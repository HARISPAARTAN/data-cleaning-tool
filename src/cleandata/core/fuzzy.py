"""Fuzzy de-duplication (extra ``fuzzy``): blocked pairwise scores, a threshold and union-find clusters.

Pairs are compared only within a block (equal ``block_on`` values; nulls never block together). Per
field the scorer compares the casefolded, accent-folded, whitespace-collapsed join of its columns;
rapidfuzz scores 0 to 100 per block through ``process.cdist`` without ``score_cutoff`` (a cutoff would
zero field scores and distort the mean). The pair score is the weighted mean of the field scores,
leaving out fields with a null on either side; a match needs ``score >= threshold``. Matches join
transitively into clusters, ordered by their first member in source order.
"""

from __future__ import annotations

import importlib
import importlib.util
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from cleandata.core.coerce import canonical_str
from cleandata.core.frame import rows_of
from cleandata.errors import MissingExtraError

if TYPE_CHECKING:
    from cleandata.core.profile import FuzzyRule


def available() -> bool:
    return importlib.util.find_spec("rapidfuzz") is not None


def require() -> Any:
    """Import rapidfuzz, or stop with exit 2 naming the extra."""
    if not available():
        raise MissingExtraError("fuzzy", "fuzzy de-duplication (dedupe.fuzzy)")
    return importlib.import_module("rapidfuzz")


def fold(text: str) -> str:
    """Casefold, strip accents and collapse whitespace: the text a fuzzy scorer sees."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


@dataclass
class FuzzyResult:
    clusters: list[list[int]] = field(default_factory=list)
    scores: dict[tuple[int, int], float] = field(default_factory=dict)
    blocks: int = 0
    compared: int = 0
    skipped: list[tuple[str, int]] = field(default_factory=list)

    def partner(self, member: int) -> tuple[int, float]:
        """The highest-scoring direct match of ``member`` (ties: the earliest row)."""
        best: tuple[float, int] | None = None
        for (a, b), score in self.scores.items():
            if member not in (a, b):
                continue
            other = b if a == member else a
            if best is None or score > best[0] or (score == best[0] and other < best[1]):
                best = (score, other)
        if best is None:
            raise ValueError(f"row {member} has no match")
        return best[1], best[0]


def field_texts(frame: pd.DataFrame, columns: Sequence[str]) -> list[str | None]:
    texts: list[str | None] = []
    for values in rows_of(frame, columns):
        joined = fold(" ".join(canonical_str(value) for value in values if value is not None))
        texts.append(joined or None)
    return texts


def find_clusters(frame: pd.DataFrame, positions: Sequence[int], rule: FuzzyRule) -> FuzzyResult:
    """Clusters among the rows at ``positions`` (positional indexes into ``frame``)."""
    rapidfuzz = require()
    numpy: Any = importlib.import_module("numpy")
    result = FuzzyResult()
    block_values = rows_of(frame, rule.block_on)
    texts = [field_texts(frame, item.columns) for item in rule.fields]
    blocks: dict[tuple[str, ...], list[int]] = {}
    for position in positions:
        values = block_values[position]
        if any(value is None for value in values):
            continue
        blocks.setdefault(tuple(canonical_str(value) for value in values), []).append(position)
    result.blocks = len(blocks)
    parent = {position: position for position in positions}

    def root(position: int) -> int:
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]
        return position

    for key, members in blocks.items():
        if len(members) < 2:
            continue
        if len(members) > rule.max_block_size:
            result.skipped.append((", ".join(key), len(members)))
            continue
        result.compared += 1
        size = len(members)
        total = numpy.zeros((size, size))
        weight = numpy.zeros((size, size))
        for item, column_texts in zip(rule.fields, texts, strict=True):
            subset = [column_texts[position] for position in members]
            valid = numpy.array([text is not None for text in subset], dtype=bool)
            scorer = getattr(rapidfuzz.fuzz, item.scorer)
            scores = rapidfuzz.process.cdist(
                [text or "" for text in subset],
                [text or "" for text in subset],
                scorer=scorer,
                dtype=numpy.float64,
            )
            mask = numpy.outer(valid, valid)
            total = total + numpy.where(mask, scores * item.weight, 0.0)
            weight = weight + numpy.where(mask, item.weight, 0.0)
        safe = numpy.where(weight > 0, weight, 1.0)
        pair = numpy.where(weight > 0, total / safe, -1.0)
        matches = numpy.triu(pair >= rule.threshold, k=1)
        for i, j in zip(*numpy.nonzero(matches), strict=True):
            a, b = members[int(i)], members[int(j)]
            result.scores[(a, b)] = float(pair[int(i), int(j)])
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for position in positions:
        groups.setdefault(root(position), []).append(position)
    result.clusters = sorted(
        (sorted(members) for members in groups.values() if len(members) > 1), key=lambda members: members[0]
    )
    return result
