"""The message that leaves the site.

The summary is forwarded, screenshotted and argued with, usually by someone who
never opens the dashboard. So it has to say the same numbers the ledger settled
and use §3.9.1's vocabulary while doing it — a message that says somebody "won
RM81" starts a conversation about cash that does not exist until May.

These drive the real emitter and assert the composed text, because the failure
mode here is not an exception: it is a sentence that reads fine and is wrong.
"""

from __future__ import annotations

import json

import pytest

from conftest import AUGUST, GW1_POINTS, GW2_POINTS, full_season
from superf.config import DATA_JSON
from superf.emit import build_payload
from superf.ledger import settle
from superf.summary import build
from superf.tiebreak import TiebreakStats

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

SETTLED_DATES = {1: "2026-08-24", 2: "2026-08-31"}


def payload_for(
    played,
    *,
    buckets=None,
    current=None,
    stats=None,
    settled_dates=None,
):
    calendar = full_season(
        played, managers=MANAGERS, months={1: "AUG", 2: "AUG"}, stats=stats
    )
    buckets = buckets or BUCKETS
    settlement = settle(MANAGERS, calendar, buckets, expected_gameweeks=38)
    final = sorted(played)
    return build_payload(
        generated_at="2026-09-01T06:00:00Z",
        league_name="SuperF",
        league_id=310479,
        managers=MANAGERS,
        teams=TEAMS,
        events=EVENTS,
        breaks=[],
        month_buckets=buckets,
        fixtures_by_gw={},
        pl_table=[],
        gameweeks=calendar,
        settlement=settlement,
        current=current
        or {
            "season": "2026/27",
            "gameweek": final[-1] if final else 0,
            "next_gw": (final[-1] + 1) if final else 1,
            "state": "final",
        },
        settled_dates=settled_dates or SETTLED_DATES,
    )


@pytest.fixture
def summary():
    return payload_for({1: GW1_POINTS, 2: GW2_POINTS})["summary"]


def test_nothing_settled_means_no_summary_at_all():
    """An empty book gets no message. "GW0, nobody is owed anything" is noise."""
    payload = payload_for(
        {},
        current={"season": "2026/27", "gameweek": 0, "next_gw": 1, "state": "upcoming"},
    )
    assert payload["summary"] is None


def test_the_summary_names_the_weekly_winner_and_what_they_are_owed(summary):
    week = "\n".join(summary["blocks"][0]["lines"])
    # GW2: Soon Lee 69 takes it, Noel 68 second, at N=8 — 46 and 14.
    assert "Soon Lee takes GW2 on 69 pts and is owed RM46." in week
    assert "Noel is second on 68 and is owed RM14." in week
    assert "The other 6 managers owe RM10 each. Pot RM80." in week


def test_the_summary_quotes_what_that_gameweek_paid_not_todays_rate():
    """§3.8.6 — a gameweek settled at a smaller field is never recomputed.

    The message is the most-forwarded artifact the league produces, so quoting
    the advertised net here would print money nobody received, in the one place
    nobody can check it against the ledger.
    """
    from superf.ledger import ManagerScore

    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    # A ninth manager is on the roster but joins at GW3, so the league is N=9
    # today while GW2 settled at N=8.
    roster = MANAGERS + [
        {"id": "late", "display_name": "Late Joiner", "short": "Late",
         "team_name": "Latecomer", "entry_id": 9999999}
    ]
    for gw, gameweek in calendar.items():
        gameweek.scores["late"] = ManagerScore(points=None, active=False)

    settlement = settle(roster, calendar, BUCKETS, expected_gameweeks=38)
    payload = build_payload(
        generated_at="2026-09-01T06:00:00Z",
        league_name="SuperF",
        league_id=310479,
        managers=roster,
        teams=TEAMS,
        events=EVENTS,
        breaks=[],
        month_buckets=BUCKETS,
        fixtures_by_gw={},
        pl_table=[],
        gameweeks=calendar,
        settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES,
    )
    week = "\n".join(payload["summary"]["blocks"][0]["lines"])
    # GW2 ran at N=8: pot RM80, 70% = RM56, less the RM10 stake = RM46.
    assert "is owed RM46." in week
    assert "Pot RM80." in week
    # Today's advertised figure is RM53 at N=9, and must not appear.
    assert payload["stakes"]["weekly"]["net"][0] == 53
    assert "RM53" not in week


def test_the_monthly_winner_appears_only_once_the_month_is_complete():
    """"When time" — the bucket's last gameweek has to be Final first."""
    mid = payload_for(
        {1: GW1_POINTS},
        current={"season": "2026/27", "gameweek": 1, "next_gw": 2, "state": "final"},
        settled_dates={1: "2026-08-24"},
    )["summary"]
    headings = [b["heading"] for b in mid["blocks"]]
    assert "August — running" in headings
    assert "August — settled" not in headings
    assert mid["monthly_settled"] is False
    running = "\n".join(
        line
        for b in mid["blocks"]
        if b["heading"] == "August — running"
        for line in b["lines"]
    )
    # A running month states a standing and an "if it held", never an accrual.
    assert "would take" in running
    assert "Nothing settles until GW2 is final." in running
    assert "is owed" not in running


