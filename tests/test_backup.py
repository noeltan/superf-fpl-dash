"""The backup tables — the record the end-of-season settle-up is paid from.

Money never moves during the season, so this record *is* the debt. If it is
wrong in November nobody finds out until somebody is asked for RM400 in May,
which is why the tables are asserted against the settled book rather than
eyeballed in a spreadsheet.
"""

from __future__ import annotations

import json

import pytest

from conftest import AUGUST, GW1_POINTS, GW2_POINTS, full_season
from superf.backup import TABLES, ledger_rows, write_csv_snapshots
from superf.config import DATA_JSON
from superf.emit import build_payload
from superf.ledger import ManagerScore, settle

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


def payload_for(calendar, managers=MANAGERS, gameweek=2, next_gw=3):
    settlement = settle(managers, calendar, BUCKETS, expected_gameweeks=38)
    return build_payload(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=managers, teams=TEAMS, events=EVENTS, breaks=[],
        month_buckets=BUCKETS, fixtures_by_gw={}, pl_table=[], gameweeks=calendar,
        settlement=settlement,
        current={"season": "2026/27", "gameweek": gameweek, "next_gw": next_gw, "state": "final"},
        settled_dates={1: "2026-08-24", 2: "2026-08-31"},
    )


@pytest.fixture
def payload():
    return payload_for(
        full_season({1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"})
    )


def _col(rows, name):
    return rows[0].index(name)


def test_the_league_tab_has_a_column_per_settled_gameweek(payload):
    rows = ledger_rows(payload)
    header = rows[0]
    assert header[:3] == ["manager", "team", "entry_id"]
    # In calendar order, and immediately before the season total.
    assert header[3:6] == ["GW1", "GW2", "points"]


def test_the_gameweek_columns_are_that_manager_s_real_scores(payload):
    rows = ledger_rows(payload)
    gw1, gw2 = _col(rows, "GW1"), _col(rows, "GW2")
    by_name = {row[0]: row for row in rows[1:-1]}
    assert by_name["Noel Tan"][gw1] == GW1_POINTS["noel"]
    assert by_name["Noel Tan"][gw2] == GW2_POINTS["noel"]
    assert by_name["Soon Lee Loo"][gw1] == GW1_POINTS["soonlee"]


def test_the_gameweek_columns_add_up_to_the_season_total(payload):
    """The one thing a reader will check by selecting the row."""
    rows = ledger_rows(payload)
    first, total = _col(rows, "GW1"), _col(rows, "points")
    for row in rows[1:-1]:
        weeks = [cell for cell in row[first:total] if cell != ""]
        assert sum(weeks) == row[total], row[0]


def test_a_manager_who_was_not_in_the_league_gets_a_blank_not_a_zero():
    """§3.8.6 — a zero would drag down any average taken over the column, and
    would read as "played, scored nothing" rather than "was not here"."""
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    roster = MANAGERS + [
        {"id": "late", "display_name": "Late Joiner", "short": "Late",
         "team_name": "Latecomer", "entry_id": 9999999}
    ]
    for gameweek in calendar.values():
        gameweek.scores["late"] = ManagerScore(points=None, active=False)
    calendar[2].scores["late"] = ManagerScore(points=41, active=True)

    rows = ledger_rows(payload_for(calendar, managers=roster))
    late = next(row for row in rows[1:-1] if row[0] == "Late Joiner")
    assert late[_col(rows, "GW1")] == ""      # not in the league yet
    assert late[_col(rows, "GW2")] == 41


def test_a_manager_who_never_set_a_team_really_did_score_zero():
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    calendar[2].scores["chris"].did_not_set = True
    calendar[2].scores["chris"].points = 0
    rows = ledger_rows(payload_for(calendar))
    chris = next(row for row in rows[1:-1] if row[0] == "Christopher Chin")
    assert chris[_col(rows, "GW2")] == 0


def test_every_row_is_the_same_width_as_the_header(payload):
    """A ragged row silently shifts every money column right of the break."""
    rows = ledger_rows(payload)
    assert len({len(row) for row in rows}) == 1, [len(r) for r in rows]


def test_the_money_columns_survived_the_gameweek_columns(payload):
    """The tab is still the accrued book — the scores were added beside the
    money, not in place of it."""
    rows = ledger_rows(payload)
    for column in ("weekly_rm", "monthly_rm", "season_rm", "accrued_rm", "position"):
        assert column in rows[0]
    accrued = _col(rows, "accrued_rm")
    assert rows[-1][0] == "TOTAL"
    # The book balances, and the TOTAL row still lands under accrued_rm.
    assert rows[-1][accrued] == "0.00"


def test_the_published_book_produces_a_consistent_league_tab():
    published = json.loads(DATA_JSON.read_text())
    if not published.get("gameweeks"):
        pytest.skip("nothing settled in the published book yet")
    rows = ledger_rows(published)
    expected = [f"GW{g['gw']}" for g in published["gameweeks"]]
    assert [c for c in rows[0] if str(c).startswith("GW")] == expected
    first, total = _col(rows, "GW1"), _col(rows, "points")
    for row in rows[1:-1]:
        assert sum(c for c in row[first:total] if c != "") == row[total], row[0]


def test_the_csv_snapshot_carries_the_same_tab(payload, tmp_path):
    written = write_csv_snapshots(payload, root=tmp_path)
    assert written == len(TABLES) * 2      # the gameweek directory and `latest`
    header = (tmp_path / "latest" / "ledger.csv").read_text().splitlines()[0]
    assert "GW1,GW2,points" in header
