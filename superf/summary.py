"""The gameweek summary — one message, ready to paste into the group chat.

Nobody opens a dashboard on a Sunday night; they read WhatsApp. So the thing
that actually circulates after a gameweek settles is a block of text, and if
that text is retyped by hand it will eventually disagree with the ledger it
claims to report — which is the one failure this repo exists to prevent. This
composes it from the assembled payload, so the message and the book are the
same numbers by construction.

Reading the payload rather than the ledger is deliberate. The summary must say
what the page says; if it recomputed from the settlement it could be right
while the page was wrong, and nobody would find out until May. So the only
aggregation here is over already-settled points (a month carried to date), and
never over money.

Money vocabulary is §3.9.1's, exactly. The weekly and monthly pots are
*accrued* — "is owed", "owe" — the season pot is *projected* and stays out of
the message entirely, and nothing is ever *won*, *paid* or *collected*.

There is no standing "nothing is paid yet" line. It was on every message and
every card on the site, and being told the same thing weekly is how a caveat
stops being read. The vocabulary carries it instead: "is owed" and "owe" are
what an accrual sounds like.

Runnable: ``python -m superf.summary [docs/data.json]`` prints the text, so a
gameweek can be sent out from a terminal without opening the site.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Sequence

from . import copy as copytext
from .config import TZ_OFFSET_HOURS
from .money import sen_to_rm

# No standing reminder that nothing is paid yet. The vocabulary already carries
# it — "is owed" and "owe" are accruals, and the message never says won, paid or
# collected — and repeating the disclaimer every week is what got it cut from the
# site. What is left in the footer is the one thing the message is for: when the
# next deadline is.
FOOTER = ""

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _date_short(value: str) -> str:
    """``2026-08-31`` → ``Mon 31 Aug``. Empty stays empty."""
    if not value:
        return ""
    try:
        moment = datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return value
    return f"{DAYS[moment.weekday()]} {moment.day} {MONTHS_SHORT[moment.month - 1]}"


def _deadline_myt(value: str) -> str:
    """A UTC deadline as the league reads it: Malaysia time, ``Fri 5 Sep, 01:30``.

    Every deadline on this site is stated in MYT first (§9.4). A summary that
    quoted UTC would be the one place on the page where the next deadline is
    eight hours out, which is exactly the mistake that costs somebody a team.
    """
    if not value:
        return ""
    try:
        moment = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return value
    local = (moment + timedelta(hours=TZ_OFFSET_HOURS)).replace(tzinfo=None)
    return (
        f"{DAYS[local.weekday()]} {local.day} {MONTHS_SHORT[local.month - 1]}, "
        f"{local:%H:%M}"
    )


def _names(managers: Sequence[Mapping]) -> dict[str, str]:
    return {
        m["id"]: (m.get("short") or m.get("display_name") or m["id"]) for m in managers
    }


def _join(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _week_block(gameweek: Mapping, names: Mapping[str, str], weekly_stake) -> dict:
    """The gameweek itself: who took the pot, then every score under it."""
    gw = gameweek["gw"]
    scores = gameweek["scores"]
    active = {m: s for m, s in scores.items() if s.get("active")}
    field = len(active)
    winners = [m for m in gameweek["winners"] if m in active]
    runners_up = [m for m in gameweek["runners_up"] if m in active]

    lines: list[str] = []
    if winners:
        points = active[winners[0]]["points"]
        won = copytext.rm(sen_to_rm(gameweek["winner_net"] or 0))
        if len(winners) > 1:
            # A level-5 tie takes both paid shares between them, so there is no
            # second place to name and each winner's share is smaller than the
            # advertised one. Say the share, not the headline number.
            lines.append(
                f"{_join([names[m] for m in winners])} tie GW{gw} on {points} pts "
                f"and are owed {won} each — dead level after all four tiebreaks."
            )
        else:
            lines.append(
                f"{names[winners[0]]} takes GW{gw} on {points} pts and is owed {won}."
            )
    if runners_up:
        points = active[runners_up[0]]["points"]
        second = copytext.rm(sen_to_rm(gameweek["runner_up_net"] or 0))
        lines.append(
            f"{_join([names[m] for m in runners_up])} "
            f"{'are' if len(runners_up) > 1 else 'is'} second on {points} and "
            f"{'are' if len(runners_up) > 1 else 'is'} owed {second}."
        )

    paid = len(winners) + len(runners_up)
    if gameweek.get("pot"):
        lines.append(
            f"The other {_plural(max(field - paid, 0), 'manager')} owe "
            f"{copytext.rm(weekly_stake)} each. Pot {copytext.rm(gameweek['pot'])}."
        )

    tiebreak = gameweek.get("tiebreak")
    if tiebreak and tiebreak.get("text"):
        lines.append(f"Split by the tiebreak: {tiebreak['text']}.")
    # §11.4 — if confirmed bonus moved the pot, the message that goes out is
    # where it has to be said. The people who watched the provisional table all
    # evening are the people reading this.
    change = gameweek.get("bonus_change")
    if change:
        lines.append(
            f"Confirmed bonus moved this one: {names.get(change['from'], change['from'])} "
            f"led at full time, {names.get(change['to'], change['to'])} took it."
        )

    if gameweek.get("note"):
        lines.append(f"{gameweek['note'].capitalize()} — read the scores with that in mind.")

    return {"heading": f"GW{gw}", "lines": lines}


def _table_block(gameweek: Mapping, names: Mapping[str, str], ledger_row: Mapping) -> dict:
    """Every manager's gameweek, in order, with what it did to their book."""
    scores = gameweek["scores"]
    active = [m for m, s in scores.items() if s.get("active")]
    winners = list(gameweek["winners"])
    runners_up = list(gameweek["runners_up"])
    order = sorted(
        active,
        key=lambda m: (
            0 if m in winners else 1 if m in runners_up else 2,
            -(scores[m]["points"] or 0),
            names.get(m, m),
        ),
    )
    lines = []
    for place, manager in enumerate(order, start=1):
        score = scores[manager]
        amount = ledger_row.get(manager)
        money = f" ({copytext.signed_rm(sen_to_rm(amount))})" if amount is not None else ""
        marks = []
        if score.get("chip"):
            marks.append(score["chip"])
        if score.get("hits"):
            marks.append(f"-{score['hits']} hit")
        if score.get("did_not_set"):
            marks.append("no team set")
        tail = f" [{', '.join(marks)}]" if marks else ""
        lines.append(
            f"{place}. {names.get(manager, manager)} — {score['points']} pts{money}{tail}"
        )
    return {"heading": "Every score", "lines": lines}


