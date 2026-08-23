"""Spec §12.2 Layer 2 — Claude ranks, frames and explains.

Code produces every number Claude is allowed to quote. That is not a style
preference: a model asked to estimate points produces confident, unfalsifiable
nonsense, and the whole point of §12.3's scorecard is that the call can be
marked right or wrong afterwards.

So the split is enforced, not requested:

* the call must name managers that exist;
* the swing player must come from the shortlist the projection built;
* every numeric token in the reasoning must appear in the allowed set derived
  from Layer 1. A violation is retried once with the offending figures named,
  and then falls back to a deterministic summary rather than publishing a
  number nobody can trace.

The model identifier is never hardcoded. It comes from ``ANTHROPIC_MODEL`` and
whatever the API actually answered with is what lands in ``prediction.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Mapping, Sequence

log = logging.getLogger(__name__)

MAX_WORDS = 120
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# `max_tokens` caps thinking *plus* the response, and thinking is on by default
# on the current models. The call itself is tiny — two manager ids, a swing
# player and 120 words — but sizing the budget around the answer would let a
# long think truncate the tool call, and a truncated call is indistinguishable
# from a bad one: it falls back silently and the projection ranking publishes
# itself as the call, every week, with nothing in the file saying so.
MAX_TOKENS = 8000

CALL_TOOL = {
    "name": "publish_call",
    "description": "Publish the gameweek call, its reasoning and the swing player.",
    "input_schema": {
        "type": "object",
        "properties": {
            "first": {
                "type": "object",
                "properties": {
                    "manager": {"type": "string", "description": "manager id"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["manager", "confidence"],
            },
            "second": {
                "type": "object",
                "properties": {
                    "manager": {"type": "string", "description": "manager id"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["manager", "confidence"],
            },
            "agrees_with_projection": {
                "type": "boolean",
                "description": "false if the call departs from the xP ranking, which is allowed and interesting, but must be stated",
            },
            "swing_player": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "owned_by": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
                "required": ["name", "owned_by", "why"],
            },
            "reasoning": {
                "type": "string",
                "description": f"<= {MAX_WORDS} words of plain prose, not bullets, naming actual managers and clubs",
            },
        },
        "required": ["first", "second", "agrees_with_projection", "swing_player", "reasoning"],
    },
}

CHIP_NAMES = {
    "bboost": "bench boost — all fifteen players score, not eleven",
    "3xc": "triple captain — the captain counts three times, not twice",
    "freehit": "free hit — a one-week squad, reverting next gameweek",
    "wildcard": "wildcard — a rebuilt squad with no transfer hits",
    "manager": "assistant manager",
}


def chip_label(code: str | None) -> str:
    """FPL's chip codes are not readable; never invent one that is not played."""
    if not code:
        return ""
    return CHIP_NAMES.get(code, code)


MID_ROUND_SYSTEM = """
This gameweek is already in progress, so you are not calling it from scratch —
you are calling who finishes 1st and 2nd *from here*.

Each manager's xP is two different things added together: `banked`, which is
points already on the board and cannot change, and `remaining`, which is the
projection over their players whose matches have not kicked off yet. A lead
made of banked points is worth more than the same lead made of projection. Say
which kind of lead you are looking at.

The swing shortlist only contains players who are still to play, because nobody
else can swing anything now. Do not describe a finished match as if it were
still to come.
"""

SYSTEM = """You are calling the weekly winner for an eight-person Malaysian FPL money league.

The projections you are given are produced by code. They are the only numbers
you may quote. Do not estimate, average, or invent any figure — not points, not
percentages, not prices. If you want to say something is likely, say it in
words.

Your job is the judgment a formula cannot do:
- the call: 1st and 2nd, each with a confidence band
- why, in the league's own terms
- structural risk: concentration on one club, one fixture, or one kickoff
- captaincy divergence: who went differential and what it costs if it fails
- the swing factor: the single player most likely to decide the pot
- chips: who played one, and what it does to the comparison

Pick the swing player from the shortlist provided; do not name anyone else.
Never say a manager played a chip unless the projections say so.

Tone: confident and specific. Never hedge into uselessness ("it could be
anyone") and never sound falsely certain — that is what the confidence bands
are for, and `low` is a legitimate and useful answer. If your call disagrees
with the projection ranking, that is allowed and interesting, but say so
explicitly and set agrees_with_projection to false.

The reasoning must be prose a friend would read, at most 120 words."""


