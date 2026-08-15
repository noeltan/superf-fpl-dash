"""Spec §3.1, §3.2b, §3.7, §3.8.2 — the shared distribution routine."""

from __future__ import annotations

import pytest

from superf.config import (
    MONTHLY_SPLIT,
    SEASON_SPLIT,
    WEEKLY_SPLIT,
    season_third_share_ok,
)
from superf.money import (
    LedgerError,
    advertised_net,
    distribute,
    rm_to_sen,
    round_half_up,
    sen_to_rm,
    share_amounts,
)

from fractions import Fraction


def order(managers):
    return {m: i for i, m in enumerate(managers)}


# --- arithmetic --------------------------------------------------------------

def test_rm_to_sen_is_exact_for_awkward_decimals():
    assert rm_to_sen(94.50) == 9450
    assert rm_to_sen("0.1") == 10
    assert sum(rm_to_sen("0.1") for _ in range(10)) == rm_to_sen(1)


def test_round_half_up_goes_up_at_the_half():
    assert round_half_up(Fraction(1, 2)) == 1
    assert round_half_up(Fraction(3, 2)) == 2
    assert round_half_up(Fraction(24, 10)) == 2
    assert round_half_up(Fraction(26, 10)) == 3


def test_sen_to_rm_keeps_whole_ringgit_whole():
    assert sen_to_rm(3500) == 35
    assert isinstance(sen_to_rm(3500), int)
    assert sen_to_rm(1550) == 15.5


def test_last_share_absorbs_the_residue():
    """§3.8.2 — rounding each share independently leaves the pot short."""
    amounts = share_amounts(10_000, [0.60, 0.25, 0.15])
    assert sum(amounts) == 10_000
    assert amounts == [6000, 2500, 1500]

    # A pot that does not divide cleanly: the headline prizes stay round.
    awkward = share_amounts(3333, [0.60, 0.25, 0.15])
    assert sum(awkward) == 3333
    assert awkward[0] == 2000  # 0.60 x 3333 = 1999.8 -> 2000
    assert awkward[-1] == 3333 - awkward[0] - awkward[1]


# --- the three pots at N=8 (§3.1) -------------------------------------------

def test_weekly_net_first_is_five_n_minus_five():
    for n in range(2, 13):
        (net,) = advertised_net(rm_to_sen(5), n, WEEKLY_SPLIT)
        assert net == rm_to_sen(5 * n - 5)


def test_monthly_nets_match_the_section_3_7_table():
    """Every month in §3.7's table, at N=8."""
    table = {2: (46, 14), 3: (69, 21), 4: (92, 28), 5: (115, 35), 6: (138, 42)}
    for gameweeks, (first, second) in table.items():
        stake = rm_to_sen(5 * gameweeks)
        assert advertised_net(stake, 8, MONTHLY_SPLIT) == [rm_to_sen(first), rm_to_sen(second)]


def test_season_nets_match_the_section_3_2b_table():
    table = {
        8: (380, 100, 20),
        9: (440, 125, 35),
        10: (500, 150, 50),
        12: (620, 200, 80),
    }
    for n, expected in table.items():
        nets = advertised_net(rm_to_sen(100), n, SEASON_SPLIT)
        assert nets == [rm_to_sen(v) for v in expected], f"N={n}"


@pytest.mark.parametrize("n", range(8, 13))
def test_every_pot_is_zero_sum_for_n_eight_through_twelve(n):
    managers = [f"m{i}" for i in range(n)]
    ranking = [[m] for m in managers]
    for stake_rm, split in (
        (5, WEEKLY_SPLIT),
        (30, MONTHLY_SPLIT),
        (100, SEASON_SPLIT),
    ):
        stakes = {m: rm_to_sen(stake_rm) for m in managers}
        ledger = distribute(stakes, split, ranking, order(managers))
        assert sum(ledger.values()) == 0


# --- ties (§3.5 level 5) -----------------------------------------------------