def _debits_line(ledger: Mapping[str, int], pot) -> str:
    """Who paid, read off the ledger — never "everyone else owes the stake".

    The stake is per gameweek a manager was active for (§3.8.6), so somebody
    who joined for the last gameweek of a three-gameweek bucket owes a third of
    what the rest do. One sentence when they all match, a range when they do
    not; the number is never invented.
    """
    debits = sorted({-v for v in ledger.values() if v < 0})
    payers = sum(1 for v in ledger.values() if v < 0)
    if not debits:
        return f"Pot {copytext.rm(pot)}."
    if len(debits) == 1:
        return (
            f"The other {_plural(payers, 'manager')} owe "
            f"{copytext.rm(sen_to_rm(debits[0]))} each. Pot {copytext.rm(pot)}."
        )
    return (
        f"The other {_plural(payers, 'manager')} owe their stake — "
        f"{copytext.rm(sen_to_rm(debits[0]))} to {copytext.rm(sen_to_rm(debits[-1]))}, "
        f"by gameweeks played. Pot {copytext.rm(pot)}."
    )


def _settled_month_block(month: Mapping, names: Mapping[str, str]) -> dict:
    """A month whose last gameweek has just gone Final — the monthly winners."""
    totals = month["totals"]
    ledger = month.get("ledger") or {}
    winners = list(month.get("winners") or month["order"][:1])
    runners_up = list(month.get("runners_up") or [])
    label = copytext.month_name(month["month"])
    lines = [
        f"{label} is settled over GW{month['gameweeks'][0]}–GW{month['gameweeks'][-1]}."
    ]
    if winners:
        points = totals.get(winners[0], 0)
        owed = copytext.rm(sen_to_rm(ledger.get(winners[0], 0)))
        if len(winners) > 1:
            # Both paid shares split between them, so there is no second place
            # and each takes less than the headline share. Say the share.
            lines.append(
                f"{_join([names.get(m, m) for m in winners])} tie the month on "
                f"{points} pts and are owed {owed} each — dead level after all four tiebreaks."
            )
        else:
            lines.append(
                f"{names.get(winners[0], winners[0])} takes the month on {points} pts "
                f"and is owed {owed}."
            )
    if runners_up:
        points = totals.get(runners_up[0], 0)
        owed = copytext.rm(sen_to_rm(ledger.get(runners_up[0], 0)))
        lines.append(
            f"{_join([names.get(m, m) for m in runners_up])} "
            f"{'are' if len(runners_up) > 1 else 'is'} second on {points} and "
            f"{'are' if len(runners_up) > 1 else 'is'} owed {owed}."
        )
    lines.append(_debits_line(ledger, month["pot"]))
    return {"heading": f"{label} — settled", "lines": lines}


