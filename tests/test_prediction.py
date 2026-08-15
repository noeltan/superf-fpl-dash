"""Spec §12 — projection arithmetic, the number guard, and the scorecard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from predict import actual_order, roll_record, score_prediction, window_for
from superf.claude_call import allowed_numbers, find_violations, validate
from superf.projection import (
    clean_sheet_probability,
    fixture_adjustment,
    fixtures_by_team,
    minutes_factor,
    p_plays,
    project_manager,
    project_player,
    swing_candidates,
)
from superf.fplcal import parse_utc


def element(**kwargs):
    base = {
        "id": 1, "web_name": "Player", "team": 1, "element_type": 3, "status": "a",
        "chance_of_playing_next_round": None, "form": "0.0", "points_per_game": "0.0",
        "ep_next": "0.0", "expected_goals_per_90": 0.0, "expected_assists_per_90": 0.0,
        "expected_goals_conceded_per_90": 0.0, "saves_per_90": 0.0,
        "minutes": 2700, "starts": 30,
    }
    base.update(kwargs)
    return base


# --- availability ------------------------------------------------------------

@pytest.mark.parametrize("status", ["i", "s", "u", "n"])
def test_unavailable_players_project_zero(status):
    assert p_plays(element(status=status)) == 0.0
    assert project_player(element(status=status), 2, True) == 0.0


def test_chance_of_playing_is_used_when_present():
    assert p_plays(element(status="d", chance_of_playing_next_round=25)) == 0.25


def test_a_doubt_without_a_percentage_is_a_coin_flip():
    assert p_plays(element(status="d")) == 0.5


def test_minutes_factor_reflects_the_record():
    assert minutes_factor(element(minutes=2700, starts=30)) == 1.0
    assert minutes_factor(element(minutes=1350, starts=30)) == 0.5
    # No record at all is a rotation risk, not a certainty.
    assert minutes_factor(element(minutes=0, starts=0)) == 0.6


# --- fixture and clean sheet -------------------------------------------------

def test_easier_fixtures_raise_expected_return():
    assert fixture_adjustment(2, True) > fixture_adjustment(3, True) > fixture_adjustment(5, True)


def test_home_advantage_beats_the_same_fixture_away():
    assert fixture_adjustment(3, True) > fixture_adjustment(3, False)


def test_clean_sheet_probability_falls_as_the_opponent_gets_harder():
    easy = clean_sheet_probability(0.8, 2, True)
    hard = clean_sheet_probability(0.8, 5, False)
    assert 0 < hard < easy < 1


def test_a_defender_with_a_good_fixture_outprojects_the_same_defender_away_at_a_top_side():
    defender = element(element_type=2, expected_goals_conceded_per_90=0.9)
    assert project_player(defender, 2, True) > project_player(defender, 5, False)


def test_a_striker_who_scores_outprojects_one_who_does_not():
    scorer = element(element_type=4, expected_goals_per_90=0.8)
    blank = element(element_type=4, expected_goals_per_90=0.0, ep_next="0.0")
    assert project_player(scorer, 3, True) > project_player(blank, 3, True)


def test_thin_records_fall_back_to_the_api_expectation():
    """A promoted club's signing has no per-90s; do not flatten them to appearance points."""
    newcomer = element(element_type=4, minutes=0, starts=0, ep_next="5.0")
    assert project_player(newcomer, 3, True) > 2.5


# --- manager totals ----------------------------------------------------------

ELEMENTS = {
    1: element(id=1, web_name="Keeper", element_type=1, team=1, saves_per_90=3.0,
               expected_goals_conceded_per_90=1.0),
    2: element(id=2, web_name="Star", element_type=4, team=2, expected_goals_per_90=0.9),
    3: element(id=3, web_name="Mid", element_type=3, team=2, expected_assists_per_90=0.4),
    4: element(id=4, web_name="Bench", element_type=3, team=3, expected_goals_per_90=0.5),
}
TEAMS = {1: {"short_name": "ARS"}, 2: {"short_name": "MCI"}, 3: {"short_name": "CHE"}}
BY_TEAM = {1: [(3, True)], 2: [(2, True)], 3: [(4, False)]}