def test_a_complete_month_names_the_monthly_winner_and_the_runner_up(summary):
    month = next(b for b in summary["blocks"] if b["heading"] == "August — settled")
    body = "\n".join(month["lines"])
    assert "August is settled over GW1–GW2." in body
    assert "Soon Lee takes the month on 141 pts and is owed RM46." in body
    assert "Jack is second on 134 and is owed RM14." in body
    assert summary["monthly_settled"] is True


def test_the_season_pot_is_never_stated_as_owed(summary):
    """§3.9.1 — projected is not in the book, so it cannot read like a credit."""
    season = next(b for b in summary["blocks"] if b["heading"] == "Season so far")
    body = "\n".join(season["lines"])
    assert "projected only" in body
    assert "GW38" in body
    assert "is owed" not in body


def test_the_message_never_says_won_or_collected(summary):
    """The words the league is not allowed to use about money it has not moved."""
    text = summary["text"].lower()
    for forbidden in (" won ", "collected", "payout", "winnings", "cash out"):
        assert forbidden not in text, forbidden
    assert "accrued, not cash" in text


def test_every_active_manager_appears_in_the_scoreboard(summary):
    scores = next(b for b in summary["blocks"] if b["heading"] == "Every score")
    assert len(scores["lines"]) == len(MANAGERS)
    # Ordered by the settled result: the paid places first, then by points.
    assert scores["lines"][0].startswith("1. Soon Lee — 69 pts (+RM46)")
    assert scores["lines"][1].startswith("2. Noel — 68 pts (+RM14)")
    assert scores["lines"][-1].startswith("8. Chris — 0 pts (√RM10)".replace("√", "−"))


def test_a_manager_who_never_set_a_team_is_marked_and_still_charged():
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    calendar[2].scores["chris"].did_not_set = True
    settlement = settle(MANAGERS, calendar, BUCKETS, expected_gameweeks=38)
    payload = build_payload(
        generated_at="2026-09-01T06:00:00Z",
        league_name="SuperF", league_id=310479, managers=MANAGERS, teams=TEAMS,
        events=EVENTS, breaks=[], month_buckets=BUCKETS, fixtures_by_gw={},
        pl_table=[], gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES,
    )
    scores = next(
        b for b in payload["summary"]["blocks"] if b["heading"] == "Every score"
    )
    chris = next(line for line in scores["lines"] if "Chris" in line)
    assert "[no team set]" in chris
    assert "−RM10" in chris


def test_a_split_pot_reads_as_a_split():
    """§3.5 level 5 — tied managers share both paid places, and it must say so."""
    tied = dict(GW2_POINTS, noel=69)
    payload = payload_for({1: GW1_POINTS, 2: tied})
    week = "\n".join(payload["summary"]["blocks"][0]["lines"])
    assert "Noel and Soon Lee tie GW2 on 69 pts" in week
    assert "are owed RM30 each" in week
    # No second place survives a level-5 tie, so none is named.
    assert "is second on" not in week


def test_a_tiebreak_is_explained_in_the_message():
    stats = {
        2: {
            "soonlee": TiebreakStats(goals=4, assists=4),
            "noel": TiebreakStats(goals=4, assists=2),
        }
    }
    tied = dict(GW2_POINTS, noel=69)
    payload = payload_for({1: GW1_POINTS, 2: tied}, stats=stats)
    week = "\n".join(payload["summary"]["blocks"][0]["lines"])
    assert "Split by the tiebreak:" in week
    assert "assists" in week


def test_the_next_deadline_is_quoted_in_malaysia_time(summary):
    """§9.4 — MYT first, everywhere. A UTC deadline here costs somebody a team."""
    # GW3 locks 2026-09-03T17:30Z, which is 01:30 on the 4th in Kuala Lumpur.
    assert "GW3 deadline: Fri 4 Sep, 01:30 MYT." in summary["footer"]


def test_the_text_is_assembled_from_the_blocks_the_card_renders(summary):
    """One source. A card and a message that can disagree eventually will."""
    for block in summary["blocks"]:
        assert block["heading"].upper() in summary["text"]
        for line in block["lines"]:
            assert line in summary["text"]
    assert summary["text"].startswith(summary["title"])
    assert summary["text"].rstrip("\n").endswith(summary["footer"])


def test_the_summary_reads_the_published_book_not_a_second_derivation():
    """Rebuilding it from `docs/data.json` must reproduce what was published."""
    published = json.loads(DATA_JSON.read_text())
    if not published.get("gameweeks"):
        pytest.skip("nothing settled in the published book yet")
    assert published["summary"] == build(published)


