"""How good was the projection? — §12.2 Layer 1, marked rather than admired.

§12.3 already scores the *call*: the model's 1st and 2nd against the result.
This scores the thing underneath it, the deterministic xP ranking, because that
is what any tuning of :mod:`superf.projection` actually changes. A model tweak
that leaves the ranking alone changed nothing worth having.

The metric is deliberately about **manager order, not player accuracy**. A
manager's xP is a sum over eleven or fifteen players, so individual errors
partly cancel; optimising per-player RMSE would be optimising the wrong loss.
What the league cares about is who finishes above whom, and the pot only pays
two — hence a spread of measures rather than one number:

* ``spearman``  — the whole order, from RM46 down to the wooden spoon.
* ``pairwise``  — the fraction of manager pairs put in the right order. Robust,
  and the easiest to reason about: 0.5 is a coin flip.
* ``exact`` / ``podium`` / ``pair`` — §12.3's own three, applied to the
  projection so the two scorecards can be read side by side.

Nothing here fetches, and nothing here knows about chips or money. Give it two
orderings and it tells you how far apart they are.
"""

from __future__ import annotations

from itertools import combinations
from typing import Mapping, Sequence


def ranks(order: Sequence[str]) -> dict[str, int]:
    """``["a", "b"]`` -> ``{"a": 1, "b": 2}``."""
    return {manager: place for place, manager in enumerate(order, 1)}


def spearman(predicted: Sequence[str], actual: Sequence[str]) -> float | None:
    """Rank correlation over the managers both orderings contain.

    +1 is a perfect ordering, 0 is noise, -1 is exactly backwards. None when
    there are fewer than two managers to compare, because a correlation over
    one point is not a number, it is a division by zero waiting to happen.
    """
    predicted_ranks, actual_ranks = ranks(predicted), ranks(actual)
    shared = sorted(set(predicted_ranks) & set(actual_ranks))
    n = len(shared)
    if n < 2:
        return None
    # Re-rank within the shared set so a manager missing from one side does not
    # silently shift everybody below them.
    p = ranks([m for m in predicted if m in set(shared)])
    a = ranks([m for m in actual if m in set(shared)])
    d2 = sum((p[m] - a[m]) ** 2 for m in shared)
    return 1 - (6 * d2) / (n * (n * n - 1))


def pairwise(predicted: Sequence[str], actual: Sequence[str]) -> tuple[int, int]:
    """``(pairs ordered correctly, pairs compared)``."""
    predicted_ranks, actual_ranks = ranks(predicted), ranks(actual)
    shared = sorted(set(predicted_ranks) & set(actual_ranks))
    right = total = 0
    for x, y in combinations(shared, 2):
        total += 1
        if (predicted_ranks[x] < predicted_ranks[y]) == (actual_ranks[x] < actual_ranks[y]):
            right += 1
    return right, total


def score_ranking(predicted: Sequence[str], actual: Sequence[str]) -> dict:
    """Every measure at once, for one gameweek."""
    right, total = pairwise(predicted, actual)
    top_two_actual = set(actual[:2])
    return {
        "spearman": spearman(predicted, actual),
        "pairwise": right / total if total else None,
        "pairs_right": right,
        "pairs": total,
        # §12.3's three, so this table and the model's own record line up.
        "exact": bool(predicted and actual and predicted[0] == actual[0]),
        "podium": bool(predicted and predicted[0] in top_two_actual),
        "pair": bool(len(predicted) >= 2 and set(predicted[:2]) == top_two_actual),
    }


def aggregate(scores: Sequence[Mapping]) -> dict:
    """Season to date. Means over the gameweeks that produced a number."""
    graded = [s for s in scores if s.get("spearman") is not None]
    if not graded:
        return {"gameweeks": 0}
    pairs_right = sum(s["pairs_right"] for s in graded)
    pairs = sum(s["pairs"] for s in graded)
    return {
        "gameweeks": len(graded),
        "spearman": sum(s["spearman"] for s in graded) / len(graded),
        "pairwise": pairs_right / pairs if pairs else None,
        "pairs_right": pairs_right,
        "pairs": pairs,
        "exact": sum(1 for s in graded if s["exact"]),
        "podium": sum(1 for s in graded if s["podium"]),
        "pair": sum(1 for s in graded if s["pair"]),
    }