def picks_payload(hits=0, chip=None):
    return {
        "active_chip": chip,
        "entry_history": {"event_transfers_cost": hits},
        "picks": [
            {"element": 1, "multiplier": 1},
            {"element": 2, "multiplier": 2},  # captain
            {"element": 3, "multiplier": 1},
            {"element": 4, "multiplier": 0},  # bench
        ],
    }


def test_captain_counts_twice_and_the_bench_not_at_all():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    solo = project_player(ELEMENTS[2], 2, True)
    assert projection.captain == "Star"
    assert projection.captain_xp == pytest.approx(solo)
    assert all(p.name != "Bench" for p in projection.players)


def test_transfer_hits_come_straight_off_the_total():
    clean = project_manager("noel", picks_payload(hits=0), ELEMENTS, BY_TEAM, TEAMS)
    hit = project_manager("noel", picks_payload(hits=4), ELEMENTS, BY_TEAM, TEAMS)
    assert clean.xp - hit.xp == pytest.approx(4.0)
    assert hit.hits == 4


def test_concentration_names_the_club_with_the_most_players():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    assert projection.concentration == {"club": "MCI", "players": 2}


def test_a_double_gameweek_counts_both_fixtures():
    single = project_manager("noel", picks_payload(), ELEMENTS, {2: [(2, True)]}, TEAMS)
    double = project_manager(
        "noel", picks_payload(), ELEMENTS, {2: [(2, True), (2, False)]}, TEAMS
    )
    assert double.xp > single.xp


def test_fixtures_by_team_maps_both_sides_with_their_own_difficulty():
    mapping = fixtures_by_team([
        {"team_h": 5, "team_a": 9, "team_h_difficulty": 2, "team_a_difficulty": 4}
    ])
    assert mapping[5] == [(2, True)]
    assert mapping[9] == [(4, False)]


def test_swing_shortlist_only_contains_shared_players():
    a = project_manager("a", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    b = project_manager("b", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    shortlist = swing_candidates([a, b])
    assert shortlist
    for candidate in shortlist:
        assert len(candidate["owned_by"]) > 1
    assert "Star" in {c["name"] for c in shortlist}


def test_contract_shape_matches_section_12_4():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS).to_contract()
    assert set(projection) == {"manager", "xp", "captain", "captain_xp", "hits", "concentration"}
    assert set(projection["concentration"]) == {"club", "players"}


# --- the number guard --------------------------------------------------------

PROJECTIONS = [
    {"manager": "noel", "xp": 58.4, "captain": "Haaland", "captain_xp": 11.2,
     "hits": 0, "concentration": {"club": "MCI", "players": 4}},
    {"manager": "jack", "xp": 56.1, "captain": "Saka", "captain_xp": 10.4,
     "hits": 4, "concentration": {"club": "ARS", "players": 3}},
]
FIXTURES = [{"team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 5,
             "kickoff_time": "2026-09-05T14:00:00Z"}]
ALLOWED = allowed_numbers(PROJECTIONS, 3, FIXTURES, 8)


def test_projection_figures_are_quotable():
    assert not find_violations("Noel projects 58.4 with Haaland at 11.2.", ALLOWED)


def test_invented_figures_are_caught():
    assert find_violations("Noel should score about 71 points.", ALLOWED) == ["71"]


def test_a_percentage_the_projection_never_produced_is_caught():
    assert "83" in find_violations("He has an 83% chance of hauling.", ALLOWED)


def test_kickoff_times_are_quotable_in_both_zones():
    assert not find_violations("They kick off at 14:00 UTC, 22:00 our time.", ALLOWED)


def test_validate_rejects_an_unknown_manager():
    call = {
        "first": {"manager": "ghost", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["jack"], "why": "-"},
        "reasoning": "Short.",
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("unknown manager" in p for p in problems)


def test_validate_rejects_a_swing_player_off_the_shortlist():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Invented", "owned_by": ["jack"], "why": "-"},
        "reasoning": "Short.",
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("shortlist" in p for p in problems)


def test_validate_rejects_reasoning_over_the_word_limit():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["jack"], "why": "-"},
        "reasoning": "word " * 150,
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("limit is 120" in p for p in problems)


def test_validate_rejects_calling_the_same_manager_twice():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "noel", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["noel"], "why": "-"},
        "reasoning": "Short.",
    }
    assert any("same manager" in p for p in validate(call, ALLOWED, {"noel"}, {"Saka"}))


