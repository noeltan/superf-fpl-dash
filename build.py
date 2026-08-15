#!/usr/bin/env python3
"""Build ``data.json`` — the book of record (spec §4 settled pipeline).

    GET /api/bootstrap-static/                     -> 38 events, deadlines, teams
    GET /api/leagues-classic/310479/standings/     -> members + entry_ids
    GET /api/entry/{entry_id}/history/  x N        -> per-GW points, hits, transfers
    GET /api/fixtures/                             -> results -> derive the PL table
    compute weekly / monthly / season ledgers (§3)
    ASSERT sum of balances == 0                    -> fail the build loudly if not
    snapshot pruned API responses -> raw/gw-NN.json (immutable, §4.2)
    emit data.json -> commit -> publish

Under deferred settlement (§3.9) nothing is paid until May, so this is not a
dashboard reporting on money that has already moved — it is the book of record
for money nobody has paid yet. An error posted in November survives seven months
and only surfaces when someone is asked for RM400, which is why every assertion
here fails the build rather than warning.

Exit codes:
    0  wrote data.json (or nothing needed writing)
    1  a ledger did not balance, or an invariant broke — nothing was published
    2  the API could not be reached and nothing usable was cached — nothing was
       published, so the previous data.json stays live and ages into the §9.5
       stale banner
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from superf import backup, corrections as corrections_mod, snapshot as snapshot_mod
from superf.config import (
    CANONICAL_DATA,
    CORRECTIONS_JSON,
    DATA_DIR,
    DATA_JSON,
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


def settled_date(fixtures: list[dict]) -> str:
    """The day a gameweek stopped moving — its last kickoff (§3.9.2 statement)."""
    kickoffs = [parse_utc(f["kickoff_time"]) for f in fixtures if f.get("kickoff_time")]
    return max(kickoffs).strftime("%Y-%m-%d") if kickoffs else ""


def gameweek_snapshot(
    gw: int,
    managers: list[dict],
    fixtures: list[dict],
    histories: dict[int, dict],
    fetcher: Fetcher,
    now: datetime,
) -> dict:
    """The immutable pruned record for a Final gameweek (§4.2). Written once."""
    existing = snapshot_mod.load(gw)
    if existing is not None:
        fetcher.snapshot_hits += 1
        return existing

    picks = {}
    for manager in managers:
        entry_id = int(manager["entry_id"])
        payload = fetcher.entry_picks(entry_id, gw)
        if payload and payload.get("picks"):
            picks[entry_id] = payload
    live = fetcher.event_live(gw) or {}

    record = snapshot_mod.build(
        gw=gw, captured_at=iso_z(now), managers=managers, fixtures=fixtures,
        histories=histories, picks=picks, live=live,
    )
    snapshot_mod.write(record)
    return record


def scores_from_snapshot(
    record: dict, managers: list[dict]
) -> dict[str, ManagerScore]:
    """Rebuild one gameweek's scores and tiebreak stats from the frozen record."""
    gw = int(record["gw"])
    element_stats = {int(k): v for k, v in record.get("elements", {}).items()}
    scores: dict[str, ManagerScore] = {}

    for manager in managers:
        entry_id = str(manager["entry_id"])
        active = gw >= manager.get("started_event", 1)
        if not active:
            scores[manager["id"]] = ManagerScore(active=False)
            continue

        row = record.get("history", {}).get(entry_id)
        picks = record.get("picks", {}).get(entry_id)
        # A manager active for this gameweek but with no history row never set a
        # squad. They score 0 and still pay (§10).
        did_not_set = row is None

        stats = TiebreakStats()
        if picks and element_stats:
            stats = stats_for_xi(starting_xi(picks), element_stats)

        scores[manager["id"]] = ManagerScore(
            points=int(row["points"]) if row and row.get("points") is not None else 0,
            hits=int((row or {}).get("event_transfers_cost") or 0),
            transfers=int((row or {}).get("event_transfers") or 0),
            chip=(row or {}).get("chip") or (picks or {}).get("active_chip"),
            did_not_set=did_not_set,
            active=True,
            stats=stats,
        )
    return scores


