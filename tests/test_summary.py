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
    """§3.9.1 — projected is not in the book, so it cannot read like a credit.

    Enforced now by the season block naming no money at all: it is a points
    standing. The old wording carried a "not in anybody's book until GW38"
    caveat, which went with the rest of the not-paid-yet refrain.
    """
    season = next(b for b in summary["blocks"] if b["heading"] == "Season so far")
    body = "\n".join(season["lines"])
    assert "RM" not in body
    for word in ("owed", "owe", "projected"):
        assert word not in body.lower(), word
    assert "is owed" not in body


def test_the_message_never_says_won_or_collected(summary):
    """The words the league is not allowed to use about money it has not moved.

    The vocabulary is the whole guard now: with the standing disclaimer gone,
    "is owed" and "owe" are the only things telling a reader these are
    accruals, so a stray "won" or "paid" would be the message's one claim that
    cash moved.
    """
    text = summary["text"].lower()
    for forbidden in (" won ", "collected", "payout", "winnings", "cash out", " paid "):
        assert forbidden not in text, forbidden
    assert "is owed" in text


def test_the_message_carries_no_nothing_is_paid_yet_disclaimer(summary):
    """Asked for across the whole site, the message included.

    Being told the same caveat every week is how a caveat stops being read.
    """
    text = summary["text"]
    for refrain in ("Nothing is paid", "accrued, not cash", "not in anybody's book",
                    "until GW38", "settles up once"):
        assert refrain not in text, refrain
    # What the footer is actually for survives.
    assert summary["footer"] == "GW3 deadline: Fri 4 Sep, 01:30 MYT."


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


# --- from the review of the GW2 message -------------------------------------


def _payload_with(mutate, **overrides):
    """The spec fixture with one thing changed on the calendar, emitted whole."""
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    mutate(calendar)
    settlement = settle(MANAGERS, calendar, BUCKETS, expected_gameweeks=38)
    kwargs = dict(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=MANAGERS, teams=TEAMS, events=EVENTS, breaks=[], month_buckets=BUCKETS,
        fixtures_by_gw={}, pl_table=[], gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES,
    )
    kwargs.update(overrides)
    return build_payload(**kwargs)


def test_chips_are_named_as_the_page_names_them():
    """`[bboost]` is an API code, not a word. GW1 printed it next to two scores."""
    import re
    from pathlib import Path

    from superf import copy as copytext

    def bench_boost(calendar):
        calendar[2].scores["jack"].chip = "bboost"

    payload = _payload_with(bench_boost)
    scores = next(b for b in payload["summary"]["blocks"] if b["heading"] == "Every score")
    jack = next(line for line in scores["lines"] if "Jack" in line)
    assert "bboost" not in jack
    assert "[Bench Boost]" in jack
    # One map, two languages: the page's `chipLabel` must agree with copy.py's.
    app = Path(__file__).resolve().parent.parent / "docs" / "app.js"
    source = app.read_text(encoding="utf-8")
    body = re.search(r"chipLabel\(code\)\{.*?\}\[code\]", source, re.S).group(0)
    page = dict(re.findall(r'"?([\w]+)"?:\s*"([^"]+)"', body))
    assert {k: v.upper() for k, v in copytext.CHIP_LABELS.items()} == page


def test_managers_level_on_points_share_a_place():
    """3 and 4 on the same score claims an order nothing decided."""
    # Soon Lee 69 and Noel 68 take the paid places; Jack and Tian Pin both on 66.
    payload = payload_for({1: GW1_POINTS, 2: dict(GW2_POINTS, tianpin=66)})
    scores = next(b for b in payload["summary"]["blocks"] if b["heading"] == "Every score")
    level = [line for line in scores["lines"] if " 66 pts" in line]
    assert len(level) == 2
    assert all(line.startswith("=3. ") for line in level), level
    assert scores["lines"][4].startswith("5. ")
    # The paid places are settled by the tiebreak, so they are never shared.
    assert scores["lines"][0].startswith("1. ")
    assert scores["lines"][1].startswith("2. ")


def test_every_score_carries_the_season_standing(summary):
    """With thirteen managers a top three leaves ten people who cannot find
    themselves. The block that names everybody says where they stand."""
    scores = next(b for b in summary["blocks"] if b["heading"] == "Every score")
    soon_lee = next(line for line in scores["lines"] if line.startswith("1. Soon Lee"))
    assert "· 1st overall on 141" in soon_lee


def test_a_hit_is_stated_in_points():
    def eight_point_hit(calendar):
        calendar[2].scores["jack"].hits = 8

    payload = _payload_with(eight_point_hit)
    scores = next(b for b in payload["summary"]["blocks"] if b["heading"] == "Every score")
    jack = next(line for line in scores["lines"] if "Jack" in line)
    assert "[8-pt hit]" in jack


