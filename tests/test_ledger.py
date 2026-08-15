"""Spec §3.8.4-§3.8.7 — settlement end to end, and the invariants that gate publishing."""

from __future__ import annotations

import pytest

from conftest import AUGUST, GW1_POINTS, GW2_POINTS, LEAGUE, full_season, make_gameweek
from superf.ledger import Gameweek, ManagerScore, monthly_ledger, settle, weekly_ledger
from superf.money import LedgerError, rm_to_sen
from superf.tiebreak import TiebreakStats


def order_key(managers=LEAGUE):
    return {m["id"]: m["entry_id"] for m in managers}


def rm(ledger):
    return {m: v / 100 for m, v in ledger.items()}


# --- §3.8.7 the worked example ----------------------------------------------

def test_worked_example_from_section_3_8_7(league):
    """GW1 and GW2, N=8, August. Soon Lee tops both; Jack is second on the month."""
    calendar = full_season({1: GW1_POINTS, 2: GW2_POINTS}, months={1: "AUG", 2: "AUG"})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)

    assert result.weekly[1]["soonlee"] == rm_to_sen(35)
    assert result.weekly[2]["soonlee"] == rm_to_sen(35)
    assert result.weekly[1]["jack"] == rm_to_sen(-5)

    august = result.monthly["AUG"]
    assert august["soonlee"] == rm_to_sen(46)
    assert august["jack"] == rm_to_sen(14)
    assert august["noel"] == rm_to_sen(-10)

    # Banked totals: Soon Lee +RM116, Jack +RM4, the other six -RM20 each.
    assert result.totals["soonlee"] == rm_to_sen(116)
    assert result.totals["jack"] == rm_to_sen(4)
    for manager in ("noel", "sam", "weihun", "boonsiang", "tianpin", "chris"):
        assert result.totals[manager] == rm_to_sen(-20), manager
    assert sum(result.totals.values()) == 0


def test_season_is_projected_not_banked_before_gw38(league):
    calendar = full_season({1: GW1_POINTS, 2: GW2_POINTS}, months={1: "AUG", 2: "AUG"})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)

    assert result.season == {}
    assert result.season_is_final is False
    assert result.projected_season["soonlee"] == rm_to_sen(380)
    assert sum(result.projected_season.values()) == 0
    # ...and it stays out of the banked total.
    assert result.totals["soonlee"] == rm_to_sen(116)


def test_season_banks_only_when_gw38_is_final(league):
    played = {gw: GW1_POINTS for gw in range(1, 39)}
    months = {gw: "AUG" for gw in range(1, 39)}
    calendar = full_season(played, months=months)
    buckets = [{"month": "AUG", "gameweeks": list(range(1, 39))}]
    result = settle(league, calendar, buckets, expected_gameweeks=38)

    assert result.season_is_final is True
    assert result.projected_season == {}
    assert result.season["soonlee"] == rm_to_sen(380)
    assert sum(result.totals.values()) == 0


# --- §10 non-participation ---------------------------------------------------
# Not a missed deadline: FPL rolls the previous squad over and it scores as
# usual. This is the manager who never entered a team at all.

def test_never_setting_a_team_scores_zero_and_still_pays(league):
    points = dict(GW1_POINTS, chris=0)
    calendar = full_season({1: points}, months={1: "AUG"})
    calendar[1].scores["chris"].did_not_set = True

    result = settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)
    assert result.weekly[1]["chris"] == rm_to_sen(-5)
    assert calendar[1].scores["chris"].active is True
    assert sum(result.weekly[1].values()) == 0


def test_a_manager_on_zero_can_still_be_ranked_last_without_breaking_the_pot(league):
    points = {m["id"]: 0 for m in league}
    calendar = full_season({1: points}, months={1: "AUG"})
    result = settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)
    # Everyone level on 0 with no tiebreak stats: the pot splits eight ways.
    assert all(value == 0 for value in result.weekly[1].values())


# --- §10 postponed round -----------------------------------------------------

