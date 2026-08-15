"""Spec §4.2 — `raw/` is append-only; `data.json` is disposable.

"You should be able to delete `data.json` and rebuild it byte-identically from
`raw/` plus `corrections.json`. **Make that a test.**"

This is the property that makes the book auditable. If a tiebreak bug is found
in March, the fix has to be recomputed *from source* — and that only works if
the snapshots carry everything the ledger needs and the rebuild is deterministic.
"""

from __future__ import annotations

import json

import pytest

from build import scores_from_snapshot, settled_date
from conftest import LEAGUE
from superf import snapshot as snapshot_mod
from superf.ledger import Gameweek, settle
from superf.tiebreak import TiebreakStats

MANAGERS = [dict(m, started_event=1) for m in LEAGUE]

PICKS = {
    1652821: {
        "active_chip": None,
        "entry_history": {"event": 1, "points": 46, "event_transfers": 1,
                          "event_transfers_cost": 0},
        "picks": [
            {"element": 1, "position": 1, "multiplier": 1, "is_captain": False,
             "is_vice_captain": False},
            {"element": 2, "position": 2, "multiplier": 2, "is_captain": True,
             "is_vice_captain": False},
            {"element": 3, "position": 12, "multiplier": 0, "is_captain": False,
             "is_vice_captain": True},
        ],
        "automatic_subs": [{"element_in": 3, "element_out": 1}],
    }
}

LIVE = {
    "elements": [
        {"id": 1, "stats": {"minutes": 0, "goals_scored": 0, "assists": 0,
                            "goals_conceded": 0, "yellow_cards": 0, "red_cards": 0,
                            "bps": 0, "total_points": 0}},
        {"id": 2, "stats": {"minutes": 90, "goals_scored": 2, "assists": 1,
                            "goals_conceded": 1, "yellow_cards": 1, "red_cards": 0,
                            "bps": 40, "total_points": 13}},
        {"id": 3, "stats": {"minutes": 90, "goals_scored": 0, "assists": 1,
                            "goals_conceded": 1, "yellow_cards": 0, "red_cards": 0,
                            "bps": 20, "total_points": 5}},
        # A player nobody in the league owns — must not survive pruning.
        {"id": 999, "stats": {"minutes": 90, "goals_scored": 3, "bps": 60,
                              "total_points": 20}},
    ]
}

HISTORIES = {
    int(m["entry_id"]): {
        "current": [{"event": 1, "points": 40 + i, "event_transfers": 1,
                     "event_transfers_cost": 0, "total_points": 40 + i,
                     "rank": 1, "points_on_bench": 2}],
        "chips": [],
    }
    for i, m in enumerate(MANAGERS)
}

FIXTURES = [
    {"id": 1, "event": 1, "team_h": 1, "team_a": 2, "team_h_score": 2,
     "team_a_score": 1, "team_h_difficulty": 2, "team_a_difficulty": 4,
     "kickoff_time": "2026-08-21T19:00:00Z", "minutes": 90, "started": True,
     "finished": True, "finished_provisional": True, "stats": ["a lot of noise"]},
]


@pytest.fixture
def record():
    return snapshot_mod.build(
        gw=1, captured_at="2026-08-25T06:00:00Z", managers=MANAGERS,
        fixtures=FIXTURES, histories=HISTORIES, picks=PICKS, live=LIVE,
    )


# --- pruning ------------------------------------------------------------------

def test_only_owned_players_survive_the_prune(record):
    """The live endpoint carries every player in the game; the league owns ~120."""
    assert set(record["elements"]) == {"1", "2", "3"}
    assert "999" not in record["elements"]


def test_only_the_stats_the_ledger_needs_are_kept(record):
    assert set(record["elements"]["2"]) == set(snapshot_mod.KEPT_STATS)


def test_fixture_noise_is_dropped(record):
    assert "stats" not in record["fixtures"][0]
    assert record["fixtures"][0]["team_h_score"] == 2


def test_a_snapshot_is_small():
    """§4.2 asks for the real figure to be measured, not extrapolated."""
    record = snapshot_mod.build(
        gw=1, captured_at="2026-08-25T06:00:00Z", managers=MANAGERS,
        fixtures=FIXTURES * 10, histories=HISTORIES,
        picks={m["entry_id"]: PICKS[1652821] for m in MANAGERS},
        live={"elements": [
            {"id": i, "stats": {k: 1 for k in snapshot_mod.KEPT_STATS}}
            for i in range(1, 600)
        ]},
    )
    size = len(json.dumps(record, sort_keys=True, separators=(",", ":")))
    assert size < 40_000, f"snapshot is {size} bytes — pruning is not working"


# --- determinism and rebuild --------------------------------------------------

def test_a_snapshot_serialises_byte_identically_every_time(record):
    again = snapshot_mod.build(
        gw=1, captured_at="2026-08-25T06:00:00Z", managers=MANAGERS,
        fixtures=FIXTURES, histories=HISTORIES, picks=PICKS, live=LIVE,
    )
    dump = lambda r: json.dumps(r, sort_keys=True, separators=(",", ":"))
    assert dump(record) == dump(again)


def test_a_snapshot_is_never_overwritten(tmp_path, record):
    path = snapshot_mod.write(record, root=tmp_path)
    first = path.read_bytes()
    tampered = dict(record, captured_at="2027-01-01T00:00:00Z")
    snapshot_mod.write(tampered, root=tmp_path)
    assert path.read_bytes() == first, "raw/ must be append-only (§4.2)"