def _running_month_block(
    month: Mapping,
    played: Sequence[int],
    totals: Mapping[str, int],
    names: Mapping[str, str],
) -> dict:
    """A month still running: a standing, and nothing owed.

    The monthly pot is the one people misread, because a table that looks like
    the settled one reads as settled. So this block never states an accrual —
    it names what is at stake and says, in the same breath, that it is not in
    the book yet.
    """
    remaining = month["gameweeks"] - len(played)
    order = sorted(totals, key=lambda m: (-totals[m], names.get(m, m)))
    lines = [
        f"{copytext.month_name(month['month'])} is {len(played)} of "
        f"{month['gameweeks']} gameweeks in — {copytext.rm(month['pot'])} in the pot, "
        f"{_plural(remaining, 'gameweek')} left."
    ]
    for place, manager in enumerate(order[:3], start=1):
        lines.append(
            f"{place}. {names.get(manager, manager)} — {totals[manager]} pts"
        )
    if order:
        nets = month.get("net") or []
        lines.append(
            f"{names.get(order[0], order[0])} would take "
            f"{copytext.rm(nets[0] if nets else 0)} if it held — would, not is. "
            f"Nothing settles until GW{month['last_gw']} is final."
        )
    return {
        "heading": f"{copytext.month_name(month['month'])} — running",
        "lines": lines,
    }


def _season_block(
    rank: Sequence[str],
    totals: Mapping[str, int],
    behind: Mapping[str, int],
    names: Mapping[str, str],
    through_gw: int,
) -> dict:
    lines = []
    for place, manager in enumerate(rank[:3], start=1):
        gap = behind.get(manager, 0)
        tail = "" if place == 1 else f" ({gap} behind)"
        lines.append(f"{place}. {names.get(manager, manager)} — {totals.get(manager, 0)} pts{tail}")
    if len(rank) > 1:
        lines.append(
            f"{names.get(rank[0], rank[0])} leads by "
            f"{behind.get(rank[1], 0)} after {_plural(through_gw, 'gameweek')}."
        )
    return {"heading": "Season so far", "lines": lines}


def _month_to_date(
    gameweeks: Sequence[Mapping], bucket_gws: Sequence[int]
) -> tuple[list[int], dict[str, int]]:
    """Settled points inside one bucket. Points only — never money."""
    played: list[int] = []
    totals: dict[str, int] = {}
    for row in gameweeks:
        if row["gw"] not in bucket_gws:
            continue
        played.append(row["gw"])
        for manager, score in row["scores"].items():
            if score.get("active"):
                totals[manager] = totals.get(manager, 0) + (score["points"] or 0)
    return played, totals