def test_postponed_round_pays_no_pot_and_charges_nothing(league):
    calendar = full_season({1: GW1_POINTS, 2: GW2_POINTS}, months={1: "AUG", 2: "AUG"},
                           notes={2: "postponed"})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)

    assert all(value == 0 for value in result.weekly[2].values())
    # August's monthly pot is built from GW1 alone, so the stake is RM5 not RM10.
    assert result.monthly["AUG"]["soonlee"] == rm_to_sen(0.70 * 40 - 5)
    assert sum(result.totals.values()) == 0


# --- §3.5 ties ---------------------------------------------------------------

def test_tie_broken_on_goals_awards_the_whole_pot(league):
    points = dict(GW1_POINTS, jack=72)  # level with soonlee on 72
    stats = {"soonlee": TiebreakStats(goals=4), "jack": TiebreakStats(goals=2)}
    calendar = full_season({1: points}, months={1: "AUG"}, stats={1: stats})
    result = settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)

    assert result.weekly[1]["soonlee"] == rm_to_sen(35)
    assert result.weekly[1]["jack"] == rm_to_sen(-5)
    assert result.rankings[1].tiebreak == {"level": 1, "text": "Won on goals (4 v 2)"}


def test_tie_surviving_all_four_levels_splits_the_pot(league):
    points = dict(GW1_POINTS, jack=72)
    identical = TiebreakStats(goals=3, assists=2, conceded=4, cards=1)
    stats = {"soonlee": identical, "jack": identical}
    calendar = full_season({1: points}, months={1: "AUG"}, stats={1: stats})
    result = settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)

    assert result.weekly[1]["soonlee"] == rm_to_sen(15)
    assert result.weekly[1]["jack"] == rm_to_sen(15)
    assert result.rankings[1].tiebreak is None  # nothing resolved it
    assert sorted(result.rankings[1].winners) == ["jack", "soonlee"]
    assert sum(result.weekly[1].values()) == 0


def test_monthly_tie_sums_the_metrics_across_the_bucket(league):
    """§3.5 — monthly ties use the same four metrics summed over the bucket."""
    gw1 = dict(GW1_POINTS, jack=70, soonlee=70)
    gw2 = dict(GW2_POINTS, jack=70, soonlee=70)
    stats1 = {"soonlee": TiebreakStats(goals=1), "jack": TiebreakStats(goals=2)}
    stats2 = {"soonlee": TiebreakStats(goals=3), "jack": TiebreakStats(goals=1)}
    calendar = full_season({1: gw1, 2: gw2}, months={1: "AUG", 2: "AUG"},
                           stats={1: stats1, 2: stats2})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)

    # Soon Lee 4 goals across August, Jack 3 -> Soon Lee takes the 70%.
    assert result.monthly["AUG"]["soonlee"] == rm_to_sen(46)
    assert result.monthly["AUG"]["jack"] == rm_to_sen(14)


# --- §3.8.6 mid-season joiner -----------------------------------------------

NINTH = {"id": "newguy", "entry_id": 9999999}


def test_ninth_manager_joining_at_gw5_rescales_from_that_gameweek_on():
    league = LEAGUE + [NINTH]
    played = {gw: dict(GW1_POINTS) for gw in range(1, 7)}
    for gw in range(5, 7):
        played[gw]["newguy"] = 50
    months = {1: "AUG", 2: "AUG", 3: "SEP", 4: "SEP", 5: "SEP", 6: "OCT"}
    calendar = full_season(played, managers=league, months=months)
    buckets = [
        {"month": "AUG", "gameweeks": [1, 2]},
        {"month": "SEP", "gameweeks": [3, 4, 5]},
    ]
    result = settle(league, calendar, buckets, expected_gameweeks=38)

    # GW1-4 settle at N=8 and are never recomputed.
    assert result.weekly[1]["soonlee"] == rm_to_sen(35)
    assert "newguy" not in result.weekly[1]
    # GW5 onward settle at N=9: the pot is RM45, so first nets +RM40.
    assert result.weekly[5]["soonlee"] == rm_to_sen(40)
    assert result.weekly[5]["newguy"] == rm_to_sen(-5)

    for gw, ledger in result.weekly.items():
        assert sum(ledger.values()) == 0, gw
    assert sum(result.totals.values()) == 0


