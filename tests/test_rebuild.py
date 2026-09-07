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

from build import build_gameweeks, scores_from_snapshot, settled_date
from conftest import LEAGUE
from superf import snapshot as snapshot_mod
from superf.ledger import Gameweek, settle
from superf.money import LedgerError
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
        # Captain: 2 x 19, plus element 3's 5 off the bench, is the 43 noel's
        # history row books — a snapshot must agree with itself to be frozen.
        {"id": 2, "stats": {"minutes": 90, "goals_scored": 2, "assists": 1,
                            "goals_conceded": 1, "yellow_cards": 1, "red_cards": 0,
                            "bps": 40, "total_points": 19}},
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


def test_no_history_row_and_no_squad_is_non_participation(record):
    """Not a missed deadline — FPL rolls the previous squad over and it scores
    normally. A true 0 means the entry never played that gameweek at all, and
    they are in the league, so they still pay."""
    stripped = json.loads(json.dumps(record))
    del stripped["history"]["1652821"]
    del stripped["picks"]["1652821"]
    scores = scores_from_snapshot(stripped, MANAGERS)
    assert scores["noel"].did_not_set is True
    assert scores["noel"].points == 0
    assert scores["noel"].active is True


def test_a_squad_with_no_history_row_refuses_to_publish(record):
    """The dangerous case: they fielded a team, so the missing row is a gap in
    what the API returned. Booking 0 would charge them for a week they played,
    and the snapshot freezes that verdict until somebody disputes a total in
    May — so the build stops instead."""
    stripped = json.loads(json.dumps(record))
    del stripped["history"]["1652821"]
    assert stripped["picks"]["1652821"]["picks"], "fixture must still hold a squad"

    with pytest.raises(LedgerError) as caught:
        scores_from_snapshot(stripped, MANAGERS)
    assert "noel" in str(caught.value)
    assert "no history row" in str(caught.value)


# --- the round FPL has not closed --------------------------------------------
# GW3 2026/27: every fixture said finished on the Monday morning, the snapshot
# froze, and the history rows were still Saturday's — up to 28 points short per
# manager, and the weekly pot booked to the wrong person. The record carries
# the squads and the per-player points, so it can tell on its own.

def test_squad_points_follow_fpl_arithmetic(record):
    """Captain doubled, bench ignored, auto-subs applied: 2 x 19 + 5."""
    picks = record["picks"]["1652821"]
    assert snapshot_mod.points_from_picks(picks, record["elements"]) == 43


def test_squad_points_survive_fpl_rewriting_the_multipliers(record):
    """After processing, FPL rewrites the subbed-in player's multiplier to 1
    and the subbed-out one's to 0. Either reading totals the same."""
    rewritten = json.loads(json.dumps(record["picks"]["1652821"]))
    for pick in rewritten["picks"]:
        if pick["element"] == 1:
            pick["multiplier"] = 0
        if pick["element"] == 3:
            pick["multiplier"] = 1
    assert snapshot_mod.points_from_picks(rewritten, record["elements"]) == 43


def test_no_squad_means_nothing_to_total():
    assert snapshot_mod.points_from_picks(None, {}) is None
    assert snapshot_mod.points_from_picks({"picks": []}, {}) is None


def test_a_consistent_record_has_no_inconsistencies(record):
    assert snapshot_mod.inconsistencies(record, MANAGERS) == []


def test_a_lagging_history_row_is_named(record):
    stale = json.loads(json.dumps(record))
    stale["history"]["1652821"]["points"] = 36
    assert snapshot_mod.inconsistencies(stale, MANAGERS) == [
        {"manager": "noel", "history": 36, "squad": 43}
    ]


def test_a_frozen_snapshot_that_disagrees_with_itself_refuses_to_publish(record):
    """The book must never settle on a history row the squad contradicts."""
    stale = json.loads(json.dumps(record))
    stale["history"]["1652821"]["points"] = 36
    with pytest.raises(LedgerError) as caught:
        scores_from_snapshot(stale, MANAGERS)
    assert "noel" in str(caught.value)
    assert "36" in str(caught.value) and "43" in str(caught.value)


def test_a_provisional_reading_scores_the_squad_not_the_history_row(record):
    stale = json.loads(json.dumps(record))
    stale["history"]["1652821"]["points"] = 36
    scores = scores_from_snapshot(stale, MANAGERS, provisional=True)
    assert scores["noel"].points == 43
    assert scores["noel"].stats == TiebreakStats(goals=2, assists=2, conceded=2, cards=1)


class _Fetcher:
    """The two endpoints a round reading needs, answered from the fixtures above."""

    def __init__(self, raw_dir, *, offline=False, history_points=None):
        self.raw_dir = raw_dir
        self.offline = offline
        self.snapshot_hits = 0
        self.requests = []
        self.history_points = history_points

    def entry_picks(self, entry_id, gw, *, final=True):
        self.requests.append(("picks", entry_id, final))
        return PICKS.get(entry_id)

    def event_live(self, gw, *, final=True):
        self.requests.append(("live", gw, final))
        return LIVE


