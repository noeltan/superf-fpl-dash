"""The emitted payload must match the prototype's ``DATA`` object, key for key.

The prototype is the contract (spec §5 is updated to match it, see SCHEMA.md).
These tests drive the real emitter with §3.8.7's two gameweeks and assert the
result against the values the design team hardcoded in their mock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import AUGUST, GW1_POINTS, GW2_POINTS, LEAGUE, full_season
from superf.emit import build_payload, exposure_block, stakes_block
from superf.ledger import settle

MANAGERS = [
    {"id": "noel", "display_name": "Noel Tan", "short": "Noel", "team_name": "JEONSOMI", "entry_id": 1652821},
    {"id": "jack", "display_name": "Jack Siah", "short": "Jack", "team_name": "Speedmaster", "entry_id": 1427521},
    {"id": "sam", "display_name": "Sam Lim", "short": "Sam", "team_name": "GeGe", "entry_id": 2686159},
    {"id": "weihun", "display_name": "Wei Hun Wong", "short": "Wei Hun", "team_name": "Vini Jr's Chin", "entry_id": 4590653},
    {"id": "soonlee", "display_name": "Soon Lee Loo", "short": "Soon Lee", "team_name": "SLucky XI", "entry_id": 2488327},
    {"id": "boonsiang", "display_name": "Boon Siang Ng", "short": "Boon", "team_name": "Boon", "entry_id": 1840731},
    {"id": "tianpin", "display_name": "Tian Pin Lee", "short": "Tian Pin", "team_name": "Vege went tight", "entry_id": 1567532},
    {"id": "chris", "display_name": "Christopher Chin", "short": "Chris", "team_name": "My Team", "entry_id": 1641730},
]

TEAMS = {i: {"name": f"Team {i}", "short_name": f"T{i:02d}"} for i in range(1, 21)}

EVENTS = [
    {"gw": gw, "deadline": f"2026-08-{20 + gw:02d}T17:30:00Z", "month": "AUG"}
    for gw in range(1, 3)
] + [
    {"gw": gw, "deadline": f"2026-09-{gw:02d}T17:30:00Z", "month": "SEP"}
    for gw in range(3, 39)
]

BUCKETS = AUGUST + [{"month": "SEP", "gameweeks": list(range(3, 39))}]


@pytest.fixture
def payload():
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS},
        managers=MANAGERS,
        months={1: "AUG", 2: "AUG"},
    )
    calendar[2].scores["chris"].did_not_set = True
    settlement = settle(MANAGERS, calendar, BUCKETS, expected_gameweeks=38)
    return build_payload(
        generated_at="2026-09-01T06:00:00Z",
        league_name="SuperF",
        league_id=310479,
        managers=MANAGERS,
        teams=TEAMS,
        events=EVENTS,
        breaks=[],
        month_buckets=BUCKETS,
        fixtures_by_gw={1: [], 2: [], 3: []},
        pl_table=[],
        gameweeks=calendar,
        settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
    )


# --- every key the view reads ------------------------------------------------

TOP_LEVEL = [
    "generated_at", "league", "current", "tz", "stakes", "exposure", "managers",
    "teams", "events", "breaks", "month_buckets", "fixtures", "pl_table",
    "gameweeks", "months", "month_current", "totals", "rank", "rank_prev",
    "behind", "ledger", "corrections", "settlement", "podiums", "weeks_won",
    "stats", "settled", "checks",
]


def test_every_top_level_key_the_prototype_reads_is_present(payload):
    assert set(TOP_LEVEL) <= set(payload)


def test_manager_records_carry_every_field(payload):
    for manager in payload["managers"]:
        assert set(manager) >= {"id", "display_name", "short", "team_name", "entry_id"}


def test_ledger_records_carry_every_field(payload):
    for entry in payload["ledger"].values():
        assert set(entry) >= {
            "weekly", "monthly", "accrued", "projected_season", "delta_last_gw",
            "by_gameweek", "statement",
        }


def test_gameweek_records_carry_every_field(payload):
    for gameweek in payload["gameweeks"]:
        assert set(gameweek) >= {
            "gw", "month", "note", "scores", "winners", "pot", "tiebreak", "bonus_change"
        }
        for score in gameweek["scores"].values():
            assert set(score) >= {"points", "hits", "transfers", "chip", "did_not_set"}


# --- values, against the prototype's mock ------------------------------------

def test_stakes_block_at_n_eight():
    """The weekly pot is RM10 a head split 70/30 — our stakes, not the RM5
    winner-takes-all the spec and the design mock were written against."""
    assert stakes_block(8, 3) == {
        "weekly": {"stake": 10, "pot": 80, "split": [0.70, 0.30], "net": [46, 14]},
        "monthly": {"stake_per_gw": 5, "gameweeks": 3, "stake": 15, "pot": 120,
                    "split": [0.70, 0.30], "net": [69, 21]},
        "season": {"stake": 100, "pot": 800, "split": [0.60, 0.25, 0.15],
                   "net": [380, 100, 20]},
    }


def test_exposure_is_derived_from_the_stakes():
    """RM15 a gameweek (RM10 week + RM5 month) x 38, plus RM100 on the season.
    §3.4's RM480 predates the weekly pot doubling."""
    assert exposure_block(8) == {"staked": 670, "best": 3002, "worst": -670}
    assert exposure_block(8)["staked"] == 38 * (10 + 5) + 100


