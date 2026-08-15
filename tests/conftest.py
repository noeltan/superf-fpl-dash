"""Shared builders for the ledger tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superf.ledger import Gameweek, ManagerScore  # noqa: E402
from superf.tiebreak import TiebreakStats  # noqa: E402

# The real league, in entry_id order as the API returns it.
LEAGUE = [
    {"id": "chris", "entry_id": 1641730},
    {"id": "jack", "entry_id": 1427521},
    {"id": "tianpin", "entry_id": 1567532},
    {"id": "noel", "entry_id": 1652821},
    {"id": "boonsiang", "entry_id": 1840731},
    {"id": "soonlee", "entry_id": 2488327},
    {"id": "sam", "entry_id": 2686159},
    {"id": "weihun", "entry_id": 4590653},
]

# Spec §3.8.7's worked example uses these two gameweeks.
GW1_POINTS = {
    "noel": 46, "jack": 68, "sam": 51, "weihun": 63,
    "soonlee": 72, "boonsiang": 61, "tianpin": 45, "chris": 64,
}
GW2_POINTS = {
    "noel": 68, "jack": 66, "sam": 65, "weihun": 54,
    "soonlee": 69, "boonsiang": 52, "tianpin": 64, "chris": 0,
}


@pytest.fixture
def league():
    return list(LEAGUE)


def make_gameweek(
    gw: int,
    month: str,
    points: dict[str, int],
    *,
    state: str = "final",
    note: str | None = None,
    stats: dict[str, TiebreakStats] | None = None,
    did_not_set: set[str] | None = None,
    inactive: set[str] | None = None,
    managers: list[dict] | None = None,
) -> Gameweek:
    """Build a Gameweek. Managers absent from ``points`` are inactive by default."""
    stats = stats or {}
    did_not_set = did_not_set or set()
    inactive = inactive or set()
    roster = [m["id"] for m in (managers or LEAGUE)]

    scores: dict[str, ManagerScore] = {}
    for manager in roster:
        active = manager in points and manager not in inactive
        scores[manager] = ManagerScore(
            points=points.get(manager) if active else None,
            did_not_set=manager in did_not_set,
            active=active,
            stats=stats.get(manager, TiebreakStats()),
        )
    return Gameweek(gw=gw, month=month, state=state, scores=scores, note=note)


def full_season(
    played: dict[int, dict[str, int]],
    *,
    managers: list[dict] | None = None,
    months: dict[int, str] | None = None,
    stats: dict[int, dict[str, TiebreakStats]] | None = None,
    inactive: dict[int, set[str]] | None = None,
    notes: dict[int, str] | None = None,
    total_gameweeks: int = 38,
) -> dict[int, Gameweek]:
    """A 38-gameweek calendar where only ``played`` gameweeks are Final."""
    months = months or {}
    stats = stats or {}
    inactive = inactive or {}
    notes = notes or {}
    roster = managers or LEAGUE
    calendar: dict[int, Gameweek] = {}
    for gw in range(1, total_gameweeks + 1):
        month = months.get(gw, "AUG" if gw <= 2 else "SEP")
        if gw in played:
            calendar[gw] = make_gameweek(
                gw, month, played[gw],
                stats=stats.get(gw),
                inactive=inactive.get(gw),
                note=notes.get(gw),
                managers=roster,
            )
        else:
            calendar[gw] = make_gameweek(
                gw, month, {}, state="upcoming", managers=roster
            )
    return calendar


AUGUST = [{"month": "AUG", "gameweeks": [1, 2]}]