# --- §12.1 the window --------------------------------------------------------

DEADLINE = parse_utc("2026-08-21T17:30:00Z")


def test_window_closes_before_the_first_kickoff():
    opens, closes = window_for(DEADLINE, [{"kickoff_time": "2026-08-21T19:00:00Z"}])
    assert opens == DEADLINE + timedelta(minutes=3)
    assert closes == parse_utc("2026-08-21T18:55:00Z")


def test_gw1_gets_its_full_ninety_minute_window():
    """§12.1's worked case: deadline 17:30, first kickoff 19:00."""
    opens, closes = window_for(DEADLINE, [
        {"kickoff_time": "2026-08-22T14:00:00Z"},
        {"kickoff_time": "2026-08-21T19:00:00Z"},
    ])
    assert (closes - opens) > timedelta(minutes=75)


def test_a_tight_gameweek_gets_a_correspondingly_short_window():
    _, closes = window_for(DEADLINE, [{"kickoff_time": "2026-08-21T17:45:00Z"}])
    assert closes == parse_utc("2026-08-21T17:40:00Z")


def test_window_falls_back_to_the_hard_limit_without_kickoff_times():
    opens, closes = window_for(DEADLINE, [])
    assert closes == DEADLINE + timedelta(minutes=50)
    assert closes > opens


# --- §12.3 the scorecard -----------------------------------------------------

GAMEWEEK_ROW = {
    "gw": 2,
    "winners": ["soonlee"],
    "scores": {
        "soonlee": {"points": 69, "active": True},
        "noel": {"points": 68, "active": True},
        "jack": {"points": 66, "active": True},
        "chris": {"points": 0, "active": True},
    },
}


def test_actual_order_is_by_points_with_the_winner_pinned():
    assert actual_order(GAMEWEEK_ROW) == ["soonlee", "noel", "jack", "chris"]


def test_inactive_managers_are_left_out_of_the_order():
    row = {"gw": 2, "winners": ["soonlee"], "scores": dict(
        GAMEWEEK_ROW["scores"], newguy={"points": None, "active": False})}
    assert "newguy" not in actual_order(row)


def make_call(first, second):
    return {
        "gw": 2,
        "call": {"first": {"manager": first}, "second": {"manager": second}},
        "record": {"played": 1, "exact": 0, "podium": 0, "pair": 0},
    }


def test_exact_hit():
    result = score_prediction(make_call("soonlee", "jack"), GAMEWEEK_ROW)["result"]
    assert result["exact_hit"] and result["podium_hit"]
    assert not result["pair_hit"]  # jack finished third


def test_podium_hit_without_an_exact_hit():
    result = score_prediction(make_call("noel", "chris"), GAMEWEEK_ROW)["result"]
    assert not result["exact_hit"]
    assert result["podium_hit"]


def test_pair_hit_in_either_order():
    result = score_prediction(make_call("noel", "soonlee"), GAMEWEEK_ROW)["result"]
    assert result["pair_hit"]
    assert result["podium_hit"]
    assert not result["exact_hit"]


def test_a_complete_miss_is_recorded_as_one():
    result = score_prediction(make_call("chris", "jack"), GAMEWEEK_ROW)["result"]
    assert not any([result["exact_hit"], result["podium_hit"], result["pair_hit"]])


def test_mean_rank_error_matches_the_mock():
    """The mock's settled prediction reports 1.5 for exactly this shape."""
    result = score_prediction(make_call("jack", "soonlee"), GAMEWEEK_ROW)["result"]
    assert result["mean_rank_error"] == 1.5


def test_record_rolls_forward_only_on_hits():
    base = {"played": 4, "exact": 1, "podium": 2, "pair": 1}
    hit = roll_record(base, {"exact_hit": True, "podium_hit": True, "pair_hit": False})
    assert hit == {"played": 5, "exact": 2, "podium": 3, "pair": 1}
    miss = roll_record(base, {"exact_hit": False, "podium_hit": False, "pair_hit": False})
    assert miss == {"played": 5, "exact": 1, "podium": 2, "pair": 1}
