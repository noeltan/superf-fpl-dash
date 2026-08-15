"""Calendar derivation: month buckets, international breaks, gameweek state.

Named ``fplcal`` so it does not shadow the stdlib ``calendar`` module.

Nothing here is hardcoded from the spec's tables. §3.6's month shape and the
six breaks are *reproduced* from real deadlines and then asserted against
``EXPECTED_GAMEWEEKS`` — so a fixture reshuffle moves the buckets instead of
silently disagreeing with a table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .config import BREAK_MIN_DAYS, MYT, UTC


def parse_utc(value: str) -> datetime:
    """Parse an FPL ISO timestamp into an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso_z(moment: datetime) -> str:
    """Render an aware datetime as the ``...Z`` form the contract uses."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_of(deadline: datetime) -> str:
    """A gameweek belongs to the month its deadline falls in, Malaysia time (§3.6)."""
    return deadline.astimezone(MYT).strftime("%b").upper()


def build_events(raw_events: Sequence[Mapping]) -> list[dict]:
    """``[{gw, deadline, month}]`` in gameweek order."""
    events = []
    for event in sorted(raw_events, key=lambda e: e["id"]):
        deadline = parse_utc(event["deadline_time"])
        events.append(
            {"gw": int(event["id"]), "deadline": iso_z(deadline), "month": month_of(deadline)}
        )
    return events


def build_month_buckets(events: Sequence[Mapping]) -> list[dict]:
    """``[{month, gameweeks}]`` in calendar order, derived from deadlines."""
    buckets: dict[str, list[int]] = {}
    order: list[str] = []
    for event in events:
        month = event["month"]
        if month not in buckets:
            buckets[month] = []
            order.append(month)
        buckets[month].append(event["gw"])
    return [{"month": m, "gameweeks": buckets[m]} for m in order]


def build_breaks(events: Sequence[Mapping], min_days: int = BREAK_MIN_DAYS) -> list[dict]:
    """Gaps between consecutive deadlines of ``min_days`` or more."""
    breaks = []
    for previous, current in zip(events, events[1:]):
        before = parse_utc(previous["deadline"])
        after = parse_utc(current["deadline"])
        days = (after - before).days
        if days >= min_days:
            breaks.append(
                {
                    "after_gw": previous["gw"],
                    "next_gw": current["gw"],
                    "days": days,
                    "resumes": current["deadline"],
                }
            )
    return breaks


def gameweek_state(deadline: datetime, fixtures: Sequence[Mapping], now: datetime) -> str:
    """The five states of §11.1.

    Order matters. Provisional is tested before Live: when every fixture is
    ``finished_provisional`` but none is ``finished``, "any started and not
    finished" is also true, and calling that Live would let a pot settle on
    bonus that has not landed yet (§11.4 — this is the trap).
    """
    if now < deadline:
        return "upcoming"
    if not fixtures:
        return "locked"
    if all(f.get("finished") for f in fixtures):
        return "final"
    if all(f.get("finished_provisional") for f in fixtures):
        return "provisional"
    if any(f.get("started") for f in fixtures):
        return "live"
    return "locked"


def match_window(fixtures: Sequence[Mapping], tail_minutes: int = 150) -> tuple[datetime, datetime] | None:
    """§11.2 — open at first kickoff, close at last kickoff + 150 minutes.

    Outside this window the client does not poll. That is a few hundred
    requests a week rather than 40,000.
    """
    kickoffs = [parse_utc(f["kickoff_time"]) for f in fixtures if f.get("kickoff_time")]
    if not kickoffs:
        return None
    return min(kickoffs), max(kickoffs) + timedelta(minutes=tail_minutes)


def month_is_complete(bucket_gws: Iterable[int], final_gws: Iterable[int]) -> bool:
    """§3.8.3 — a monthly pot settles when the last gameweek in the bucket is Final."""
    final = set(final_gws)
    return all(gw in final for gw in bucket_gws)