def test_the_ledger_block_is_sen_and_balances(payload):
    """The ledger, statement and settlement are sen (§3.8.1); stakes and exposure
    stay in ringgit. Figures are our 70/30 weekly, so they no longer match the
    design mock, which was drawn against RM5 winner-takes-all."""
    expected = {
        "noel": {"weekly": 400, "monthly": -1000, "accrued": -600, "projected_season": -10000},
        "jack": {"weekly": 400, "monthly": 1400, "accrued": 1800, "projected_season": 10000},
        "sam": {"weekly": -2000, "monthly": -1000, "accrued": -3000, "projected_season": -10000},
        "weihun": {"weekly": -2000, "monthly": -1000, "accrued": -3000, "projected_season": 2000},
        "soonlee": {"weekly": 9200, "monthly": 4600, "accrued": 13800, "projected_season": 38000},
        "boonsiang": {"weekly": -2000, "monthly": -1000, "accrued": -3000, "projected_season": -10000},
        "tianpin": {"weekly": -2000, "monthly": -1000, "accrued": -3000, "projected_season": -10000},
        "chris": {"weekly": -2000, "monthly": -1000, "accrued": -3000, "projected_season": -10000},
    }
    for manager, values in expected.items():
        actual = payload["ledger"][manager]
        for key, value in values.items():
            assert actual[key] == value, f"{manager}.{key}"
    assert sum(payload["ledger"][m]["accrued"] for m in payload["ledger"]) == 0


def test_by_gameweek_is_the_per_gameweek_weekly_amount(payload):
    """§5's own example is [-500, -500] in sen, not a running total."""
    assert payload["ledger"]["noel"]["by_gameweek"] == [-1000, 1400]
    assert payload["ledger"]["soonlee"]["by_gameweek"] == [4600, 4600]


def test_standings_match_the_mock(payload):
    assert payload["rank"] == [
        "soonlee", "jack", "weihun", "sam", "noel", "boonsiang", "tianpin", "chris"
    ]
    assert payload["totals"] == {
        "noel": 114, "jack": 134, "sam": 116, "weihun": 117,
        "soonlee": 141, "boonsiang": 113, "tianpin": 109, "chris": 64,
    }
    assert payload["behind"] == {
        "soonlee": 0, "jack": 7, "weihun": 24, "sam": 25,
        "noel": 27, "boonsiang": 28, "tianpin": 32, "chris": 77,
    }
    assert payload["weeks_won"] == {
        "soonlee": 2, "jack": 0, "sam": 0, "weihun": 0,
        "noel": 0, "boonsiang": 0, "tianpin": 0, "chris": 0,
    }
    assert payload["podiums"]["soonlee"] == 2
    assert payload["stats"]["avg_points"] == 113.5
    assert payload["stats"]["high"] == {"gw": 1, "manager": "soonlee", "points": 72}


def test_august_month_matches_the_mock(payload):
    august = payload["months"][0]
    assert august["month"] == "AUG"
    assert august["gameweeks"] == [1, 2] and august["played"] == [1, 2]
    assert august["complete"] is True
    assert august["stake"] == 10 and august["pot"] == 80
    assert august["net"] == [46, 14]
    assert august["order"][:2] == ["soonlee", "jack"]
    assert august["totals"]["soonlee"] == 141
    assert "August" in august["callout"]