def allowed_numbers(
    projections: Sequence[Mapping],
    gw: int,
    fixtures: Sequence[Mapping],
    manager_count: int,
) -> set[str]:
    """Every figure the model is permitted to put in the reasoning."""
    allowed: set[str] = set()

    def add(value) -> None:
        if value is None:
            return
        number = float(value)
        allowed.add(f"{number:.1f}")
        if number == int(number):
            allowed.add(str(int(number)))

    for projection in projections:
        add(projection["xp"])
        add(projection["captain_xp"])
        add(projection["hits"])
        add(projection["concentration"]["players"])
        # Mid-round only: the two halves of xp are quotable in their own right,
        # and are the figures worth talking about when half the round is done.
        add(projection.get("banked"))
        add(projection.get("remaining"))
        # A bench boost puts fifteen on the field; the model has to be able to
        # say so. Squad sizes at or below N are already allowed by the loop
        # over places below.
        add(projection.get("squad"))
    add(gw)
    for place in range(0, manager_count + 1):
        add(place)
    for fixture in fixtures:
        for key in ("team_h_difficulty", "team_a_difficulty"):
            add(fixture.get(key))
        kickoff = fixture.get("kickoff_time") or ""
        if len(kickoff) >= 16:  # allow the clock times people actually discuss
            add(int(kickoff[11:13]))
            add(int(kickoff[14:16]))
            for shifted in (int(kickoff[11:13]) + 8, int(kickoff[11:13]) + 8 - 24):
                add(shifted % 24)
    return allowed


def find_violations(reasoning: str, allowed: set[str]) -> list[str]:
    bad = []
    for token in NUMBER_PATTERN.findall(reasoning):
        normalised = f"{float(token):.1f}"
        if token not in allowed and normalised not in allowed:
            bad.append(token)
    return sorted(set(bad))


def build_prompt(
    *,
    gw: int,
    deadline: str,
    projections: Sequence[Mapping],
    squads: Mapping[str, Sequence[Mapping]],
    fixtures: Sequence[Mapping],
    teams: Mapping[int, Mapping],
    managers: Sequence[Mapping],
    swing_shortlist: Sequence[Mapping],
    record: Mapping,
    mid_round: Mapping | None = None,
) -> str:
    names = {m["id"]: m["display_name"] for m in managers}
    lines = [
        f"Gameweek {gw}. Deadline {deadline}. League of {len(managers)} managers.",
        "Weekly pot pays the top two, 70/30 — second place is worth having.",
    ]
    if mid_round:
        lines += [
            "",
            f"THIS GAMEWEEK IS IN PROGRESS. As of {mid_round['as_of']}, "
            f"{mid_round['played']} of {mid_round['total']} matches have kicked off and "
            f"{mid_round['remaining']} have not. You are calling the finish from here.",
        ]
    lines += [
        "",
        "PROJECTIONS (from code — these are the only numbers you may quote):",
    ]
    for projection in sorted(projections, key=lambda p: -p["xp"]):
        manager = names.get(projection["manager"], projection["manager"])
        split = (
            f" (banked {projection['banked']} + remaining {projection['remaining']})"
            if "banked" in projection else ""
        )
        chip = (
            f", CHIP: {chip_label(projection['chip'])}, "
            f"{projection['squad']} players counting"
            if projection.get("chip") else ""
        )
        lines.append(
            f"  {manager} (id: {projection['manager']}) — xP {projection['xp']}{split}, "
            f"captain {projection['captain']} xP {projection['captain_xp']}, "
            f"hits {projection['hits']}, "
            f"{projection['concentration']['players']} x {projection['concentration']['club']}"
            f"{chip}"
        )

    played = [p for p in projections if p.get("chip")]
    if played:
        lines += [
            "",
            "CHIPS PLAYED THIS GAMEWEEK — this is structural, not a detail. A "
            "manager on a bench boost is scoring from a larger squad than "
            "everyone else, so their total is not comparable to an unboosted "
            "one player for player, and they have more players left to play:",
        ]
        for projection in played:
            lines.append(
                f"  {names.get(projection['manager'], projection['manager'])} — "
                f"{chip_label(projection['chip'])} ({projection['squad']} players)"
            )

    lines += ["", "SQUADS (every player who counts, highest projected first — "
              "fifteen for a bench boost, eleven otherwise):"]
    for manager_id, players in squads.items():
        manager = names.get(manager_id, manager_id)
        listed = ", ".join(
            f"{p['name']} ({teams.get(p['team'], {}).get('short_name', '?')})" for p in players
        )
        lines.append(f"  {manager}: {listed}")

    heading = ("FIXTURES STILL TO KICK OFF" if mid_round else "FIXTURES this gameweek")
    lines += ["", heading + " (kickoffs in UTC; Malaysia is UTC+8):"]
    for fixture in fixtures:
        home = teams.get(int(fixture["team_h"]), {}).get("short_name", "?")
        away = teams.get(int(fixture["team_a"]), {}).get("short_name", "?")
        lines.append(
            f"  {home} v {away} — {fixture.get('kickoff_time', 'TBC')}, "
            f"difficulty {fixture.get('team_h_difficulty')}/{fixture.get('team_a_difficulty')}"
        )

    lines += ["", "SWING SHORTLIST (pick one; owned by more than one manager"
              + ("; all still to play):" if mid_round else "):")]
    for candidate in swing_shortlist:
        owners = ", ".join(names.get(o, o) for o in candidate["owned_by"])
        captained = ", ".join(names.get(o, o) for o in candidate["captained_by"]) or "nobody"
        lines.append(
            f"  {candidate['name']} — owned by {owners}; captained by {captained}"
        )

    if record.get("played"):
        lines += [
            "",
            f"Your record so far: {record['exact']} exact of {record['played']}, "
            f"podium {record['podium']}, pair {record['pair']}.",
        ]

    lines += [
        "",
        ("Call who finishes 1st and 2nd from here."
         if mid_round else "Call 1st and 2nd.")
        + " Use manager ids exactly as given in parentheses.",
    ]
    return "\n".join(lines)


