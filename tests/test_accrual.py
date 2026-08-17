"""Spec §3.9 — deferred settlement: the statement, corrections, and the sheet.

Nothing is paid until May, so this system is the book of record for money nobody
has paid yet. An error posted in November survives seven months and only
surfaces when someone is asked for RM400 — which is why every one of these is a
hard assertion rather than a display detail.
"""

from __future__ import annotations

import pytest

from conftest import AUGUST, GW1_POINTS, GW2_POINTS, full_season
from superf import corrections as corrections_mod
from superf.emit import build_payload, ordinal
from superf.ledger import settle
from superf.money import LedgerError, minimum_transfers
from test_schema import BUCKETS, EVENTS, MANAGERS, TEAMS

SETTLED_DATES = {1: "2026-08-24", 2: "2026-08-31"}


def make_payload(corrections=None, did_not_set=True):
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    if did_not_set:
        calendar[2].scores["chris"].did_not_set = True
    settlement = settle(MANAGERS, calendar, BUCKETS, expected_gameweeks=38)
    return build_payload(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=MANAGERS, teams=TEAMS, events=EVENTS, breaks=[],
        month_buckets=BUCKETS, fixtures_by_gw={}, pl_table=[],
        gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES, corrections=corrections,
    )


@pytest.fixture
def payload():
    return make_payload()


# --- §3.9.2 the per-manager statement ---------------------------------------

def test_the_statement_decomposes_the_balance_row_by_row(payload):
    """Every row explains its own amount, and the last balance is what the
    manager is asked to pay. Noel finishes 7th in GW1 and second in GW2, so the
    70/30 weekly pays him once — the row has to say why."""
    assert payload["ledger"]["noel"]["statement"] == [
        {"date": "2026-08-24", "type": "weekly", "gw": 1,
         "detail": "46 pts, 7th of 8", "amount": -1000, "balance": -1000},
        {"date": "2026-08-31", "type": "weekly", "gw": 2,
         "detail": "68 pts, 2nd of 8 — 30% share", "amount": 1400, "balance": 400},
        {"date": "2026-08-31", "type": "monthly", "month": "AUG",
         "detail": "114 pts, 5th in August", "amount": -1000, "balance": -600},
    ]


def test_the_winner_and_the_share_are_named_in_the_detail(payload):
    soonlee = payload["ledger"]["soonlee"]["statement"]
    assert soonlee[0]["detail"] == "72 pts, 1st of 8, top score — 70% share"
    assert soonlee[2]["detail"] == "141 pts, 1st in August — 70% share"
    assert payload["ledger"]["jack"]["statement"][2]["detail"] == (
        "134 pts, 2nd in August — 30% share"
    )


def test_a_missed_deadline_says_so_in_the_statement(payload):
    assert payload["ledger"]["chris"]["statement"][1]["detail"] == "never set team, 0 pts"


def test_every_statement_ends_on_the_accrued_balance(payload):
    for manager, entry in payload["ledger"].items():
        assert entry["statement"][-1]["balance"] == entry["accrued"], manager


def test_running_balance_is_the_cumulative_sum(payload):
    for entry in payload["ledger"].values():
        running = 0
        for row in entry["statement"]:
            running += row["amount"]
            assert row["balance"] == running


def test_statement_rows_are_oldest_first_with_weekly_before_monthly(payload):
    for entry in payload["ledger"].values():
        keys = [(r["date"], 0 if r["type"] == "weekly" else 1) for r in entry["statement"]]
        assert keys == sorted(keys)


def test_ordinals_read_correctly():
    assert [ordinal(n) for n in (1, 2, 3, 4, 8, 11, 12, 13, 21)] == [
        "1st", "2nd", "3rd", "4th", "8th", "11th", "12th", "13th", "21st"
    ]


# --- §3.9.3 the settlement sheet ---------------------------------------------

def test_the_settlement_sheet_clears_the_book_in_n_minus_one_payments(payload):
    """Soon Lee +RM138, Jack +RM18, Noel -RM6, five on -RM30. Eight managers
    could mean 56 transfers; this is seven, and the same seven every run.

    §3.9.5 prints RM120 moved over seven payments, computed against the RM5
    winner-takes-all weekly. Ours moves RM156 — same bound, bigger pots.
    """
    assert payload["settlement"]["settled"] is False
    assert payload["settlement"]["payments"] == [
        {"from": "boonsiang", "to": "soonlee", "amount": 3000},
        {"from": "chris", "to": "soonlee", "amount": 3000},
        {"from": "sam", "to": "soonlee", "amount": 3000},
        {"from": "tianpin", "to": "soonlee", "amount": 3000},
        {"from": "weihun", "to": "soonlee", "amount": 1800},
        {"from": "weihun", "to": "jack", "amount": 1200},
        {"from": "noel", "to": "jack", "amount": 600},
    ]
    assert len(payload["settlement"]["payments"]) <= len(payload["ledger"]) - 1
    assert sum(p["amount"] for p in payload["settlement"]["payments"]) == 15600