def test_scores_rebuild_from_the_snapshot_alone(record):
    """No API, no data.json — just the frozen record."""
    scores = scores_from_snapshot(record, MANAGERS)
    assert set(scores) == {m["id"] for m in MANAGERS}
    noel = scores["noel"]
    assert noel.points == 40 + [m["id"] for m in MANAGERS].index("noel")
    assert noel.active is True and noel.did_not_set is False


def test_the_tiebreak_ladder_rebuilds_from_the_snapshot(record):
    """§4.2 reason 3: per-player detail is not recoverable from data.json."""
    scores = scores_from_snapshot(record, MANAGERS)
    noel = scores["noel"]
    # Element 1 played 0 minutes and was auto-subbed for element 3, so the XI is
    # {3, 2}: 2 goals, 2 assists, 2 conceded, 1 card.
    assert noel.stats == TiebreakStats(goals=2, assists=2, conceded=2, cards=1)


def test_a_manager_with_no_history_row_rebuilds_as_a_missed_deadline(record):
    stripped = json.loads(json.dumps(record))
    del stripped["history"]["1652821"]
    scores = scores_from_snapshot(stripped, MANAGERS)
    assert scores["noel"].did_not_set is True
    assert scores["noel"].points == 0
    assert scores["noel"].active is True


def test_the_full_ledger_rebuilds_from_snapshots_alone(record):
    """The §4.2 guarantee, end to end: raw/ -> scores -> settled ledger."""
    calendar = {}
    for gw in range(1, 39):
        if gw == 1:
            calendar[gw] = Gameweek(
                gw=1, month="AUG", state="final",
                scores=scores_from_snapshot(record, MANAGERS),
            )
        else:
            calendar[gw] = Gameweek(gw=gw, month="AUG", state="upcoming", scores={})

    first = settle(MANAGERS, calendar, [{"month": "AUG", "gameweeks": [1]}], 38)
    second = settle(MANAGERS, calendar, [{"month": "AUG", "gameweeks": [1]}], 38)
    assert first.weekly == second.weekly
    assert first.totals == second.totals
    assert sum(first.totals.values()) == 0


def test_settled_date_is_the_last_kickoff():
    assert settled_date(FIXTURES) == "2026-08-21"
    assert settled_date([]) == ""


# --- the season mirror --------------------------------------------------------

SEASON_ARGS = dict(
    events=[{"id": 2, "deadline_time": "2026-08-28T17:30:00Z", "noise": "dropped"},
            {"id": 1, "deadline_time": "2026-08-21T17:30:00Z"}],
    teams=[{"id": 2, "name": "Chelsea", "short_name": "CHE", "strength": 4},
           {"id": 1, "name": "Arsenal", "short_name": "ARS", "strength": 5}],
    managers=MANAGERS,
    fixtures=FIXTURES,
    league_name="SuperF",
)


def test_the_season_mirror_round_trips(tmp_path):
    """Without the calendar, clubs and roster, `raw/` rebuilds the ledger but
    not data.json — and §4.2 asks for the file."""
    snapshot_mod.write_season(root=tmp_path, **SEASON_ARGS)
    loaded = snapshot_mod.load_season(root=tmp_path)
    assert [e["id"] for e in loaded["events"]] == [1, 2]      # sorted
    assert [t["id"] for t in loaded["teams"]] == [1, 2]
    assert "noise" not in loaded["events"][0]                 # pruned
    assert "strength" not in loaded["teams"][0]
    assert len(loaded["managers"]) == len(MANAGERS)
    assert loaded["league_name"] == "SuperF"


def test_the_season_mirror_is_deterministic(tmp_path):
    first = snapshot_mod.write_season(root=tmp_path, **SEASON_ARGS).read_bytes()
    second = snapshot_mod.write_season(root=tmp_path, **SEASON_ARGS).read_bytes()
    assert first == second


def test_no_season_mirror_reads_as_absent(tmp_path):
    assert snapshot_mod.load_season(root=tmp_path) is None


# --- §11.4 bonus flips --------------------------------------------------------

def test_a_bonus_flip_is_named_permanently(tmp_path):
    snapshot_mod.record_provisional_leader(3, "jack", "2026-09-06T20:00:00Z", root=tmp_path)
    change = snapshot_mod.bonus_change_for(3, "soonlee", "2026-09-06", root=tmp_path)
    assert change == {"from": "jack", "to": "soonlee", "at": "2026-09-06"}


def test_no_note_when_bonus_confirmed_the_same_winner(tmp_path):
    snapshot_mod.record_provisional_leader(3, "soonlee", "2026-09-06T20:00:00Z", root=tmp_path)
    assert snapshot_mod.bonus_change_for(3, "soonlee", "2026-09-06", root=tmp_path) is None


def test_no_note_when_the_gameweek_was_never_observed_provisional(tmp_path):
    assert snapshot_mod.bonus_change_for(9, "soonlee", "2026-11-01", root=tmp_path) is None


def test_the_provisional_leader_is_recorded_once(tmp_path):
    snapshot_mod.record_provisional_leader(3, "jack", "2026-09-06T20:00:00Z", root=tmp_path)
    snapshot_mod.record_provisional_leader(3, "sam", "2026-09-06T21:00:00Z", root=tmp_path)
    payload = json.loads(snapshot_mod.provisional_path(3, tmp_path).read_text())
    assert payload["leader"] == "jack"
