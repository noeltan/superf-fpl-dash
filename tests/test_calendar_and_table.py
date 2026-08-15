"""Calendar derivation (§3.6, §11.1) and the derived Premier League table (§4)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from superf.backup import net_settlement
from superf.fplcal import (
    build_breaks,
    build_events,
    build_month_buckets,
    gameweek_state,
    match_window,
    month_of,
    parse_utc,
)
from superf.pltable import build_table

BOOT = Path(__file__).resolve().parent.parent / ".cache"


def fixture(**kwargs):
    base = {
        "team_h": 1, "team_a": 2, "team_h_score": None, "team_a_score": None,
        "started": False, "finished": False, "finished_provisional": False,
        "kickoff_time": "2026-08-21T19:00:00Z",
    }
    base.update(kwargs)
    return base


# --- §3.6 month buckets ------------------------------------------------------

def test_a_gameweek_belongs_to_the_month_its_deadline_falls_in_malaysia_time():
    # 30 Dec 18:30 UTC is 31 Dec in Malaysia — still December either way, but
    # the conversion is what the rule says, so it is what we do.
    assert month_of(parse_utc("2026-12-30T18:30:00Z")) == "DEC"
    # 31 Jul 17:00 UTC is 1 Aug in Malaysia, which changes the bucket.
    assert month_of(parse_utc("2026-07-31T17:00:00Z")) == "AUG"


def test_buckets_and_breaks_reproduce_the_real_season():
    """§3.6's table is an assertion about real deadlines, not an input."""
    deadlines = [
        "2026-08-21T17:30:00Z", "2026-08-28T17:30:00Z", "2026-09-04T17:30:00Z",
        "2026-09-12T12:30:00Z", "2026-09-18T17:30:00Z", "2026-10-10T12:30:00Z",
    ]
    events = build_events([{"id": i + 1, "deadline_time": d} for i, d in enumerate(deadlines)])
    buckets = {b["month"]: b["gameweeks"] for b in build_month_buckets(events)}
    assert buckets == {"AUG": [1, 2], "SEP": [3, 4, 5], "OCT": [6]}

    breaks = build_breaks(events)
    assert breaks == [
        {"after_gw": 5, "next_gw": 6, "days": 21, "resumes": "2026-10-10T12:30:00Z"}
    ]


# --- §11.1 the five states ---------------------------------------------------

DEADLINE = parse_utc("2026-08-21T17:30:00Z")
BEFORE = datetime(2026, 8, 20, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_upcoming_before_the_deadline():
    assert gameweek_state(DEADLINE, [fixture()], BEFORE) == "upcoming"


def test_locked_after_the_deadline_with_no_fixture_started():
    assert gameweek_state(DEADLINE, [fixture()], AFTER) == "locked"


def test_live_when_a_fixture_has_started_and_not_finished():
    fixtures = [fixture(started=True), fixture()]
    assert gameweek_state(DEADLINE, fixtures, AFTER) == "live"


def test_provisional_when_all_finished_provisional_but_not_finished():
    """The trap: bonus moves after the whistle and can flip the pot winner."""
    fixtures = [fixture(started=True, finished_provisional=True) for _ in range(3)]
    assert gameweek_state(DEADLINE, fixtures, AFTER) == "provisional"


def test_final_only_when_every_fixture_is_finished():
    fixtures = [
        fixture(started=True, finished=True, finished_provisional=True) for _ in range(3)
    ]
    assert gameweek_state(DEADLINE, fixtures, AFTER) == "final"
    fixtures[-1]["finished"] = False
    assert gameweek_state(DEADLINE, fixtures, AFTER) != "final"


def test_match_window_opens_at_first_kickoff_and_closes_150_minutes_after_the_last():
    fixtures = [
        fixture(kickoff_time="2026-08-22T11:30:00Z"),
        fixture(kickoff_time="2026-08-22T16:30:00Z"),
    ]
    start, end = match_window(fixtures)
    assert start == parse_utc("2026-08-22T11:30:00Z")
    assert end == parse_utc("2026-08-22T19:00:00Z")


def test_no_window_when_nothing_is_scheduled():
    assert match_window([]) is None


# --- §4 the derived PL table -------------------------------------------------

TEAMS = {
    1: {"name": "Arsenal", "short_name": "ARS"},
    2: {"name": "Chelsea", "short_name": "CHE"},
    3: {"name": "Everton", "short_name": "EVE"},
}


def test_table_counts_wins_draws_losses_and_orders_by_points_then_gd():
    fixtures = [
        fixture(team_h=1, team_a=2, team_h_score=3, team_a_score=0, finished=True,
                kickoff_time="2026-08-21T19:00:00Z"),
        fixture(team_h=3, team_a=1, team_h_score=1, team_a_score=1, finished=True,
                kickoff_time="2026-08-28T19:00:00Z"),
        fixture(team_h=2, team_a=3, team_h_score=2, team_a_score=0, finished=True,
                kickoff_time="2026-09-04T19:00:00Z"),
    ]
    table = build_table(fixtures, TEAMS)
    assert [r["team"] for r in table] == [1, 2, 3]
    arsenal = table[0]
    assert (arsenal["p"], arsenal["w"], arsenal["d"], arsenal["l"]) == (2, 1, 1, 0)
    assert (arsenal["gf"], arsenal["ga"], arsenal["gd"], arsenal["pts"]) == (4, 1, 3, 4)
    assert arsenal["form"] == ["W", "D"]
    assert [r["pos"] for r in table] == [1, 2, 3]


def test_unfinished_fixtures_are_ignored():
    fixtures = [fixture(team_h=1, team_a=2, team_h_score=5, team_a_score=0, finished=False)]
    table = build_table(fixtures, TEAMS)
    assert all(row["p"] == 0 for row in table)


def test_preseason_table_is_twenty_clubs_on_zero():
    """§9.6 — pre-season is a real state that will be seen."""
    table = build_table([], TEAMS)
    assert len(table) == 3
    assert all(row["pts"] == 0 and row["form"] == [] for row in table)


def test_form_keeps_only_the_last_five_results():
    fixtures = [
        fixture(team_h=1, team_a=2, team_h_score=1, team_a_score=0, finished=True,
                kickoff_time=f"2026-09-{day:02d}T19:00:00Z")
        for day in range(1, 8)
    ]
    table = build_table(fixtures, TEAMS)
    arsenal = next(r for r in table if r["team"] == 1)
    assert arsenal["p"] == 7
    assert arsenal["form"] == ["W"] * 5


# --- the settle-up ------------------------------------------------------------

def test_settlement_squares_everybody_with_at_most_n_minus_one_payments():
    totals = {"a": 116, "b": 4, "c": -20, "d": -20, "e": -20, "f": -20, "g": -20, "h": -20}
    payments = net_settlement(totals)
    assert len(payments) <= len(totals) - 1

    net = {m: 0.0 for m in totals}
    for payment in payments:
        net[payment["from"]] -= payment["amount"]
        net[payment["to"]] += payment["amount"]
    assert {m: round(v, 2) for m, v in net.items()} == {m: float(v) for m, v in totals.items()}


def test_settlement_is_empty_when_nobody_owes_anything():
    assert net_settlement({"a": 0, "b": 0}) == []


def test_settlement_is_deterministic():
    totals = {"a": 50, "b": 50, "c": -50, "d": -50}
    assert net_settlement(totals) == net_settlement(totals)
