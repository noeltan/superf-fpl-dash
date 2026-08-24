"""Freezing the projection's inputs, and marking it against what happened.

The point of these is that a model change can be shown to help or hurt. Two
properties carry that: a frozen record must replay to *exactly* the projection
that was published (or the history is fiction), and the scoring must be right
about orderings it is handed.
"""

from __future__ import annotations

import json

import pytest

from superf import snapshot as snapshot_mod
from superf.projection import fixtures_by_team, project_manager
from superf.scoring import aggregate, pairwise, ranks, score_ranking, spearman

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import backtest  # noqa: E402


# --- the measures ------------------------------------------------------------

ORDER = ["a", "b", "c", "d"]


def test_a_perfect_ordering_scores_one():
    assert spearman(ORDER, ORDER) == 1.0
    assert pairwise(ORDER, ORDER) == (6, 6)


def test_exactly_backwards_scores_minus_one():
    assert spearman(list(reversed(ORDER)), ORDER) == -1.0
    assert pairwise(list(reversed(ORDER)), ORDER) == (0, 6)


def test_one_swap_costs_what_it_should():
    swapped = ["b", "a", "c", "d"]
    assert spearman(swapped, ORDER) == pytest.approx(0.8)
    assert pairwise(swapped, ORDER) == (5, 6)


def test_a_correlation_over_one_manager_is_not_a_number():
    """n < 2 divides by zero. Returning None beats returning nonsense."""
    assert spearman(["a"], ["a"]) is None
    assert spearman([], []) is None


def test_managers_missing_from_one_side_are_re_ranked_not_shifted():
    """A manager who did not play must not drag everyone below them down."""
    assert spearman(["a", "gone", "b", "c"], ["a", "b", "c"]) == 1.0


def test_the_three_section_twelve_measures():
    perfect = score_ranking(["a", "b", "c"], ["a", "b", "c"])
    assert perfect["exact"] and perfect["podium"] and perfect["pair"]

    # Right winner, wrong second: exact and podium, but not the pair.
    near = score_ranking(["a", "c", "b"], ["a", "b", "c"])
    assert near["exact"] and near["podium"] and not near["pair"]

    # Called second first: podium and pair, but not exact.
    swapped = score_ranking(["b", "a", "c"], ["a", "b", "c"])
    assert not swapped["exact"] and swapped["podium"] and swapped["pair"]

    missed = score_ranking(["c", "a", "b"], ["a", "b", "c"])
    assert not missed["exact"] and not missed["podium"] and not missed["pair"]


def test_aggregate_skips_gameweeks_that_produced_no_number():
    scores = [
        score_ranking(["a", "b", "c"], ["a", "b", "c"]),
        score_ranking(["a"], ["a"]),          # ungradeable
        score_ranking(["c", "b", "a"], ["a", "b", "c"]),
    ]
    total = aggregate(scores)
    assert total["gameweeks"] == 2
    assert total["spearman"] == pytest.approx(0.0)   # +1 and -1
    assert total["pairs"] == 6 and total["pairs_right"] == 3
    assert total["exact"] == 1


def test_nothing_to_aggregate_says_so():
    assert aggregate([]) == {"gameweeks": 0}
    assert aggregate([score_ranking(["a"], ["a"])]) == {"gameweeks": 0}


def test_ranks_are_one_based():
    assert ranks(["x", "y"]) == {"x": 1, "y": 2}


# --- freezing ----------------------------------------------------------------

ELEMENTS = {
    1: {"id": 1, "web_name": "Keeper", "team": 1, "element_type": 1, "status": "a",
        "chance_of_playing_next_round": None, "form": "3.0", "points_per_game": "4.0",
        "ep_next": "3.5", "expected_goals_per_90": 0.0, "expected_assists_per_90": 0.0,
        "expected_goals_conceded_per_90": 1.0, "saves_per_90": 3.0, "minutes": 900,
        "starts": 10, "now_cost": 50, "second_name": "not frozen"},
    2: {"id": 2, "web_name": "Star", "team": 2, "element_type": 4, "status": "a",
        "chance_of_playing_next_round": None, "form": "6.0", "points_per_game": "7.0",
        "ep_next": "6.5", "expected_goals_per_90": 0.9, "expected_assists_per_90": 0.1,
        "expected_goals_conceded_per_90": 0.0, "saves_per_90": 0.0, "minutes": 900,
        "starts": 10, "now_cost": 140, "second_name": "not frozen"},
    3: {"id": 3, "web_name": "Spare", "team": 3, "element_type": 3, "status": "a",
        "chance_of_playing_next_round": None, "form": "2.0", "points_per_game": "3.0",
        "ep_next": "2.5", "expected_goals_per_90": 0.2, "expected_assists_per_90": 0.3,
        "expected_goals_conceded_per_90": 1.2, "saves_per_90": 0.0, "minutes": 450,
        "starts": 5, "now_cost": 70, "second_name": "not frozen"},
}
TEAMS = {1: {"short_name": "ARS", "name": "Arsenal"},
         2: {"short_name": "MCI", "name": "Man City"},
         3: {"short_name": "CHE", "name": "Chelsea"}}
FIXTURES = [{"id": 9, "event": 2, "team_h": 1, "team_a": 2, "team_h_difficulty": 2,
             "team_a_difficulty": 4, "kickoff_time": "2026-08-28T18:30:00Z",
             "started": False, "finished": False, "pulse_id": "not frozen"}]


def squad(*elements, hits=0, chip=None):
    return {
        "active_chip": chip,
        "entry_history": {"event_transfers_cost": hits, "bank": "not frozen"},
        "picks": [{"element": e, "multiplier": m, "is_captain": False}
                  for e, m in elements],
    }