def test_the_month_says_how_its_stake_was_built(summary):
    """A two-gameweek month charges RM10, which is also the weekly stake. Two
    lines saying "owe RM10 each" read as one charge said twice."""
    month = next(b for b in summary["blocks"] if b["heading"] == "August — settled")
    assert "owe RM10 each — RM5 a gameweek, 2 gameweeks in the month. Pot RM80." in "\n".join(month["lines"])
    week = "\n".join(summary["blocks"][0]["lines"])
    assert "a gameweek" not in week


def test_the_week_is_given_its_context_in_points_only(summary):
    week = "\n".join(summary["blocks"][0]["lines"])
    average = sum(GW2_POINTS.values()) / len(GW2_POINTS)
    assert f"League average {average:.1f}." in week or f"League average {average:.0f}." in week
    # GW2's 69 is below GW1's 74, so it is not the season high and must not claim to be.
    assert "highest score" not in week


def test_the_season_high_is_named_only_once_there_is_a_season():
    tall = dict(GW2_POINTS, soonlee=120)
    payload = payload_for({1: GW1_POINTS, 2: tall})
    assert "120 is the highest score of the season so far." in "\n".join(
        payload["summary"]["blocks"][0]["lines"]
    )
    # After GW1 every score is the highest so far, and saying so is noise.
    one = payload_for(
        {1: GW1_POINTS},
        current={"season": "2026/27", "gameweek": 1, "next_gw": 2, "state": "final"},
        settled_dates={1: "2026-08-24"},
    )
    assert "highest score" not in "\n".join(one["summary"]["blocks"][0]["lines"])


def test_the_season_block_names_the_movers_and_nothing_twice(summary):
    season = next(b for b in summary["blocks"] if b["heading"] == "Season so far")
    body = "\n".join(season["lines"])
    assert "leads by" not in body          # said already by "(N behind)" above it
    assert body.count("Movers:") == 1
    assert "up " in body and " to " in body
    # After GW1 there is no before, so there are no movers.
    one = payload_for(
        {1: GW1_POINTS},
        current={"season": "2026/27", "gameweek": 1, "next_gw": 2, "state": "final"},
        settled_dates={1: "2026-08-24"},
    )
    first = next(b for b in one["summary"]["blocks"] if b["heading"] == "Season so far")
    assert "Movers" not in "\n".join(first["lines"])


def test_the_running_month_carries_the_gaps_and_says_not_settled_once():
    mid = payload_for(
        {1: GW1_POINTS},
        current={"season": "2026/27", "gameweek": 1, "next_gw": 2, "state": "final"},
        settled_dates={1: "2026-08-24"},
    )["summary"]
    running = next(b for b in mid["blocks"] if b["heading"] == "August — running")
    body = "\n".join(running["lines"])
    assert "2. " in body and "behind)" in body
    assert "would, not is" not in body
    assert body.count("settle") == 1


def test_a_correction_posted_since_the_last_gameweek_gets_its_own_block():
    """§3.9.4 — the message is the record that circulates. An adjusting entry
    that only ever appeared in a statement nobody opens is the November error
    that surfaces in May."""
    calendar = full_season(
        {1: GW1_POINTS, 2: GW2_POINTS}, managers=MANAGERS, months={1: "AUG", 2: "AUG"}
    )
    settlement = settle(MANAGERS, calendar, BUCKETS, expected_gameweeks=38)
    corrections = [
        {"type": "correction", "date": "2026-08-20", "affects_gw": 1,
         "reason": "posted before either gameweek — already announced",
         "adjustments": {"jack": 100, "sam": -100}},
        {"type": "correction", "date": "2026-08-26", "affects_gw": 1,
         "reason": "GW1 tiebreak applied cards before goals conceded",
         "adjustments": {"jack": 3500, "sam": -3500}},
    ]
    kwargs = dict(
        generated_at="2026-09-01T06:00:00Z", league_name="SuperF", league_id=310479,
        managers=MANAGERS, teams=TEAMS, events=EVENTS, breaks=[], month_buckets=BUCKETS,
        fixtures_by_gw={}, pl_table=[], gameweeks=calendar, settlement=settlement,
        current={"season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final"},
        settled_dates=SETTLED_DATES,
    )
    payload = build_payload(corrections=corrections, **kwargs)
    block = next(b for b in payload["summary"]["blocks"] if b["heading"] == "Corrections")
    assert len(block["lines"]) == 1, block
    line = block["lines"][0]
    assert line.startswith("Adjusting entry to GW1, posted Wed 26 Aug: ")
    assert "GW1 tiebreak applied cards before goals conceded. Jack +RM35, Sam −RM35." in line
    assert "already announced" not in line
    assert block["heading"].upper() in payload["summary"]["text"]
    # The vocabulary guard holds over the new block too.
    for forbidden in (" won ", " paid ", "collected"):
        assert forbidden not in payload["summary"]["text"].lower()
    # No corrections, no block — the card is not padded with an empty heading.
    clean = build_payload(**kwargs)
    assert "Corrections" not in [b["heading"] for b in clean["summary"]["blocks"]]