def build_gameweeks(
    managers: list[dict],
    events: list[dict],
    fixtures_by_gw: dict[int, list[dict]],
    histories: dict[int, dict],
    fetcher: Fetcher,
    now: datetime,
) -> tuple[dict[int, Gameweek], dict[int, str], dict[int, str]]:
    states: dict[int, str] = {}
    dates: dict[int, str] = {}
    gameweeks: dict[int, Gameweek] = {}

    for event in events:
        gw = event["gw"]
        deadline = parse_utc(event["deadline"])
        raw_fixtures = fixtures_by_gw.get(gw, [])
        state = gameweek_state(deadline, raw_fixtures, now)
        states[gw] = state
        dates[gw] = settled_date(raw_fixtures)
        note = fixture_note(raw_fixtures)

        if state == "final":
            record = gameweek_snapshot(gw, managers, raw_fixtures, histories, fetcher, now)
            scores = scores_from_snapshot(record, managers)
        else:
            scores = {}
            for manager in managers:
                active = gw >= manager.get("started_event", 1)
                if not active:
                    scores[manager["id"]] = ManagerScore(active=False)
                    continue
                history = histories.get(int(manager["entry_id"])) or {}
                row = next(
                    (r for r in history.get("current", []) if int(r["event"]) == gw), None
                )
                scores[manager["id"]] = ManagerScore(
                    points=int(row["points"]) if row else None,
                    hits=int(row.get("event_transfers_cost", 0)) if row else 0,
                    transfers=int(row.get("event_transfers", 0)) if row else 0,
                    active=True,
                )

            # §11.4 — remember who led while the gameweek was provisional, so a
            # bonus flip can be named permanently once it settles. The history
            # endpoint already carries live points, so this costs no requests.
            if state == "provisional":
                live_points = {
                    m: s.points for m, s in scores.items() if s.points is not None
                }
                if live_points:
                    leader = max(sorted(live_points), key=lambda m: live_points[m])
                    snapshot_mod.record_provisional_leader(gw, leader, iso_z(now))

        gameweeks[gw] = Gameweek(
            gw=gw, month=event["month"], state=state, scores=scores, note=note
        )
    return gameweeks, states, dates


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