EVENTS = [{"gw": 1, "deadline": "2026-08-21T17:30:00Z", "month": "AUG"}]


def _histories(noel_points: int) -> dict:
    histories = {int(k): v for k, v in json.loads(json.dumps(HISTORIES)).items()}
    histories[1652821]["current"][0]["points"] = noel_points
    return histories


def test_a_round_fpl_has_not_closed_is_held_provisional(tmp_path):
    """Every fixture finished, history a day behind: nothing frozen, nothing
    booked, and the page shows the squads' points while it waits."""
    from superf.fplcal import parse_utc

    fetcher = _Fetcher(tmp_path)
    gameweeks, states, _ = build_gameweeks(
        MANAGERS, EVENTS, {1: FIXTURES}, _histories(36), fetcher,
        parse_utc("2026-08-24T06:00:00Z"),
    )
    assert states[1] == "provisional"
    assert gameweeks[1].is_final is False
    assert not snapshot_mod.exists(1, root=tmp_path), "a lagging round must not be frozen"
    assert gameweeks[1].scores["noel"].points == 43
    assert all(final is False for kind, _, final in fetcher.requests), \
        "an unfrozen reading must never land in the HTTP cache"
    observed = snapshot_mod.load_provisional(1, root=tmp_path)
    assert observed["scores"]["noel"] == {"points": 43, "hits": 0}


def test_a_round_that_agrees_with_itself_is_frozen(tmp_path):
    from superf.fplcal import parse_utc

    fetcher = _Fetcher(tmp_path)
    gameweeks, states, _ = build_gameweeks(
        MANAGERS, EVENTS, {1: FIXTURES}, _histories(43), fetcher,
        parse_utc("2026-08-24T06:00:00Z"),
    )
    assert states[1] == "final"
    assert snapshot_mod.exists(1, root=tmp_path)
    assert gameweeks[1].scores["noel"].points == 43


def test_offline_a_round_with_no_snapshot_is_held_provisional_too(tmp_path):
    """The online build held it, so the offline rebuild must land on the same
    state — and it cannot read the API to check."""
    from superf.fplcal import parse_utc

    fetcher = _Fetcher(tmp_path, offline=True)
    _, states, _ = build_gameweeks(
        MANAGERS, EVENTS, {1: FIXTURES}, {}, fetcher, parse_utc("2026-08-24T06:00:00Z"),
    )
    assert states[1] == "provisional"
    assert fetcher.requests == []


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


SCORES = {"jack": {"points": 70, "hits": 0}, "sam": {"points": 66, "hits": 4}}


def test_provisional_scores_ride_along_with_the_leader(tmp_path):
    snapshot_mod.record_provisional_leader(
        3, "jack", "2026-09-06T20:00:00Z", root=tmp_path, scores=SCORES
    )
    record = snapshot_mod.load_provisional(3, tmp_path)
    assert record["leader"] == "jack"
    assert record["scores"] == SCORES


def test_scores_refresh_while_the_leader_stays_write_once(tmp_path):
    """The two halves of this record answer different questions.

    §11.4 asks who led when the round FIRST went provisional, so the leader and
    its timestamp are frozen. The standing on the page should track whatever
    FPL last said, so the scores are replaced every run — they are an
    observation of a moving state, not a record of a settled one."""
    snapshot_mod.record_provisional_leader(
        3, "jack", "2026-09-06T20:00:00Z", root=tmp_path, scores=SCORES
    )
    later = {"jack": {"points": 72, "hits": 0}, "sam": {"points": 80, "hits": 4}}
    snapshot_mod.record_provisional_leader(
        3, "sam", "2026-09-06T23:00:00Z", root=tmp_path, scores=later
    )
    record = snapshot_mod.load_provisional(3, tmp_path)
    assert record["leader"] == "jack"                       # write-once survives
    assert record["observed_at"] == "2026-09-06T20:00:00Z"  # ditto
    assert record["scores"] == later                        # but the standing moves
    assert record["scores_at"] == "2026-09-06T23:00:00Z"


def test_a_record_written_before_scores_existed_gains_them(tmp_path):
    """A file written before scores were part of the shape is completed, not
    left half-empty, and still keeps its original leader."""
    snapshot_mod.record_provisional_leader(3, "jack", "2026-09-06T20:00:00Z", root=tmp_path)
    assert "scores" not in snapshot_mod.load_provisional(3, tmp_path)
    snapshot_mod.record_provisional_leader(
        3, "sam", "2026-09-06T22:00:00Z", root=tmp_path, scores=SCORES
    )
    record = snapshot_mod.load_provisional(3, tmp_path)
    assert record["leader"] == "jack"
    assert record["scores"] == SCORES


def test_no_provisional_record_reads_as_absent(tmp_path):
    assert snapshot_mod.load_provisional(9, tmp_path) is None
