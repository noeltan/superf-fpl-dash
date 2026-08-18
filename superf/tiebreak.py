"""Spec §3.5 — the official FPL Head-to-Head tiebreak sequence.

Four levels, applied in order until one manager is ahead:

    1. goals scored      most wins
    2. assists           most wins
    3. goals conceded    fewest wins   (starting XI only)
    4. yellow + red      fewest wins
    5. split the pot                   (ours — FPL defines no fifth level,
                                        because a H2H match must produce a
                                        winner and a pot need not)

Pinned decisions, all of which are load-bearing:

* **Raw counts, not FPL points.** The armband does not double goals or assists
  for tiebreak purposes. A captained hat-trick counts as 3.
* **After auto-subs.** The XI is the final one FPL settles on, so a tie can only
  be resolved once the gameweek is Final — never Live or Provisional (§11.4).
* **Goals conceded double-counts by design.** Three players from one club means
  that club's conceded goals count three times. That is how FPL's own rule
  behaves; do not "fix" it.
* **Monthly and season ties** use the same four metrics summed across every
  gameweek in the bucket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

LEVEL_LABELS = {1: "goals", 2: "assists", 3: "goals conceded", 4: "cards"}


@dataclass(frozen=True)
class TiebreakStats:
    """Raw counts over the post-auto-sub starting XI, for one bucket."""

    goals: int = 0
    assists: int = 0
    conceded: int = 0
    cards: int = 0

    def __add__(self, other: "TiebreakStats") -> "TiebreakStats":
        return TiebreakStats(
            self.goals + other.goals,
            self.assists + other.assists,
            self.conceded + other.conceded,
            self.cards + other.cards,
        )

    @property
    def key(self) -> tuple[int, int, int, int]:
        """Sort key: lower is better, so most-wins metrics are negated."""
        return (-self.goals, -self.assists, self.conceded, self.cards)

    def level_values(self) -> tuple[int, int, int, int]:
        return (self.goals, self.assists, self.conceded, self.cards)


EMPTY = TiebreakStats()


def ladder() -> list[dict]:
    """The §3.5 ladder as label + direction, for anything that explains it.

    The direction is read out of the sort key rather than written down again, so
    flipping a sign in ``key`` changes what the dashboard tells people, instead
    of leaving the two quietly disagreeing.
    """
    probe = TiebreakStats(goals=1, assists=1, conceded=1, cards=1).key
    return [
        {
            "level": level,
            "label": LEVEL_LABELS[level],
            "direction": "most" if probe[level - 1] < 0 else "fewest",
        }
        for level in sorted(LEVEL_LABELS)
    ]


def starting_xi(picks_payload: Mapping) -> list[int]:
    """The final XI FPL settles on, after automatic substitutions.

    ``picks`` carries the manager's selection with bench players at
    ``multiplier == 0``. ``automatic_subs`` carries the swaps FPL applied at
    gameweek end. Applying the swaps ourselves is robust whether or not the
    endpoint has rewritten the multipliers by the time we read it.
    """
    xi = [p["element"] for p in picks_payload.get("picks", []) if p.get("multiplier", 0) > 0]
    for sub in picks_payload.get("automatic_subs", []) or []:
        out_element, in_element = sub.get("element_out"), sub.get("element_in")
        if out_element in xi:
            xi[xi.index(out_element)] = in_element
        elif in_element not in xi:
            xi.append(in_element)
    return xi


def stats_for_xi(xi: Sequence[int], element_stats: Mapping[int, Mapping]) -> TiebreakStats:
    """Sum the four raw metrics across an XI. No captain multiplier applied."""
    goals = assists = conceded = cards = 0
    for element in xi:
        stats = element_stats.get(element) or {}
        goals += int(stats.get("goals_scored", 0) or 0)
        assists += int(stats.get("assists", 0) or 0)
        conceded += int(stats.get("goals_conceded", 0) or 0)
        cards += int(stats.get("yellow_cards", 0) or 0) + int(stats.get("red_cards", 0) or 0)
    return TiebreakStats(goals, assists, conceded, cards)


def first_differing_level(a: TiebreakStats, b: TiebreakStats) -> int | None:
    """Which ladder level separates ``a`` from ``b``, or None if all four are level."""
    for level, (left, right) in enumerate(zip(a.level_values(), b.level_values()), start=1):
        if left != right:
            return level
    return None


def describe(level: int, winner: TiebreakStats, runner_up: TiebreakStats) -> dict:
    """The display line §3.5 requires: ``Won on assists (4 v 2)``.

    An unexplained tiebreak is exactly what the old sheet did wrong.
    """
    index = level - 1
    return {
        "level": level,
        "text": (
            f"Won on {LEVEL_LABELS[level]} "
            f"({winner.level_values()[index]} v {runner_up.level_values()[index]})"
        ),
    }


@dataclass
class Ranking:
    """Finishing order as groups. An inner list longer than one is a split pot."""

    groups: list[list[str]] = field(default_factory=list)
    tiebreak: dict | None = None  # only set when a tiebreak decided first place

    @property
    def winners(self) -> list[str]:
        return list(self.groups[0]) if self.groups else []

    @property
    def runners_up(self) -> list[str]:
        """Whoever holds second place — empty if a tie for first swallowed it.

        The weekly pot pays 70/30, so second place is a paid place and the view
        has to be able to name it. When two managers tie for first they take
        both shares between them and there is no second place to name.
        """
        if len(self.groups) < 2 or len(self.groups[0]) > 1:
            return []
        return list(self.groups[1])

    def place_of(self, manager: str) -> int | None:
        """1-indexed finishing place; tied managers share the earliest place."""
        place = 1
        for group in self.groups:
            if manager in group:
                return place
            place += len(group)
        return None


def rank(
    points: Mapping[str, int],
    stats: Mapping[str, TiebreakStats],
    order_key: Mapping[str, int],
) -> Ranking:
    """Rank managers by points, splitting level scores with the §3.5 ladder.

    Args:
        points: ``{manager: net_points}`` for everyone active in the bucket.
        stats: ``{manager: TiebreakStats}`` summed over the same bucket.
        order_key: ``{manager: entry_id}``, for deterministic ordering inside a
            group that survives all four levels.
    """
    if not points:
        return Ranking()

    by_points: dict[int, list[str]] = {}
    for manager, value in points.items():
        by_points.setdefault(value, []).append(manager)

    groups: list[list[str]] = []
    for value in sorted(by_points, reverse=True):
        contenders = by_points[value]
        if len(contenders) == 1:
            groups.append(contenders)
            continue
        # Level score — walk the ladder.
        contenders.sort(key=lambda m: (stats.get(m, EMPTY).key, order_key.get(m, 0)))
        current: list[str] = [contenders[0]]
        for manager in contenders[1:]:
            if stats.get(manager, EMPTY).key == stats.get(current[0], EMPTY).key:
                current.append(manager)  # all four levels equal — split (level 5)
            else:
                groups.append(current)
                current = [manager]
        groups.append(current)

    ranking = Ranking(groups=groups)

    # Explain the tiebreak only when one actually decided the pot, i.e. someone
    # else posted the same score as the winner but finished behind.
    if groups:
        top = groups[0]
        top_points = points[top[0]]
        level_on_points = [m for m, v in points.items() if v == top_points]
        if len(level_on_points) > len(top):
            runner_up = next(
                (m for g in groups[1:] for m in g if points[m] == top_points), None
            )
            if runner_up is not None:
                level = first_differing_level(stats.get(top[0], EMPTY), stats.get(runner_up, EMPTY))
                if level is not None:
                    ranking.tiebreak = describe(
                        level, stats.get(top[0], EMPTY), stats.get(runner_up, EMPTY)
                    )
    return ranking
