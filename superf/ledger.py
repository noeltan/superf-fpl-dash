"""Spec §3.8.4 — order of operations, and §3.8.5 — the invariants.

``N`` is evaluated **per gameweek**, not once (§3.8.6). If a ninth manager
joins at GW5, gameweeks 1-4 settle at N=8 and are never recomputed; GW5 onward
settle at N=9. A month bucket spanning the join is computed gameweek by
gameweek, not with one blended N — which is why every stake below is a
per-manager mapping rather than a scalar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .config import (
    MONTHLY_SPLIT,
    MONTHLY_STAKE_PER_GW_RM,
    SEASON_SPLIT,
    SEASON_STAKE_RM,
    WEEKLY_SPLIT,
    WEEKLY_STAKE_RM,
)
from .money import LedgerError, assert_zero_sum, distribute, rm_to_sen
from .tiebreak import EMPTY, Ranking, TiebreakStats, rank

WEEKLY_STAKE_SEN = rm_to_sen(WEEKLY_STAKE_RM)
MONTHLY_STAKE_PER_GW_SEN = rm_to_sen(MONTHLY_STAKE_PER_GW_RM)
SEASON_STAKE_SEN = rm_to_sen(SEASON_STAKE_RM)


@dataclass
class ManagerScore:
    """One manager's gameweek, as the ledger needs it."""

    points: int | None = None
    hits: int = 0
    transfers: int = 0
    chip: str | None = None
    did_not_set: bool = False
    active: bool = False  # was this manager in the league for this gameweek?
    stats: TiebreakStats = field(default_factory=TiebreakStats)

    @property
    def net_points(self) -> int:
        """Points as they count for money. A missed deadline scores 0 and still pays."""
        return 0 if self.points is None else int(self.points)


@dataclass
class Gameweek:
    gw: int
    month: str
    state: str
    scores: dict[str, ManagerScore] = field(default_factory=dict)
    note: str | None = None  # "double gameweek" | "blank gameweek" | "postponed" | None
    # §11.4 — set when confirmed bonus moved the pot from one manager to another.
    bonus_change: dict | None = None

    @property
    def is_final(self) -> bool:
        return self.state == "final"

    @property
    def pays_pot(self) -> bool:
        """A postponed or cancelled round pays no pot (§10)."""
        return self.is_final and self.note != "postponed" and bool(self.active_managers)

    @property
    def active_managers(self) -> list[str]:
        return [m for m, s in self.scores.items() if s.active]


@dataclass
class Settlement:
    """Everything §3.8.4 produces, in sen."""

    weekly: dict[int, dict[str, int]] = field(default_factory=dict)
    monthly: dict[str, dict[str, int]] = field(default_factory=dict)
    season: dict[str, int] = field(default_factory=dict)
    projected_season: dict[str, int] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    rankings: dict[int, Ranking] = field(default_factory=dict)
    month_rankings: dict[str, Ranking] = field(default_factory=dict)
    season_ranking: Ranking | None = None
    season_is_final: bool = False


def _order_key(managers: Sequence[Mapping]) -> dict[str, int]:
    return {m["id"]: int(m["entry_id"]) for m in managers}


def weekly_ledger(gameweek: Gameweek, order_key: Mapping[str, int]) -> tuple[dict[str, int], Ranking]:
    """Step 2 of §3.8.4 — one settled gameweek."""
    active = gameweek.active_managers
    if not gameweek.pays_pot:
        return {m: 0 for m in active}, Ranking()

    points = {m: gameweek.scores[m].net_points for m in active}
    stats = {m: gameweek.scores[m].stats for m in active}
    ranking = rank(points, stats, order_key)
    stakes = {m: WEEKLY_STAKE_SEN for m in active}
    ledger = distribute(stakes, WEEKLY_SPLIT, ranking.groups, order_key)
    assert_zero_sum(ledger, f"weekly GW{gameweek.gw}")
    return ledger, ranking


def monthly_ledger(
    bucket_gws: Sequence[int],
    gameweeks: Mapping[int, Gameweek],
    order_key: Mapping[str, int],
) -> tuple[dict[str, int], Ranking]:
    """Step 3 of §3.8.4 — a month bucket, every gameweek of which is Final.

    The stake is ``RM5 × gameweeks the manager was active for`` (§3.7 read
    through §3.8.6), so the pot is ``Σ_g 5 × N_g`` and a bucket spanning a join
    still balances without inventing a blended N.
    """
    paying = [gw for gw in bucket_gws if gameweeks[gw].pays_pot]
    if not paying:
        return {}, Ranking()

    stakes: dict[str, int] = {}
    points: dict[str, int] = {}
    stats: dict[str, TiebreakStats] = {}
    for gw in paying:
        gameweek = gameweeks[gw]
        for manager in gameweek.active_managers:
            score = gameweek.scores[manager]
            stakes[manager] = stakes.get(manager, 0) + MONTHLY_STAKE_PER_GW_SEN
            points[manager] = points.get(manager, 0) + score.net_points
            stats[manager] = stats.get(manager, EMPTY) + score.stats

    ranking = rank(points, stats, order_key)
    ledger = distribute(stakes, MONTHLY_SPLIT, ranking.groups, order_key)
    assert_zero_sum(ledger, f"monthly {gameweeks[paying[0]].month}")
    return ledger, ranking


