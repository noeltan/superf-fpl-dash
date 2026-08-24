"""Derive the Premier League table from full-time fixtures.

§4: "the FPL API does not publish a Premier League table. It must be derived
from finished fixtures. That is real work, not a freebie."

"Finished" here means the final whistle: ``finished_provisional`` counts. What
FPL is still confirming after the whistle is bonus and auto-subs (§11.4), and
neither can change a match result — but the ``finished`` flag waits on them, so
a table gated on it goes missing for the hours (sometimes the whole night) a
round sits provisional, showing "opens with the first whistle" over ten played
matches. The ledger keeps its stricter gate; this table is display, not money.

Ordering is the league's own: points, then goal difference, then goals scored,
then club name. (The real competition separates a dead heat at the top with a
playoff; alphabetical is the conventional fallback for a derived table and is
at least deterministic.)
"""

from __future__ import annotations

from typing import Mapping, Sequence

from .fplcal import parse_utc

FORM_LENGTH = 5


def build_table(
    fixtures: Sequence[Mapping], teams: Mapping[int, Mapping]
) -> list[dict]:
    """One row per club, ordered, with a `form` tail of the last five results."""
    rows: dict[int, dict] = {
        team_id: {
            "team": team_id,
            "p": 0, "w": 0, "d": 0, "l": 0,
            "gf": 0, "ga": 0, "gd": 0, "pts": 0,
            "_form": [],
        }
        for team_id in teams
    }

    played = [
        f
        for f in fixtures
        if (f.get("finished") or f.get("finished_provisional"))
        and f.get("team_h_score") is not None
        and f.get("team_a_score") is not None
    ]
    played.sort(key=lambda f: (parse_utc(f["kickoff_time"]) if f.get("kickoff_time") else None) or 0)

    for fixture in played:
        home, away = fixture["team_h"], fixture["team_a"]
        if home not in rows or away not in rows:
            continue
        hs, as_ = int(fixture["team_h_score"]), int(fixture["team_a_score"])
        for team_id, scored, conceded in ((home, hs, as_), (away, as_, hs)):
            row = rows[team_id]
            row["p"] += 1
            row["gf"] += scored
            row["ga"] += conceded
            if scored > conceded:
                row["w"] += 1
                row["pts"] += 3
                row["_form"].append("W")
            elif scored == conceded:
                row["d"] += 1
                row["pts"] += 1
                row["_form"].append("D")
            else:
                row["l"] += 1
                row["_form"].append("L")

    if not played:
        # Pre-season is a real state that will be seen (§9.6). An empty table
        # lets the view show its designed empty state rather than twenty rows
        # of zeros that look like a bug.
        return []

    table = []
    for row in rows.values():
        row["gd"] = row["gf"] - row["ga"]
        row["form"] = row.pop("_form")[-FORM_LENGTH:]
        table.append(row)

    table.sort(
        key=lambda r: (-r["pts"], -r["gd"], -r["gf"], teams[r["team"]]["name"])
    )
    for position, row in enumerate(table, start=1):
        row["pos"] = position

    # Emit in the contract's key order so diffs stay readable in git.
    return [
        {
            "pos": r["pos"], "team": r["team"], "p": r["p"], "w": r["w"], "d": r["d"],
            "l": r["l"], "gf": r["gf"], "ga": r["ga"], "gd": r["gd"], "pts": r["pts"],
            "form": r["form"],
        }
        for r in table
    ]
