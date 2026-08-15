"""Spec §3.5 — the four-level ladder, and the XI it is computed over."""

from __future__ import annotations

from superf.tiebreak import (
    TiebreakStats,
    first_differing_level,
    rank,
    starting_xi,
    stats_for_xi,
)

ORDER = {"a": 1, "b": 2, "c": 3}


def test_the_xi_is_the_one_after_auto_substitutions():
    """§3.5 — ties can only be computed once the gameweek is Final."""
    picks = {
        "picks": [
            {"element": 1, "multiplier": 1},
            {"element": 2, "multiplier": 2},
            {"element": 3, "multiplier": 0},  # bench
            {"element": 4, "multiplier": 0},  # bench
        ],
        "automatic_subs": [{"element_in": 3, "element_out": 1}],
    }
    assert starting_xi(picks) == [3, 2]


def test_bench_players_never_count():
    picks = {"picks": [{"element": 9, "multiplier": 0}], "automatic_subs": []}
    assert starting_xi(picks) == []


def test_the_armband_does_not_double_goals_or_assists():
    """A captained hat-trick counts as 3, not 6."""
    stats = stats_for_xi([7], {7: {"goals_scored": 3, "assists": 1}})
    assert stats.goals == 3
    assert stats.assists == 1


def test_goals_conceded_double_counts_by_design():
    """Three players from one club means that club's conceded goals count thrice."""
    element_stats = {1: {"goals_conceded": 2}, 2: {"goals_conceded": 2}, 3: {"goals_conceded": 2}}
    assert stats_for_xi([1, 2, 3], element_stats).conceded == 6


def test_cards_are_yellows_plus_reds():
    assert stats_for_xi([1], {1: {"yellow_cards": 2, "red_cards": 1}}).cards == 3


def test_ladder_levels_resolve_in_order():
    base = dict(goals=2, assists=2, conceded=2, cards=2)
    assert first_differing_level(TiebreakStats(**base), TiebreakStats(**base)) is None
    assert first_differing_level(TiebreakStats(**{**base, "goals": 3}), TiebreakStats(**base)) == 1
    assert first_differing_level(TiebreakStats(**{**base, "assists": 3}), TiebreakStats(**base)) == 2
    assert first_differing_level(TiebreakStats(**{**base, "conceded": 3}), TiebreakStats(**base)) == 3
    assert first_differing_level(TiebreakStats(**{**base, "cards": 3}), TiebreakStats(**base)) == 4


def test_level_one_most_goals_wins():
    ranking = rank(
        {"a": 60, "b": 60},
        {"a": TiebreakStats(goals=1), "b": TiebreakStats(goals=3)},
        ORDER,
    )
    assert ranking.winners == ["b"]
    assert ranking.tiebreak == {"level": 1, "text": "Won on goals (3 v 1)"}


def test_level_three_fewest_conceded_wins():
    ranking = rank(
        {"a": 60, "b": 60},
        {"a": TiebreakStats(goals=1, assists=1, conceded=0),
         "b": TiebreakStats(goals=1, assists=1, conceded=4)},
        ORDER,
    )
    assert ranking.winners == ["a"]
    assert ranking.tiebreak == {"level": 3, "text": "Won on goals conceded (0 v 4)"}


def test_level_four_fewest_cards_wins():
    ranking = rank(
        {"a": 60, "b": 60},
        {"a": TiebreakStats(cards=3), "b": TiebreakStats(cards=1)},
        ORDER,
    )
    assert ranking.winners == ["b"]
    assert ranking.tiebreak == {"level": 4, "text": "Won on cards (1 v 3)"}


def test_all_four_level_means_a_split_and_no_explanation():
    identical = TiebreakStats(goals=1, assists=1, conceded=1, cards=1)
    ranking = rank({"a": 60, "b": 60}, {"a": identical, "b": identical}, ORDER)
    assert sorted(ranking.winners) == ["a", "b"]
    assert ranking.tiebreak is None


def test_no_tiebreak_note_when_the_winner_was_clear():
    ranking = rank({"a": 70, "b": 60}, {}, ORDER)
    assert ranking.winners == ["a"]
    assert ranking.tiebreak is None


def test_places_account_for_tied_groups():
    identical = TiebreakStats()
    ranking = rank({"a": 70, "b": 70, "c": 60}, {"a": identical, "b": identical}, ORDER)
    assert ranking.place_of("a") == 1
    assert ranking.place_of("b") == 1
    assert ranking.place_of("c") == 3  # two managers occupy first


def test_stats_add_across_a_bucket():
    """Monthly and season ties sum the same four metrics."""
    total = TiebreakStats(1, 2, 3, 4) + TiebreakStats(10, 20, 30, 40)
    assert total == TiebreakStats(11, 22, 33, 44)