def season_ledger(
    managers: Sequence[str],
    points: Mapping[str, int],
    stats: Mapping[str, TiebreakStats],
    order_key: Mapping[str, int],
) -> tuple[dict[str, int], Ranking]:
    """Step 4 of §3.8.4. Everyone in the league at the end pays the season stake."""
    if not managers:
        return {}, Ranking()
    ranking = rank({m: points.get(m, 0) for m in managers}, stats, order_key)
    stakes = {m: SEASON_STAKE_SEN for m in managers}
    ledger = distribute(stakes, SEASON_SPLIT, ranking.groups, order_key)
    assert_zero_sum(ledger, "season")
    return ledger, ranking


def settle(
    managers: Sequence[Mapping],
    gameweeks: Mapping[int, Gameweek],
    month_buckets: Sequence[Mapping],
    expected_gameweeks: int,
) -> Settlement:
    """Run §3.8.4 end to end and assert §3.8.5. Fail loudly on any breach."""
    order_key = _order_key(managers)
    ids = [m["id"] for m in managers]
    result = Settlement()

    # The roster and the scores must agree. They can drift when a manager joins
    # between a league fetch and a history fetch, and a stranger in the scores
    # would otherwise reach `distribute` and blow up somewhere less obvious.
    known = set(ids)
    for gw, gameweek in gameweeks.items():
        unknown = set(gameweek.scores) - known
        if unknown:
            raise LedgerError(
                f"GW{gw} scores reference managers missing from the roster: {sorted(unknown)}"
            )

    # 2 — weekly, per Final gameweek.
    for gw in sorted(gameweeks):
        gameweek = gameweeks[gw]
        if not gameweek.is_final:
            continue
        ledger, ranking = weekly_ledger(gameweek, order_key)
        result.weekly[gw] = ledger
        result.rankings[gw] = ranking

    # 3 — monthly, per bucket whose every gameweek is Final.
    final_gws = {gw for gw, g in gameweeks.items() if g.is_final}
    for bucket in month_buckets:
        bucket_gws = list(bucket["gameweeks"])
        if not bucket_gws or not all(gw in final_gws for gw in bucket_gws):
            continue
        ledger, ranking = monthly_ledger(bucket_gws, gameweeks, order_key)
        if ledger:
            result.monthly[bucket["month"]] = ledger
            result.month_rankings[bucket["month"]] = ranking

    # 4 — season, banked only once GW38 is Final.
    season_points: dict[str, int] = {}
    season_stats: dict[str, TiebreakStats] = {}
    league_members: list[str] = []
    for manager in ids:
        played = [g for g in gameweeks.values() if g.is_final and g.scores.get(manager, ManagerScore()).active]
        if not played:
            continue
        league_members.append(manager)
        season_points[manager] = sum(g.scores[manager].net_points for g in played)
        total = EMPTY
        for g in played:
            total = total + g.scores[manager].stats
        season_stats[manager] = total

    last_gw = max(gameweeks) if gameweeks else 0
    season_final = bool(gameweeks) and gameweeks[last_gw].is_final and last_gw == expected_gameweeks

    if league_members:
        ledger, ranking = season_ledger(league_members, season_points, season_stats, order_key)
        result.season_ranking = ranking
        if season_final:
            result.season = ledger
            result.season_is_final = True
        else:
            # DISPLAY ONLY, never banked (§3.8.4 step 4, §3.8.5).
            result.projected_season = ledger

    # 5 — totals. Projected season is excluded until GW38 is Final.
    totals = {m: 0 for m in ids}
    for ledger in list(result.weekly.values()) + list(result.monthly.values()):
        for manager, value in ledger.items():
            totals[manager] = totals.get(manager, 0) + value
    for manager, value in result.season.items():
        totals[manager] = totals.get(manager, 0) + value
    result.totals = totals

    # 6 — assert every invariant (§3.8.5).
    _assert_invariants(result, gameweeks, expected_gameweeks)
    return result


def _assert_invariants(
    result: Settlement, gameweeks: Mapping[int, Gameweek], expected_gameweeks: int
) -> None:
    for gw, ledger in result.weekly.items():
        assert_zero_sum(ledger, f"weekly GW{gw}")
    for month, ledger in result.monthly.items():
        assert_zero_sum(ledger, f"monthly {month}")
    assert_zero_sum(result.season, "season")
    assert_zero_sum(result.projected_season, "projected season")
    assert_zero_sum(result.totals, "overall totals")

    if len(gameweeks) != expected_gameweeks:
        raise LedgerError(
            f"expected {expected_gameweeks} gameweeks in the calendar, found {len(gameweeks)}"
        )

    # Every manager appears in every gameweek they were active for.
    for gw, gameweek in gameweeks.items():
        if not gameweek.is_final or not gameweek.pays_pot:
            continue
        settled = result.weekly.get(gw, {})
        for manager in gameweek.active_managers:
            if manager not in settled:
                raise LedgerError(f"{manager!r} was active in GW{gw} but is missing from its ledger")

    if result.projected_season and result.season:
        raise LedgerError("season is both projected and banked")
    if result.projected_season:
        banked = set(result.totals)
        for manager, value in result.projected_season.items():
            if value and manager in banked:
                contribution = sum(
                    ledger.get(manager, 0)
                    for ledger in list(result.weekly.values()) + list(result.monthly.values())
                )
                if result.totals[manager] != contribution:
                    raise LedgerError(
                        f"projected season leaked into banked total for {manager!r}"
                    )
