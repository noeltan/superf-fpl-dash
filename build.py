#!/usr/bin/env python3
"""Build ``docs/data.json`` — the settled ledger (spec §4 settled pipeline).

    GET /api/bootstrap-static/               -> 38 events, deadlines, teams, players
    GET /api/leagues-classic/310479/standings/  -> members + entry_ids
    GET /api/entry/{entry_id}/history/  x N  -> per-GW points, hits, transfers
    GET /api/fixtures/                       -> results -> derive the PL table
    compute weekly / monthly / season ledgers (§3)
    ASSERT sum of balances == 0              -> fail the build loudly if not
    emit data.json -> commit -> publish

Exit codes:
    0  wrote data.json (or nothing needed writing)
    1  a ledger did not balance, or an invariant broke — nothing was published
    2  the API could not be reached and nothing usable was cached — nothing
       was published, so the previous data.json stays live and ages into the
       §9.5 stale banner
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from superf import backup
from superf.config import (
    DATA_JSON,
    DOCS,
    EXPECTED_GAMEWEEKS,
    LEAGUE_ID,
    SEASON,
    load_manager_overrides,
    season_third_share_ok,
)
from superf.emit import build_payload
from superf.fpl import Fetcher, FetchError
from superf.fplcal import (
    build_breaks,
    build_events,
    build_month_buckets,
    gameweek_state,
    iso_z,
    parse_utc,
)
from superf.ledger import Gameweek, ManagerScore, settle
from superf.money import LedgerError
from superf.pltable import build_table
from superf.tiebreak import TiebreakStats, starting_xi, stats_for_xi

log = logging.getLogger("build")

# The prototype's state pill has no `locked` case, so the five states of §11.1
# are mapped down to the four it can render. The countdown already shows LOCKED
# on its own once the deadline passes.
VIEW_STATES = {"upcoming", "locked", "live", "provisional", "final"}


def slugify(first: str, last: str, taken: set[str]) -> str:
    """Fallback slug for a manager who is not yet pinned in managers.json."""
    base = re.sub(r"[^a-z]", "", f"{first}{last}".lower()) or "manager"
    slug = base[:12]
    suffix = 2
    while slug in taken:
        slug = f"{base[:11]}{suffix}"
        suffix += 1
    return slug


def collect_managers(standings: dict, fetcher: Fetcher) -> list[dict]:
    """Merge ``standings.results`` and ``new_entries`` (§Appendix).

    Pre-season every member sits under ``new_entries``; once the season starts
    they migrate to ``standings.results``. Both are read so the roster is right
    on either side of GW1.
    """
    overrides = load_manager_overrides()
    seen: dict[int, dict] = {}

    for row in standings.get("standings", {}).get("results", []):
        seen[int(row["entry"])] = {
            "entry_id": int(row["entry"]),
            "team_name": row.get("entry_name") or "",
            "first": row.get("player_name", ""),
            "last": "",
        }
    for row in standings.get("new_entries", {}).get("results", []):
        entry_id = int(row["entry"])
        seen.setdefault(
            entry_id,
            {
                "entry_id": entry_id,
                "team_name": row.get("entry_name") or "",
                "first": row.get("player_first_name", ""),
                "last": row.get("player_last_name", ""),
            },
        )

    # Display order follows managers.json, so the "You"/"Compare" pickers and the
    # pre-season standings keep a stable, intentional order. Anyone who joins
    # later lands at the end, by entry_id.
    pinned = {entry_id: i for i, entry_id in enumerate(overrides)}
    ordering = sorted(seen, key=lambda e: (pinned.get(e, len(pinned)), e))

    managers: list[dict] = []
    taken: set[str] = set()
    for entry_id in ordering:
        raw = seen[entry_id]
        override = overrides.get(entry_id, {})
        entry = fetcher.entry(entry_id) or {}
        first = raw["first"] or entry.get("player_first_name", "")
        last = raw["last"] or entry.get("player_last_name", "")
        slug = override.get("id") or slugify(first, last, taken)
        taken.add(slug)
        display = override.get("display_name") or f"{first} {last}".strip() or slug
        managers.append(
            {
                "id": slug,
                "display_name": display,
                "short": override.get("short") or display.split()[0],
                "team_name": raw["team_name"] or entry.get("name") or "",
                "entry_id": entry_id,
                "started_event": int(entry.get("started_event") or 1),
            }
        )
    return managers


def build_gameweeks(
    managers: list[dict],
    events: list[dict],
    fixtures_by_gw: dict[int, list[dict]],
    fetcher: Fetcher,
    now: datetime,
) -> tuple[dict[int, Gameweek], dict[int, str]]:
    """One :class:`Gameweek` per event, with scores and tiebreak stats."""
    histories: dict[int, dict] = {}
    for manager in managers:
        histories[manager["entry_id"]] = fetcher.entry_history(manager["entry_id"]) or {}

    states: dict[int, str] = {}
    gameweeks: dict[int, Gameweek] = {}

    for event in events:
        gw = event["gw"]
        deadline = parse_utc(event["deadline"])
        raw_fixtures = fixtures_by_gw.get(gw, [])
        state = gameweek_state(deadline, raw_fixtures, now)
        states[gw] = state
        is_final = state == "final"

        note = fixture_note(raw_fixtures)
        element_stats: dict[int, dict] = {}
        if is_final:
            live = fetcher.event_live(gw, final=True) or {}
            for element in live.get("elements", []):
                element_stats[int(element["id"])] = element.get("stats", {}) or {}

        scores: dict[str, ManagerScore] = {}
        for manager in managers:
            entry_id = manager["entry_id"]
            active = gw >= manager.get("started_event", 1)
            history = histories.get(entry_id) or {}
            row = next(
                (r for r in history.get("current", []) if int(r["event"]) == gw), None
            )
            chip = next(
                (
                    c.get("name")
                    for c in history.get("chips", [])
                    if int(c.get("event", 0)) == gw
                ),
                None,
            )

            if not active:
                scores[manager["id"]] = ManagerScore(active=False)
                continue

            # A manager active for this gameweek but with no history row never
            # set a squad. They score 0 and still pay (§10).
            did_not_set = row is None and is_final

            stats = TiebreakStats()
            if is_final and row is not None and element_stats:
                picks = fetcher.entry_picks(entry_id, gw, final=True)
                if picks:
                    stats = stats_for_xi(starting_xi(picks), element_stats)

            scores[manager["id"]] = ManagerScore(
                points=int(row["points"]) if row else (0 if did_not_set else None),
                hits=int(row.get("event_transfers_cost", 0)) if row else 0,
                transfers=int(row.get("event_transfers", 0)) if row else 0,
                chip=chip,
                did_not_set=did_not_set,
                active=True,
                stats=stats,
            )

        gameweeks[gw] = Gameweek(
            gw=gw, month=event["month"], state=state, scores=scores, note=note
        )
    return gameweeks, states


def fixture_note(fixtures: list[dict]) -> str | None:
    """§10 — a double or blank gameweek, so a huge score reads as fixture luck."""
    if not fixtures:
        return None
    appearances: dict[int, int] = {}
    for fixture in fixtures:
        for team in (fixture["team_h"], fixture["team_a"]):
            appearances[team] = appearances.get(team, 0) + 1
    if any(count >= 2 for count in appearances.values()):
        return "double gameweek"
    if len(fixtures) < 10:
        return "blank gameweek"
    return None


def shape_fixtures(fixtures: list[dict]) -> dict[int, list[dict]]:
    by_gw: dict[int, list[dict]] = {}
    for fixture in fixtures:
        if fixture.get("event") is None:
            continue
        by_gw.setdefault(int(fixture["event"]), []).append(fixture)
    for gw in by_gw:
        by_gw[gw].sort(key=lambda f: (f.get("kickoff_time") or "", f.get("id", 0)))
    return by_gw


def contract_fixtures(by_gw: dict[int, list[dict]]) -> dict[int, list[dict]]:
    """The `fixtures` block of the contract. Order here is the join key the live
    layer uses, so it must match what the client rebuilds from the proxy."""
    return {
        gw: [
            {
                "h": f["team_h"],
                "a": f["team_a"],
                "ko": iso_z(parse_utc(f["kickoff_time"])) if f.get("kickoff_time") else None,
                "dh": f.get("team_h_difficulty"),
                "da": f.get("team_a_difficulty"),
                "hs": f.get("team_h_score"),
                "as": f.get("team_a_score"),
                "started": bool(f.get("started")),
                "finished": bool(f.get("finished")),
                "finished_provisional": bool(f.get("finished_provisional")),
                "id": f.get("id"),
            }
            for f in rows
        ]
        for gw, rows in by_gw.items()
    }


def derive_current(states: dict[int, str], events: list[dict]) -> dict:
    live = next((gw for gw in sorted(states) if states[gw] in ("live", "provisional")), None)
    final_gws = [gw for gw in sorted(states) if states[gw] == "final"]
    unplayed = [gw for gw in sorted(states) if states[gw] not in ("final", "live", "provisional")]

    if live is not None:
        gameweek, state = live, states[live]
        next_gw = min(live + 1, EXPECTED_GAMEWEEKS)
    elif final_gws:
        gameweek = final_gws[-1]
        next_gw = unplayed[0] if unplayed else gameweek
        state = "final" if not unplayed else (
            "locked" if states[next_gw] == "locked" else "final"
        )
    else:
        gameweek = 0
        next_gw = unplayed[0] if unplayed else 1
        state = states.get(next_gw, "upcoming")

    return {"season": SEASON, "gameweek": gameweek, "next_gw": next_gw, "state": state}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="read only from cache and raw/")
    parser.add_argument("--out", type=Path, default=DATA_JSON)
    parser.add_argument("--no-backup", action="store_true", help="skip CSV ledger snapshots")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    now = datetime.now(timezone.utc)
    fetcher = Fetcher(offline=args.offline)

    try:
        bootstrap = fetcher.bootstrap()
        standings = fetcher.league_standings(LEAGUE_ID)
        all_fixtures = fetcher.fixtures()
        managers = collect_managers(standings, fetcher)
    except FetchError as exc:
        log.error("could not reach the FPL API: %s", exc)
        log.error("nothing published — the previous data.json stays live and will age "
                  "into the stale banner")
        return 2

    if not managers:
        log.error("league %s returned no members", LEAGUE_ID)
        return 2

    n = len(managers)
    if not season_third_share_ok(n):
        log.error(
            "third place would lose money at N=%d: the 15%% share is below 1/N (§3.2)", n
        )
        return 1

    events = build_events(bootstrap["events"])
    month_buckets = build_month_buckets(events)
    breaks = build_breaks(events)
    teams = {int(t["id"]): t for t in bootstrap["teams"]}
    fixtures_by_gw = shape_fixtures(all_fixtures)

    try:
        gameweeks, states = build_gameweeks(managers, events, fixtures_by_gw, fetcher, now)
    except FetchError as exc:
        log.error("gameweek detail unavailable: %s", exc)
        return 2

    roster = [
        {k: v for k, v in m.items() if k != "started_event"} for m in managers
    ]

    try:
        settlement = settle(managers, gameweeks, month_buckets, EXPECTED_GAMEWEEKS)
    except LedgerError as exc:
        log.error("LEDGER DOES NOT BALANCE: %s", exc)
        log.error("nothing published — a silently wrong payout is far worse than a "
                  "missing update (§3.8.5)")
        return 1

    payload = build_payload(
        generated_at=iso_z(now),
        league_name=standings.get("league", {}).get("name", "SuperF"),
        league_id=LEAGUE_ID,
        managers=roster,
        teams=teams,
        events=events,
        breaks=breaks,
        month_buckets=month_buckets,
        fixtures_by_gw=contract_fixtures(fixtures_by_gw),
        pl_table=build_table(all_fixtures, teams),
        gameweeks=gameweeks,
        settlement=settlement,
        current=derive_current(states, events),
    )

    if not payload["checks"]["zero_sum"]:
        log.error("zero-sum check failed after assembly — refusing to publish")
        return 1
    if payload["checks"]["gameweeks_present"] != EXPECTED_GAMEWEEKS:
        log.error(
            "calendar has %d gameweeks, expected %d (§3.9)",
            payload["checks"]["gameweeks_present"], EXPECTED_GAMEWEEKS,
        )
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")

    settled_through = payload["settled"]["through_gw"]
    log.info(
        "wrote %s — N=%d, settled through GW%d, %s",
        args.out, n, settled_through, fetcher.summary(),
    )

    if not args.no_backup and settled_through:
        written = backup.write_csv_snapshots(payload)
        log.info("wrote %d CSV backup file(s)", written)
        backup.push_to_sheets(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