def test_month_bucket_spanning_a_join_is_computed_gameweek_by_gameweek():
    """§3.8.6 — not with one blended N. The joiner pays only for what they played."""
    league = LEAGUE + [NINTH]
    played = {3: dict(GW1_POINTS), 4: dict(GW1_POINTS), 5: dict(GW1_POINTS)}
    played[5]["newguy"] = 50
    months = {3: "SEP", 4: "SEP", 5: "SEP"}
    calendar = full_season(played, managers=league, months=months)
    buckets = [{"month": "SEP", "gameweeks": [3, 4, 5]}]
    result = settle(league, calendar, buckets, expected_gameweeks=38)

    september = result.monthly["SEP"]
    # Pot = 5x8 + 5x8 + 5x9 = RM125. The joiner staked RM5, everyone else RM15.
    assert september["newguy"] == rm_to_sen(-5)
    assert september["noel"] == rm_to_sen(-15)
    # 70% of RM125 is RM87.50 exactly — a fraction, which is the whole reason
    # §3.8.1 insists on sen. First nets RM87.50 - RM15 = RM72.50.
    assert september["soonlee"] == rm_to_sen("72.50")
    assert sum(september.values()) == 0


def test_a_joiner_after_gw3_does_not_disturb_anything_already_settled():
    """The realistic case: nobody joins late, but if they do, history is frozen."""
    league_eight = LEAGUE
    league_nine = LEAGUE + [NINTH]
    played_before = {1: dict(GW1_POINTS), 2: dict(GW2_POINTS)}
    months = {1: "AUG", 2: "AUG"}

    before = settle(league_eight, full_season(played_before, months=months), AUGUST, 38)
    after = settle(
        league_nine,
        full_season(played_before, managers=league_nine, months=months),
        AUGUST,
        38,
    )
    for manager in [m["id"] for m in league_eight]:
        assert before.totals[manager] == after.totals[manager], manager
    assert after.totals["newguy"] == 0
    assert sum(after.totals.values()) == 0


# --- §3.8.5 invariants -------------------------------------------------------

def test_a_calendar_that_is_not_38_gameweeks_fails_the_build(league):
    """§3.9 — the old sheet stopped at GW37 and a monthly pot went to the wrong person."""
    calendar = full_season({1: GW1_POINTS}, months={1: "AUG"}, total_gameweeks=37)
    with pytest.raises(LedgerError, match="expected 38 gameweeks"):
        settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)


def test_every_active_manager_appears_in_every_gameweek_they_played(league):
    calendar = full_season({1: GW1_POINTS, 2: GW2_POINTS}, months={1: "AUG", 2: "AUG"})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)
    for gw in (1, 2):
        assert set(result.weekly[gw]) == {m["id"] for m in league}


def test_scores_referencing_an_unknown_manager_fail_the_build(league):
    """Roster and scores drifting apart must fail loudly, not settle a stranger."""
    calendar = full_season({1: GW1_POINTS}, months={1: "AUG"})
    calendar[1].scores["ghost"] = ManagerScore(points=999, active=True)
    with pytest.raises(LedgerError, match="missing from the roster"):
        settle(league, calendar, [{"month": "AUG", "gameweeks": [1]}], expected_gameweeks=38)


def test_incomplete_month_does_not_settle(league):
    """§3.8.3 — a monthly pot waits for the last gameweek in its bucket."""
    calendar = full_season({1: GW1_POINTS}, months={1: "AUG", 2: "AUG"})
    result = settle(league, calendar, AUGUST, expected_gameweeks=38)
    assert "AUG" not in result.monthly
    assert result.weekly[1]  # the weekly pot still settled