def deterministic_fallback(
    projections: Sequence[Mapping],
    names: Mapping[str, str],
    swing_shortlist: Sequence[Mapping] = (),
) -> dict:
    """Used when the model is unavailable or will not stay inside its numbers.

    The swing player comes off the shortlist when there is one, because the
    shortlist is the only thing that knows who is still to play — mid-round,
    the projected leader's captain may have finished hours ago.
    """
    ordered = sorted(projections, key=lambda p: -p["xp"])
    first, second = ordered[0], ordered[1]
    top_swing = swing_shortlist[0] if swing_shortlist else None
    swing = {
        "name": top_swing["name"],
        "owned_by": list(top_swing["owned_by"]),
        "why": "the most shared player still to play",
    } if top_swing else {
        "name": first["captain"],
        "owned_by": [first["manager"]],
        "why": "captained by the projected leader",
    }
    return {
        "first": {"manager": first["manager"], "confidence": "low"},
        "second": {"manager": second["manager"], "confidence": "low"},
        "agrees_with_projection": True,
        "swing_player": swing,
        "reasoning": (
            f"No model call this week, so this is the projection ranking as it stands: "
            f"{names.get(first['manager'], first['manager'])} ahead of "
            f"{names.get(second['manager'], second['manager'])}. "
            f"Confidence is low by default when nothing has looked at the fixtures."
        ),
        "_fallback": True,
    }


def request_call(
    *,
    prompt: str,
    allowed: set[str],
    manager_ids: set[str],
    swing_names: set[str],
    projections: Sequence[Mapping],
    names: Mapping[str, str],
    system_extra: str = "",
    swing_shortlist: Sequence[Mapping] = (),
) -> tuple[dict, str | None]:
    """Return ``(call, model_id)``. Falls back deterministically on any failure."""
    def fallback() -> tuple[dict, None]:
        return deterministic_fallback(projections, names, swing_shortlist), None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — publishing the projection ranking")
        return fallback()
    if not model:
        log.warning("ANTHROPIC_MODEL not set — publishing the projection ranking")
        return fallback()

    try:
        import anthropic  # type: ignore
    except ImportError:
        log.warning("anthropic SDK not installed — publishing the projection ranking")
        return fallback()

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM + system_extra,
                tools=[CALL_TOOL],
                tool_choice={"type": "tool", "name": "publish_call"},
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run on the model
            log.warning("model call failed: %s", exc)
            return fallback()

        stop = getattr(response, "stop_reason", None)
        block = next((b for b in response.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            # A refusal returns HTTP 200 with no tool call, as does a response
            # that ran out of budget mid-call. Both look like success to code
            # that indexes content[0], so say which one it was.
            log.warning("model returned no tool call (stop_reason=%s)", stop)
            return fallback()

        call = dict(block.input)
        problems = validate(call, allowed, manager_ids, swing_names)
        if not problems:
            return call, getattr(response, "model", model)

        log.warning("call rejected (attempt %d): %s", attempt + 1, "; ".join(problems))
        if attempt == 0:
            # The rejection has to come back as a tool_result, not as loose
            # text: a tool_use block must be answered by a tool_result in the
            # very next user turn, and the assistant turn is replayed verbatim
            # because it may carry thinking blocks that cannot be reconstructed.
            messages += [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "is_error": True,
                            "content": (
                                "That call was rejected: "
                                + "; ".join(problems)
                                + ". Every number in the reasoning must be one the "
                                "projections gave you. Rewrite it, using words instead "
                                "of any figure you cannot point at. Call the tool again."
                            ),
                        }
                    ],
                },
            ]

    return fallback()


def validate(
    call: Mapping, allowed: set[str], manager_ids: set[str], swing_names: set[str]
) -> list[str]:
    problems: list[str] = []

    for slot in ("first", "second"):
        manager = (call.get(slot) or {}).get("manager")
        if manager not in manager_ids:
            problems.append(f"{slot} names an unknown manager {manager!r}")
    if (call.get("first") or {}).get("manager") == (call.get("second") or {}).get("manager"):
        problems.append("first and second are the same manager")

    swing = call.get("swing_player") or {}
    if swing_names and swing.get("name") not in swing_names:
        problems.append(f"swing player {swing.get('name')!r} is not on the shortlist")
    for owner in swing.get("owned_by", []):
        if owner not in manager_ids:
            problems.append(f"swing player owned_by names unknown manager {owner!r}")

    reasoning = call.get("reasoning", "") or ""
    words = len(reasoning.split())
    if words > MAX_WORDS:
        problems.append(f"reasoning is {words} words, limit is {MAX_WORDS}")
    violations = find_violations(reasoning, allowed)
    if violations:
        problems.append("reasoning quotes numbers the projection never produced: "
                        + ", ".join(violations))
    return problems