def build(payload: Mapping) -> dict | None:
    """The summary block, or ``None`` while nothing has settled.

    Absent rather than empty: a message that says "GW0, nobody is owed
    anything" is worse than no message, and the view has a designed state for
    a book with nothing in it.
    """
    gameweeks = payload.get("gameweeks") or []
    if not gameweeks:
        return None

    managers = payload["managers"]
    names = _names(managers)
    last = gameweeks[-1]
    gw = last["gw"]
    league = payload["league"]["name"]
    settled_on = ""
    # The date this gameweek stopped moving, taken off any statement row that
    # cites it — the emitter has already written it there, so the summary and
    # the statement quote one date, not two.
    for row in payload["ledger"].values():
        for entry in row["statement"]:
            if entry.get("type") == "weekly" and entry.get("gw") == gw:
                settled_on = entry.get("date") or ""
                break
        if settled_on:
            break

    weekly_ledger = {
        manager: row["by_gameweek"][-1]
        for manager, row in payload["ledger"].items()
        if row["by_gameweek"]
    }

    blocks = [
        _week_block(last, names, payload["stakes"]["weekly"]["stake"]),
        _table_block(last, names, weekly_ledger),
    ]

    # The month, if this gameweek closed one; otherwise the month it sits in,
    # carried to date. "When time" is the whole point of the distinction: a
    # monthly winner exists only once the bucket's last gameweek is Final.
    settled_month = next(
        (m for m in payload.get("months") or [] if m["gameweeks"][-1] == gw), None
    )
    if settled_month:
        blocks.append(_settled_month_block(settled_month, names))
    else:
        bucket = next(
            (b for b in payload.get("month_buckets") or [] if gw in b["gameweeks"]), None
        )
        # `rules.months` carries every bucket's stake, pot and advertised net.
        # NOT `month_current`: that follows next_gw, and while the bucket's
        # last gameweek is live it already points a month ahead.
        terms = next(
            (m for m in (payload.get("rules") or {}).get("months") or []
             if bucket and m["month"] == bucket["month"]),
            None,
        )
        if bucket and terms:
            played, totals = _month_to_date(gameweeks, bucket["gameweeks"])
            if played:
                blocks.append(
                    _running_month_block(
                        {**terms, "last_gw": bucket["gameweeks"][-1]},
                        played,
                        totals,
                        names,
                    )
                )

    blocks.append(
        _season_block(
            payload["rank"],
            payload["totals"],
            payload["behind"],
            names,
            payload["settled"]["through_gw"],
        )
    )

    next_gw = payload["current"].get("next_gw")
    next_event = next((e for e in payload["events"] if e["gw"] == next_gw), None)
    tail_parts = [FOOTER] if FOOTER else []
    if next_event and next_gw != gw:
        tail_parts.append(
            f"GW{next_gw} deadline: {_deadline_myt(next_event['deadline'])} MYT."
        )
    tail = "\n".join(tail_parts)

    title = f"{league} · GW{gw} settled"
    if settled_on:
        title += f" · {_date_short(settled_on)}"

    body = "\n\n".join(
        block["heading"].upper() + "\n" + "\n".join(block["lines"])
        for block in blocks
        if block["lines"]
    )
    text = f"{title}\n\n{body}\n" + (f"\n{tail}\n" if tail else "")

    winners = [names.get(m, m) for m in last["winners"]]
    return {
        "gw": gw,
        "month": last["month"],
        "settled_on": settled_on,
        "title": title,
        # What a share sheet puts in a subject line, and what the card shows
        # before anybody expands it.
        "headline": (
            f"{_join(winners)} {'take' if len(winners) != 1 else 'takes'} GW{gw}"
            if winners
            else f"GW{gw} settled"
        ),
        "monthly_settled": bool(settled_month),
        "blocks": [b for b in blocks if b["lines"]],
        "footer": tail,
        "text": text,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import json
    import sys
    from pathlib import Path

    from .config import DATA_JSON

    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else DATA_JSON
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    block = payload.get("summary") or build(payload)
    if not block:
        print("Nothing has settled yet — no summary to send.", file=sys.stderr)
        return 1
    print(block["text"], end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
