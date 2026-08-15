"""Callout copy for the month-pot card (§7.2C) and the month teaser.

The highest-engagement component on the site converts a table into a live bet,
so the callout has to say what is actually at stake in plain words. Every number
in here is passed in — nothing is invented, and nothing is rounded for effect.
"""

from __future__ import annotations

MONTH_NAMES = {
    "AUG": "August", "SEP": "September", "OCT": "October", "NOV": "November",
    "DEC": "December", "JAN": "January", "FEB": "February", "MAR": "March",
    "APR": "April", "MAY": "May",
}


def _rm(value) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"RM{value:.2f}"
    return f"RM{int(value)}"


def month_name(month: str) -> str:
    return MONTH_NAMES.get(month, month)


def settled_month_callout(
    month: str, first: str, first_points: int, first_net, second: str,
    second_points: int, second_net, stake, others: int, gameweeks: int,
) -> str:
    """Copy for a month whose last gameweek has gone Final."""
    margin = first_points - second_points
    lead = (
        f"{first} take the {_rm(first_net)} with {first_points} points"
        if margin
        else f"{first} take the {_rm(first_net)} on {first_points} points"
    )
    gap = f" — {margin} clear of {second}" if margin else f" — level with {second} on points, split by the tiebreak"
    return (
        f"{month_name(month)} settled. {lead}{gap}, and {second} takes {_rm(second_net)} "
        f"with {second_points}. The other {others} pay {_rm(stake)} each. "
        f"{gameweeks} gameweek{'s' if gameweeks != 1 else ''} in the bucket."
    )


def live_month_callout(
    month: str, leader: str, leader_points: int, first_net, you: str,
    you_points: int, remaining: int,
) -> str:
    """Copy while a month is still running — the 'live bet' framing of §7.2C."""
    gap = leader_points - you_points
    tail = (
        f"{remaining} gameweek{'s' if remaining != 1 else ''} left in {month_name(month)}."
        if remaining
        else f"Last gameweek of {month_name(month)}."
    )
    if you == leader:
        return (
            f"You lead the {month_name(month)} pot on {you_points} points and the "
            f"{_rm(first_net)} is yours if it holds. {tail}"
        )
    if gap <= 0:
        return (
            f"You are level with {leader} on {you_points} for the {_rm(first_net)}. {tail}"
        )
    return (
        f"You need {gap} more than {leader} to take the {_rm(first_net)} — "
        f"{you_points} against {leader_points}. {tail}"
    )


def month_opens_note(month: str, gameweeks: int, opens_gw: int, stake, pot) -> str:
    """Copy for a bucket that has not started yet."""
    return (
        f"{month_name(month)} opens with GW{opens_gw} — {_rm(stake)} each, "
        f"{_rm(pot)} in the pot, {gameweeks} gameweek"
        f"{'s' if gameweeks != 1 else ''} to decide it."
    )


def month_in_progress_note(month: str, played: int, gameweeks: int, pot) -> str:
    """Copy for a bucket mid-flight, shown under the last settled month's card."""
    return (
        f"{month_name(month)} is {played} of {gameweeks} gameweeks in, "
        f"{_rm(pot)} in the pot. Nothing settles until the last one is final."
    )