PICKS = {
    "noel": squad((1, 1), (2, 2)),
    "jack": squad((1, 1), (3, 1), hits=4),
}


def freeze(tmp_path, mode="pre_kickoff", scored=None):
    record = snapshot_mod.build_projection_inputs(
        gw=2, captured_at="2026-08-28T17:35:00Z", mode=mode,
        deadline="2026-08-28T17:30:00Z", elements=ELEMENTS, teams=TEAMS,
        fixtures=FIXTURES, picks=PICKS, scored_so_far=scored,
    )
    snapshot_mod.write_projection_inputs(record, root=tmp_path)
    return record


def test_the_frozen_record_replays_to_the_same_projection(tmp_path):
    """The property the whole exercise rests on. If a replay drifts from what
    was published, every number this tool ever prints is fiction."""
    freeze(tmp_path)
    live = {
        manager: project_manager(
            manager, payload, ELEMENTS, fixtures_by_team(FIXTURES), TEAMS
        ).to_contract()
        for manager, payload in PICKS.items()
    }

    record = snapshot_mod.load_projection_inputs(2, root=tmp_path)
    elements, teams, fixtures, picks, scored = snapshot_mod.replay_projection_inputs(record)
    replayed = {
        manager: project_manager(
            manager, payload, elements, fixtures_by_team(fixtures), teams, scored
        ).to_contract()
        for manager, payload in picks.items()
    }
    assert replayed == live


def test_a_mid_round_record_replays_with_its_banked_points(tmp_path):
    scored = {1: 6.0, 2: 9.0}
    freeze(tmp_path, mode="mid_round", scored=scored)
    record = snapshot_mod.load_projection_inputs(2, "mid_round", root=tmp_path)
    elements, teams, fixtures, picks, replayed_scored = snapshot_mod.replay_projection_inputs(record)
    assert replayed_scored == scored
    assert project_manager(
        "noel", picks["noel"], elements, fixtures_by_team(fixtures), teams, replayed_scored
    ).banked == pytest.approx(6.0 + 9.0 * 2)


def test_only_owned_players_and_only_the_fields_the_model_reads(tmp_path):
    record = freeze(tmp_path)
    assert set(record["elements"]) == {"1", "2", "3"}
    assert "second_name" not in record["elements"]["1"]
    assert "pulse_id" not in record["fixtures"][0]
    assert "bank" not in record["picks"]["noel"]["entry_history"]
    # Frozen for a prior that does not exist yet — that is the point of writing
    # it now rather than wishing for it later.
    assert record["elements"]["2"]["now_cost"] == 140
    assert record["elements"]["2"]["points_per_game"] == "7.0"


def test_a_pre_kickoff_record_is_written_once(tmp_path):
    freeze(tmp_path)
    path = snapshot_mod.projection_path(2, root=tmp_path)
    first = path.read_text()
    snapshot_mod.write_projection_inputs(
        dict(snapshot_mod.load_projection_inputs(2, root=tmp_path), deadline="TAMPERED"),
        root=tmp_path,
    )
    assert path.read_text() == first


def test_a_mid_round_record_is_refreshed_because_the_state_moved(tmp_path):
    freeze(tmp_path, mode="mid_round")
    path = snapshot_mod.projection_path(2, "mid_round", root=tmp_path)
    snapshot_mod.write_projection_inputs(
        dict(snapshot_mod.load_projection_inputs(2, "mid_round", root=tmp_path),
             captured_at="later"),
        root=tmp_path,
    )
    assert json.loads(path.read_text())["captured_at"] == "later"


# --- the harness -------------------------------------------------------------

def settled(points: dict[str, int]) -> dict:
    winner = max(points, key=lambda m: points[m])
    return {"gameweeks": [{
        "gw": 2, "winners": [winner],
        "scores": {m: {"points": p, "active": True} for m, p in points.items()},
    }]}


def test_the_harness_scores_a_gameweek_it_can_replay(tmp_path):
    freeze(tmp_path)
    # noel captains the Star; he should out-project jack, who also took a hit.
    report = backtest.run(tmp_path, settled({"noel": 70, "jack": 40}))
    assert [g["gw"] for g in report["gameweeks"]] == [2]
    assert report["gameweeks"][0]["ours"]["exact"] is True
    assert report["total"]["ours"]["gameweeks"] == 1


def test_the_harness_scores_the_ep_next_control_alongside(tmp_path):
    freeze(tmp_path)
    report = backtest.run(tmp_path, settled({"noel": 70, "jack": 40}))
    assert "ep_next" in report["gameweeks"][0]
    assert report["total"]["ep_next"]["gameweeks"] == 1


def test_an_unsettled_gameweek_is_skipped_not_guessed(tmp_path):
    freeze(tmp_path)
    report = backtest.run(tmp_path, {"gameweeks": []})
    assert report["gameweeks"] == []
    assert report["skipped"] == [{"gw": 2, "why": "not settled yet"}]


def test_a_settled_gameweek_with_no_frozen_inputs_is_named(tmp_path):
    report = backtest.run(tmp_path, settled({"noel": 70, "jack": 40}))
    assert report["gameweeks"] == []
    assert {"gw": 2, "why": "no frozen inputs"} in report["skipped"]


def test_mid_round_records_are_not_scored_as_if_they_were_calls(tmp_path):
    """They predict the rest of a gameweek — a different question."""
    freeze(tmp_path, mode="mid_round", scored={1: 6.0})
    report = backtest.run(tmp_path, settled({"noel": 70, "jack": 40}))
    assert report["gameweeks"] == []


def test_the_empty_report_reads_as_empty_not_as_a_perfect_score(tmp_path):
    rendered = backtest.render(backtest.run(tmp_path, {"gameweeks": []}))
    assert "Nothing to score yet" in rendered
    assert "1.000" not in rendered