def test_settlement_never_exceeds_n_minus_one_payments():
    """The §3.9.3 bound, over awkward books rather than tidy ones."""
    books = [
        {"a": 700, "b": -100, "c": -200, "d": -400},
        {"a": 1, "b": 2, "c": 3, "d": -6},
        {"a": 5000, "b": 5000, "c": -2500, "d": -2500, "e": -2500, "f": -2500},
        {f"m{i}": (100 if i else -700) for i in range(8)},
    ]
    for book in books:
        payments = minimum_transfers(book)
        assert len(payments) <= len(book) - 1
        net = {m: 0 for m in book}
        for payment in payments:
            net[payment["from"]] -= payment["amount"]
            net[payment["to"]] += payment["amount"]
        assert net == book


def test_a_book_that_does_not_balance_cannot_be_settled():
    with pytest.raises(LedgerError, match="does not balance"):
        minimum_transfers({"a": 100, "b": -50})


def test_settlement_flips_to_final_once_gw38_is_final():
    played = {gw: GW1_POINTS for gw in range(1, 39)}
    calendar = full_season(played, managers=MANAGERS, months={gw: "AUG" for gw in range(1, 39)})
    buckets = [{"month": "AUG", "gameweeks": list(range(1, 39))}]
    settlement = settle(MANAGERS, calendar, buckets, expected_gameweeks=38)
    payload = build_payload(
        generated_at="2027-05-31T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=MANAGERS, teams=TEAMS, events=EVENTS, breaks=[],
        month_buckets=buckets, fixtures_by_gw={}, pl_table=[],
        gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 38, "next_gw": 38, "state": "final"},
    )
    assert payload["settlement"]["settled"] is True
    assert payload["settled"]["projected"] == "season pot settled"
    # The season pot is now inside the book, so it must reach the settlement.
    assert sum(p["amount"] for p in payload["settlement"]["payments"]) > 0


# --- §3.9.4 corrections -------------------------------------------------------

CORRECTION = {
    "type": "correction", "date": "2027-03-14", "affects_gw": 1,
    "reason": "GW1 tiebreak applied cards before goals conceded",
    "adjustments": {"jack": 3500, "soonlee": -3500},
}


def test_a_correction_adjusts_the_accrued_balance():
    payload = make_payload(corrections=[CORRECTION])
    assert payload["ledger"]["jack"]["accrued"] == 1800 + 3500
    assert payload["ledger"]["soonlee"]["accrued"] == 13800 - 3500
    assert sum(v["accrued"] for v in payload["ledger"].values()) == 0
    assert payload["checks"]["zero_sum"] is True


def test_a_correction_appears_as_its_own_statement_row_and_never_edits_history():
    payload = make_payload(corrections=[CORRECTION])
    jack = payload["ledger"]["jack"]["statement"]
    # The original entries are untouched...
    assert jack[0]["detail"] == "68 pts, 2nd of 8 — 30% share"
    assert jack[2]["detail"] == "134 pts, 2nd in August — 30% share"
    # ...and the adjustment is appended, with its reason.
    assert jack[-1] == {
        "date": "2027-03-14", "type": "correction", "affects_gw": 1,
        "detail": CORRECTION["reason"], "amount": 3500, "balance": 5300,
    }


def test_a_correction_reaches_the_settlement_sheet():
    plain = make_payload()
    corrected = make_payload(corrections=[CORRECTION])
    assert plain["settlement"]["payments"] != corrected["settlement"]["payments"]
    net = {m: 0 for m in corrected["ledger"]}
    for payment in corrected["settlement"]["payments"]:
        net[payment["from"]] -= payment["amount"]
        net[payment["to"]] += payment["amount"]
    assert net == {m: v["accrued"] for m, v in corrected["ledger"].items()}


def test_a_correction_that_does_not_sum_to_zero_is_rejected():
    bad = dict(CORRECTION, adjustments={"jack": 3500, "soonlee": -3000})
    with pytest.raises(LedgerError, match="does not sum to zero"):
        corrections_mod.validate([bad])


def test_a_correction_naming_an_unknown_manager_is_rejected():
    bad = dict(CORRECTION, adjustments={"ghost": 100, "jack": -100})
    with pytest.raises(LedgerError, match="unknown managers"):
        corrections_mod.validate([bad], known={"jack", "soonlee"})


def test_a_correction_missing_its_reason_is_rejected():
    bad = {k: v for k, v in CORRECTION.items() if k != "reason"}
    with pytest.raises(LedgerError, match="reason"):
        corrections_mod.validate([bad])


def test_corrections_are_published_so_they_can_be_read(payload):
    assert payload["corrections"] == []
    assert make_payload(corrections=[CORRECTION])["corrections"] == [CORRECTION]


# --- §3.9.1 terminology -------------------------------------------------------

def test_the_projected_season_stays_out_of_the_accrued_balance(payload):
    """'Projected' is not in the book at all until GW38 is Final."""
    for manager, entry in payload["ledger"].items():
        assert entry["accrued"] == entry["weekly"] + entry["monthly"], manager
        assert entry["projected_season"] != 0