def test_only_complete_months_are_listed(payload):
    """The view labels the last entry SETTLED, so an in-flight month would lie."""
    assert [m["month"] for m in payload["months"]] == ["AUG"]
    assert payload["month_current"]["month"] == "SEP"
    assert payload["month_current"]["opens_gw"] == 3


def test_missed_deadline_is_flagged_and_still_charged(payload):
    gw2 = payload["gameweeks"][1]
    assert gw2["scores"]["chris"]["did_not_set"] is True
    assert payload["ledger"]["chris"]["by_gameweek"][1] == -1000


def test_checks_block_is_honest(payload):
    assert payload["checks"] == {
        "zero_sum": True, "gameweeks_expected": 38, "gameweeks_present": 38
    }
    assert payload["settled"]["through_gw"] == 2
    assert payload["settled"]["projected"] == "season pot projected, not banked"


def test_winners_is_an_array_so_a_split_pot_fits(payload):
    for gameweek in payload["gameweeks"]:
        assert isinstance(gameweek["winners"], list)
    assert payload["gameweeks"][0]["winners"] == ["soonlee"]


def test_payload_is_json_serialisable(payload):
    assert json.loads(json.dumps(payload))


# --- what a gameweek paid, versus what it would pay today --------------------

def test_the_weekly_row_names_second_place(payload):
    """70/30 means second place is paid, so the row has to identify it or the
    card hides a credit somebody is owed."""
    gw1 = payload["gameweeks"][0]
    assert gw1["winners"] == ["soonlee"]           # 72 pts
    assert gw1["runners_up"] == ["jack"]           # 68 pts
    assert gw1["winner_net"] == 4600
    assert gw1["runner_up_net"] == 1400


def test_a_tie_for_first_leaves_no_second_place_to_name(payload):
    """The tied pair take both shares between them, so there is no runner-up."""
    from superf.tiebreak import Ranking
    assert Ranking(groups=[["a", "b"], ["c"]]).runners_up == []
    assert Ranking(groups=[["a"], ["b", "c"]]).runners_up == ["b", "c"]
    assert Ranking(groups=[["a"]]).runners_up == []


def test_the_weekly_row_reports_what_that_gameweek_paid_not_todays_rate():
    """A gameweek played before somebody joined settled a smaller pot, and the
    snapshot of it is never recomputed (§3.8.6). Reporting the advertised
    `stakes.weekly.net` against that row would print money nobody received."""
    ninth = {"id": "newguy", "display_name": "New Guy", "short": "New",
             "team_name": "Late Entry", "entry_id": 9999999}
    roster = MANAGERS + [ninth]
    played = {1: GW1_POINTS, 2: dict(GW2_POINTS, newguy=40)}
    calendar = full_season(played, managers=roster, months={1: "AUG", 2: "AUG"})
    settlement = settle(roster, calendar, BUCKETS, expected_gameweeks=38)
    built = build_payload(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=roster, teams=TEAMS, events=EVENTS, breaks=[], month_buckets=BUCKETS,
        fixtures_by_gw={}, pl_table=[], gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
    )
    gw1, gw2 = built["gameweeks"][0], built["gameweeks"][1]
    # GW1 settled at N=8: RM80 pot, 70% less stake.
    assert gw1["pot"] == 80 and gw1["winner_net"] == 4600
    # GW2 settled at N=9: RM90 pot, so the same finish is worth more.
    assert gw2["pot"] == 90 and gw2["winner_net"] == 5300
    # And neither equals the advertised figure at today's size for the other row.
    assert built["stakes"]["weekly"]["net"] == [53, 17]
    assert gw1["winner_net"] != built["stakes"]["weekly"]["net"][0] * 100


# --- the live artefact --------------------------------------------------------

DATA_JSON = Path(__file__).resolve().parent.parent / "docs" / "data.json"


@pytest.mark.skipif(not DATA_JSON.exists(), reason="run build.py first")
def test_the_published_file_conforms():
    published = json.loads(DATA_JSON.read_text())
    assert set(TOP_LEVEL) <= set(published)
    assert published["checks"]["zero_sum"] is True
    assert published["checks"]["gameweeks_present"] == 38
    assert len(published["events"]) == 38
    assert len(published["teams"]) == 20
    assert published["league"]["players"] == len(published["managers"])
    assert sum(published["ledger"][m]["accrued"] for m in published["ledger"]) == 0
    assert len(published["settlement"]["payments"]) <= len(published["managers"]) - 1
    for manager in published["managers"]:
        assert manager["id"] in published["ledger"]
        assert manager["id"] in published["totals"]
