"""Callout copy for the month-pot card (§7.2C), and the words money is said in.

The highest-engagement component on the site converts a table into a live bet,
so the callout has to say what is actually at stake in plain words. Every number
in here is passed in — nothing is invented, and nothing is rounded for effect.

``rm``, ``signed_rm`` and ``ordinal`` live here rather than in one caller
because the page, the statement and the summary all have to render a ringgit
the same way. Two formatters for one currency is how RM4.5 and RM4.50 end up
on the same screen.
"""

from __future__ import annotations

MONTH_NAMES = {
    "AUG": "August", "SEP": "September", "OCT": "October", "NOV": "November",
    "DEC": "December", "JAN": "January", "FEB": "February", "MAR": "March",
    "APR": "April", "MAY": "May",
}


def rm(value) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"RM{value:.2f}"
    return f"RM{int(value)}"


def month_name(month: str) -> str:
    return MONTH_NAMES.get(month, month)


# FPL's chip codes are not words anybody says out loud. `docs/app.js` carries
# the same map for the page (`chipLabel`), and `tests/test_summary.py` reads
# that file and checks the two agree, because a message that says "bboost"
# next to a score the page calls BENCH BOOST is the kind of drift a reader
# notices and stops trusting. Unknown codes pass through in capitals rather
# than being swallowed: a chip nobody has heard of still inflated the score.
CHIP_LABELS = {
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
    "freehit": "Free Hit",
    "wildcard": "Wildcard",
    "manager": "Assistant Mgr",
}


def chip_label(code: str | None) -> str:
    if not code:
        return ""
    return CHIP_LABELS.get(code, code.upper())


def ordinal(place: int) -> str:
    if 10 <= place % 100 <= 20:
        return f"{place}th"
    return f"{place}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th') }"


def signed_rm(value) -> str:
    """A credit or a debit, with the true minus sign the view already uses.

    The summary is read on a phone with no column alignment to lean on, so the
    sign has to carry the meaning on its own — ``+RM81`` against ``-RM10``.
    """
    if value < 0:
        return "\u2212" + rm(-value)
    return "+" + rm(value)


def settled_month_callout(
    month: str, first: str, first_points: int, first_net, second: str,
    second_points: int, second_net, stake, others: int, gameweeks: int,
) -> str:
    """Copy for a month whose last gameweek has gone Final."""
    margin = first_points - second_points
    lead = (
        f"{first} take the {rm(first_net)} with {first_points} points"
        if margin
        else f"{first} take the {rm(first_net)} on {first_points} points"
    )
    gap = f" — {margin} clear of {second}" if margin else f" — level with {second} on points, split by the tiebreak"
    return (
        f"{month_name(month)} settled. {lead}{gap}, and {second} takes {rm(second_net)} "
        f"with {second_points}. The other {others} pay {rm(stake)} each. "
        f"{gameweeks} gameweek{'s' if gameweeks != 1 else ''} in the bucket."
    )


def month_opens_note(month: str, gameweeks: int, opens_gw: int, stake, pot) -> str:
    """Copy for a bucket that has not started yet."""
    return (
        f"{month_name(month)} opens with GW{opens_gw} — {rm(stake)} each, "
        f"{rm(pot)} in the pot, {gameweeks} gameweek"
        f"{'s' if gameweeks != 1 else ''} to decide it."
    )


def month_in_progress_note(month: str, played: int, gameweeks: int, pot) -> str:
    """Copy for a bucket mid-flight, shown under the last settled month's card."""
    return (
        f"{month_name(month)} is {played} of {gameweeks} gameweeks in, "
        f"{rm(pot)} in the pot. Nothing settles until the last one is final."
    )