def test_two_way_tie_on_the_weekly_pot_pays_fifteen_each():
    """§3.5: 'expect fractions (+RM15 each at N=8 for two winners)'."""
    managers = [f"m{i}" for i in range(8)]
    stakes = {m: rm_to_sen(5) for m in managers}
    ranking = [["m0", "m1"]] + [[m] for m in managers[2:]]
    ledger = distribute(stakes, WEEKLY_SPLIT, ranking, order(managers))
    assert ledger["m0"] == ledger["m1"] == rm_to_sen(15)
    assert all(ledger[m] == rm_to_sen(-5) for m in managers[2:])
    assert sum(ledger.values()) == 0


def test_three_way_tie_splits_the_pot_and_spare_sen_go_by_entry_id():
    """RM40 across three does not divide: 4000 sen -> 1334/1333/1333."""
    managers = [f"m{i}" for i in range(8)]
    stakes = {m: rm_to_sen(5) for m in managers}
    ranking = [["m2", "m0", "m1"]] + [[m] for m in managers[3:]]
    ledger = distribute(stakes, WEEKLY_SPLIT, ranking, order(managers))
    # Earliest entry_id takes the spare sen, deterministically.
    assert ledger["m0"] == 1334 - 500
    assert ledger["m1"] == 1333 - 500
    assert ledger["m2"] == 1333 - 500
    assert sum(ledger.values()) == 0


def test_tie_for_first_splits_the_places_those_managers_occupy():
    """Two tied for 1st share the 60% and 25%; the next manager takes 15%."""
    managers = [f"m{i}" for i in range(8)]
    stakes = {m: rm_to_sen(100) for m in managers}
    ranking = [["m0", "m1"], ["m2"]] + [[m] for m in managers[3:]]
    ledger = distribute(stakes, SEASON_SPLIT, ranking, order(managers))
    pot = rm_to_sen(800)
    assert ledger["m0"] == ledger["m1"] == (pot * 85 // 100) // 2 - rm_to_sen(100)
    assert ledger["m2"] == pot * 15 // 100 - rm_to_sen(100)
    assert sum(ledger.values()) == 0


def test_all_managers_tied_splits_the_whole_pot_evenly():
    managers = [f"m{i}" for i in range(8)]
    stakes = {m: rm_to_sen(5) for m in managers}
    ledger = distribute(stakes, WEEKLY_SPLIT, [managers], order(managers))
    assert all(value == 0 for value in ledger.values())
    assert sum(ledger.values()) == 0


# --- mixed stakes (§3.8.6) ---------------------------------------------------

def test_differing_stakes_still_balance():
    """A month bucket spanning a mid-season join: the joiner pays for fewer gameweeks."""
    stakes = {"a": 3000, "b": 3000, "c": 1000}
    ledger = distribute(stakes, MONTHLY_SPLIT, [["a"], ["b"], ["c"]], order(["a", "b", "c"]))
    pot = 7000
    assert ledger["a"] == round(pot * 0.70) - 3000
    assert ledger["b"] == pot - round(pot * 0.70) - 3000
    assert ledger["c"] == -1000
    assert sum(ledger.values()) == 0


# --- guards ------------------------------------------------------------------

def test_no_ranking_means_no_pot_and_no_charge():
    """§10 — a postponed round pays nothing and costs nothing."""
    stakes = {"a": 500, "b": 500}
    assert distribute(stakes, WEEKLY_SPLIT, [], order(["a", "b"])) == {"a": 0, "b": 0}


def test_ranking_a_manager_who_did_not_pay_is_an_error():
    with pytest.raises(LedgerError):
        distribute({"a": 500}, WEEKLY_SPLIT, [["ghost"]], {"a": 0, "ghost": 1})


def test_third_place_share_floor_breaks_below_seven_managers():
    """§3.2's constraint can only fail downward, not as the league grows."""
    assert not season_third_share_ok(6)
    assert all(season_third_share_ok(n) for n in range(7, 21))