def test_the_published_summary_agrees_with_the_published_ledger():
    """Every credit the message names is a row somebody can find in the book."""
    published = json.loads(DATA_JSON.read_text())
    summary = published.get("summary")
    if not summary:
        pytest.skip("nothing settled in the published book yet")
    gw = summary["gw"]
    gameweek = next(g for g in published["gameweeks"] if g["gw"] == gw)
    for manager in gameweek["winners"]:
        amount = published["ledger"][manager]["by_gameweek"][-1]
        assert f"RM{amount // 100}" in "\n".join(summary["blocks"][0]["lines"])


# --- from the PR review: four things the first cut got wrong ------------------


def test_a_tie_for_the_month_reads_as_a_tie():
    """A level-5 tie for the month splits both paid shares; the message must not
    crown one of them and call the other second."""
    # GW1 as spec, GW2 arranged so noel and soonlee finish August dead level
    # on 141 with identical (empty) tiebreak stats.
    tied_gw2 = dict(GW2_POINTS, noel=141 - GW1_POINTS["noel"], soonlee=141 - GW1_POINTS["soonlee"])
    payload = payload_for({1: GW1_POINTS, 2: tied_gw2})
    month = payload["months"][0]
    assert sorted(month["winners"]) == ["noel", "soonlee"]
    assert month["runners_up"] == []
    body = "\n".join(
        b["lines"][1] for b in payload["summary"]["blocks"] if b["heading"] == "August — settled"
    )
    assert "Noel and Soon Lee tie the month on 141 pts" in body
    assert "is second" not in "\n".join(
        line for b in payload["summary"]["blocks"] if b["heading"] == "August — settled" for line in b["lines"]
    )
    # And the amount quoted is each one's own line in the ledger, not net[0].
    assert f"RM{month['ledger']['noel'] // 100}" in body


def test_month_debits_are_read_off_the_ledger_not_assumed_to_be_the_stake():
    """§3.8.6 — a manager who joined for the last gameweek of a bucket owes one
    gameweek's stake, and the message must say so rather than charge the full
    month to "the other N"."""
    from superf.ledger import ManagerScore

    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    roster = MANAGERS + [
        {"id": "late", "display_name": "Late Joiner", "short": "Late",
         "team_name": "Latecomer", "entry_id": 9999999}
    ]
    for gw, gameweek in calendar.items():
        gameweek.scores["late"] = ManagerScore(points=None, active=False)
    calendar[2].scores["late"] = ManagerScore(points=1, active=True)
    settlement = settle(roster, calendar, BUCKETS, expected_gameweeks=38)
    payload = build_payload(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=roster, teams=TEAMS, events=EVENTS, breaks=[], month_buckets=BUCKETS,
        fixtures_by_gw={}, pl_table=[], gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES,
    )
    month = payload["months"][0]
    # One gameweek in the bucket, so one gameweek's monthly stake — RM5, not RM10.
    assert month["ledger"]["late"] == -500
    assert month["ledger"]["noel"] == -1000
    body = "\n".join(
        line for b in payload["summary"]["blocks"] if b["heading"] == "August — settled" for line in b["lines"]
    )
    assert "owe their stake — RM5 to RM10, by gameweeks played" in body
    assert "owe RM10 each" not in body


def test_rank_prev_is_empty_until_there_is_a_before():
    """With one gameweek settled, "the rank before it" is a book of zeros sorted
    by id — and the league table's movement arrows would print it as places
    gained. Empty means no arrow."""
    one = payload_for(
        {1: GW1_POINTS},
        current={"season": "2026/27", "gameweek": 1, "next_gw": 2, "state": "final"},
        settled_dates={1: "2026-08-24"},
    )
    assert one["rank_prev"] == {}
    two = payload_for({1: GW1_POINTS, 2: GW2_POINTS})
    assert two["rank_prev"]["soonlee"] == 1   # led after GW1


def test_the_running_month_survives_its_last_gameweek_being_live():
    """`month_current` follows next_gw, and while a bucket's last gameweek is
    being played next_gw already points a month ahead. The summary for the
    gameweek before it must still carry the running month."""
    buckets = [{"month": "AUG", "gameweeks": [1, 2, 3]}, {"month": "SEP", "gameweeks": list(range(4, 39))}]
    live = payload_for(
        {1: GW1_POINTS, 2: GW2_POINTS},
        buckets=buckets,
        # GW3, the bucket's last gameweek, is live: derive_current sets next_gw = 4.
        current={"season": "2026/27", "gameweek": 3, "next_gw": 4, "state": "live"},
    )
    assert live["month_current"]["month"] == "SEP"          # the trap
    headings = [b["heading"] for b in live["summary"]["blocks"]]
    assert "August — running" in headings
    running = "\n".join(
        line for b in live["summary"]["blocks"] if b["heading"] == "August — running" for line in b["lines"]
    )
    assert "August is 2 of 3 gameweeks in" in running
    assert "Nothing settles until GW3 is final." in running