def derive_current(states: dict[int, str]) -> dict:
    live = next((gw for gw in sorted(states) if states[gw] in ("live", "provisional")), None)
    final_gws = [gw for gw in sorted(states) if states[gw] == "final"]
    unplayed = [gw for gw in sorted(states) if states[gw] not in ("final", "live", "provisional")]

    if live is not None:
        return {
            "season": SEASON, "gameweek": live,
            "next_gw": min(live + 1, EXPECTED_GAMEWEEKS), "state": states[live],
        }
    if final_gws:
        gameweek = final_gws[-1]
        next_gw = unplayed[0] if unplayed else gameweek
        state = "locked" if unplayed and states[next_gw] == "locked" else "final"
        return {"season": SEASON, "gameweek": gameweek, "next_gw": next_gw, "state": state}
    next_gw = unplayed[0] if unplayed else 1
    return {
        "season": SEASON, "gameweek": 0, "next_gw": next_gw,
        "state": states.get(next_gw, "upcoming"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="read only from cache and raw/")
    parser.add_argument("--out", type=Path, default=None, help="override the published path")
    parser.add_argument("--no-backup", action="store_true", help="skip CSV ledger snapshots")
    parser.add_argument("--generated-at", help="pin generated_at, for reproducible rebuilds")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    now = datetime.now(timezone.utc)
    if args.generated_at:
        now = parse_utc(args.generated_at)
    fetcher = Fetcher(offline=args.offline)

    # §4.2 — "delete data.json and rebuild it from raw/ plus corrections.json".
    # That has to include the calendar, the clubs and the roster, not only the
    # scores, so the offline path reads the season mirror rather than the API.
    season = snapshot_mod.load_season() if args.offline else None
    if season:
        log.info("rebuilding offline from %s", snapshot_mod.season_path())
        raw_events = season["events"]
        teams = {int(t["id"]): t for t in season["teams"]}
        all_fixtures = season["fixtures"]
        managers = [dict(m) for m in season["managers"]]
        league_name = season.get("league_name", "SuperF")
    else:
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
        raw_events = bootstrap["events"]
        teams = {int(t["id"]): t for t in bootstrap["teams"]}
        league_name = standings.get("league", {}).get("name", "SuperF")

    if not managers:
        log.error("league %s returned no members", LEAGUE_ID)
        return 2

    n = len(managers)
    if not season_third_share_ok(n):
        log.error(
            "third place would lose money at N=%d: the 15%% share is below 1/N (§3.2)", n
        )
        return 1

    events = build_events(raw_events)
    month_buckets = build_month_buckets(events)
    breaks = build_breaks(events)
    fixtures_by_gw = shape_fixtures(all_fixtures)

    try:
        # Offline, every Final gameweek is read from its snapshot, so no history
        # is needed; unplayed gameweeks have no scores to carry anyway.
        histories = {} if season else {
            int(m["entry_id"]): (fetcher.entry_history(int(m["entry_id"])) or {})
            for m in managers
        }
        gameweeks, states, settled_dates = build_gameweeks(
            managers, events, fixtures_by_gw, histories, fetcher, now
        )
    except FetchError as exc:
        log.error("gameweek detail unavailable: %s", exc)
        return 2

    try:
        adjustments = corrections_mod.load(CORRECTIONS_JSON)
        corrections_mod.validate(adjustments, [m["id"] for m in managers])
    except LedgerError as exc:
        log.error("corrections.json is not valid: %s", exc)
        return 1

    try:
        settlement = settle(managers, gameweeks, month_buckets, EXPECTED_GAMEWEEKS)
    except LedgerError as exc:
        log.error("LEDGER DOES NOT BALANCE: %s", exc)
        log.error("nothing published — a silently wrong payout is far worse than a "
                  "missing update (§3.8.5)")
        return 1

    # §11.4 — name a bonus flip permanently, now that the winner is confirmed.
    for gw, ranking in settlement.rankings.items():
        if not ranking.winners:
            continue
        change = snapshot_mod.bonus_change_for(gw, ranking.winners[0], settled_dates.get(gw, ""))
        if change:
            gameweeks[gw].bonus_change = change
            log.info("GW%d bonus changed the winner: %s -> %s", gw, change["from"], change["to"])

    roster = [{k: v for k, v in m.items() if k != "started_event"} for m in managers]

    try:
        payload = build_payload(
            generated_at=iso_z(now),
            league_name=league_name,
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
            current=derive_current(states),
            settled_dates=settled_dates,
            corrections=adjustments,
        )
    except LedgerError as exc:
        log.error("BOOK DOES NOT BALANCE: %s", exc)
        return 1

    if not payload["checks"]["zero_sum"]:
        log.error("zero-sum check failed after assembly — refusing to publish")
        return 1
    if payload["checks"]["gameweeks_present"] != EXPECTED_GAMEWEEKS:
        log.error(
            "calendar has %d gameweeks, expected %d (§3.10)",
            payload["checks"]["gameweeks_present"], EXPECTED_GAMEWEEKS,
        )
        return 1

    # Canonical copy under data/<season>/, published copy under docs/ (§4.1).
    body = json.dumps(payload, indent=1, sort_keys=False) + "\n"
    CANONICAL_DATA.parent.mkdir(parents=True, exist_ok=True)
    CANONICAL_DATA.write_text(body)
    published = args.out or DATA_JSON
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text(body)
    if not CORRECTIONS_JSON.exists():
        CORRECTIONS_JSON.write_text('{"corrections": []}\n')
    if not season:
        snapshot_mod.write_season(
            events=raw_events, teams=list(teams.values()), managers=managers,
            fixtures=all_fixtures, league_name=league_name,
        )

    settled_through = payload["settled"]["through_gw"]
    accrued = payload["ledger"]
    owed = sum(v["accrued"] for v in accrued.values() if v["accrued"] > 0)
    log.info(
        "wrote %s — N=%d, settled through GW%d, RM%.2f accrued across %d payments, %s",
        published, n, settled_through, owed / 100,
        len(payload["settlement"]["payments"]), fetcher.summary(),
    )

    if not args.no_backup and settled_through:
        written = backup.write_csv_snapshots(payload)
        log.info("wrote %d CSV backup file(s)", written)
        backup.push_to_sheets(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
