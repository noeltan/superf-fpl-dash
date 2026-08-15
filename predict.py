#!/usr/bin/env python3
"""Build ``docs/prediction.json`` — the weekly winner call (spec §12).

Two layers (§12.2): a deterministic projection in code, then one Claude call
for the ranking and the reasoning. The model may only quote numbers the
projection produced, and that is enforced, not requested.

Timing (§12.1). Squad picks are not public before the deadline, so this runs in
the gap between the deadline and the first kickoff — 90 minutes for GW1, less
for some others. If the window has closed, publish nothing: a late prediction
is worthless and it looks like cheating.

Modes:
    predict.py             call the upcoming gameweek (inside the window)
    predict.py --score     only score a finished gameweek into `result`/`record`
    predict.py --force     ignore the window guard (manual reruns, testing)

Exit codes:
    0  published, scored, or deliberately did nothing
    1  refused to publish (window closed, picks unavailable)
    2  the API could not be reached
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from superf.claude_call import allowed_numbers, build_prompt, request_call
from superf.config import (
    DATA_JSON,
    LEAGUE_ID,
    PREDICTION_JSON,
    PREDICTIONS,
    load_manager_overrides,
)
from superf.fpl import Fetcher, FetchError
from superf.fplcal import iso_z, parse_utc
from superf.projection import fixtures_by_team, project_manager, swing_candidates

log = logging.getLogger("predict")

OPEN_AFTER = timedelta(minutes=3)  # never fire before the deadline has actually passed
HARD_LIMIT = timedelta(minutes=50)  # a cron delayed beyond this has missed the point
KICKOFF_MARGIN = timedelta(minutes=5)

EMPTY_RECORD = {"played": 0, "exact": 0, "podium": 0, "pair": 0}


# --- scoring (§12.3) ---------------------------------------------------------

def actual_order(gameweek_row: dict) -> list[str]:
    """Finishing order for a settled gameweek, winners pinned to the front."""
    scores = gameweek_row.get("scores", {})
    points = {
        manager: score["points"]
        for manager, score in scores.items()
        if score.get("active", True) and score.get("points") is not None
    }
    winners = set(gameweek_row.get("winners", []))
    return sorted(points, key=lambda m: (-points[m], m not in winners, m))


def score_prediction(prediction: dict, gameweek_row: dict) -> dict:
    """Exact / podium / pair hits and mean rank error, per §12.3's table."""
    order = actual_order(gameweek_row)
    if len(order) < 2:
        return prediction

    called_first = prediction["call"]["first"]["manager"]
    called_second = prediction["call"]["second"]["manager"]
    actual_first, actual_second = order[0], order[1]
    top_two = {actual_first, actual_second}

    def place(manager: str) -> int:
        return order.index(manager) + 1 if manager in order else len(order)

    prediction["result"] = {
        "actual_first": actual_first,
        "actual_second": actual_second,
        "exact_hit": called_first == actual_first,
        "podium_hit": called_first in top_two,
        "pair_hit": {called_first, called_second} == top_two,
        "mean_rank_error": round(
            (abs(place(called_first) - 1) + abs(place(called_second) - 2)) / 2, 2
        ),
    }
    return prediction


def roll_record(record: dict, result: dict) -> dict:
    """A prediction feature that isn't scored is astrology (§12.3)."""
    return {
        "played": record.get("played", 0) + 1,
        "exact": record.get("exact", 0) + (1 if result["exact_hit"] else 0),
        "podium": record.get("podium", 0) + (1 if result["podium_hit"] else 0),
        "pair": record.get("pair", 0) + (1 if result["pair_hit"] else 0),
    }


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.warning("%s is not valid JSON", path)
        return None


def archive_path(gw: int) -> Path:
    return PREDICTIONS / f"gw{gw:02d}.json"


def write_prediction(prediction: dict) -> None:
    PREDICTION_JSON.parent.mkdir(parents=True, exist_ok=True)
    PREDICTIONS.mkdir(parents=True, exist_ok=True)
    body = json.dumps(prediction, indent=1) + "\n"
    PREDICTION_JSON.write_text(body)
    archive_path(prediction["gw"]).write_text(body)


def settle_outstanding(data: dict) -> dict:
    """Score the published prediction if its gameweek has since gone Final.

    Returns the running record to carry into the next call. This is what makes
    ``prediction.json`` flip from a call into a verdict without changing shape.
    """
    record = dict(EMPTY_RECORD)
    published = load_json(PREDICTION_JSON)
    if not published:
        return record

    record = published.get("record") or record
    if published.get("result"):
        return record  # already scored

    row = next((g for g in data.get("gameweeks", []) if g["gw"] == published["gw"]), None)
    if row is None:
        return record  # its gameweek is not Final yet

    scored = score_prediction(published, row)
    if not scored.get("result"):
        return record
    record = roll_record(record, scored["result"])
    scored["record"] = record
    write_prediction(scored)
    log.info(
        "scored GW%d: %s (record now %d exact of %d)",
        scored["gw"],
        "exact hit" if scored["result"]["exact_hit"]
        else "podium hit" if scored["result"]["podium_hit"] else "missed",
        record["exact"], record["played"],
    )
    return record


# --- the window (§12.1) ------------------------------------------------------

def window_for(deadline: datetime, gw_fixtures: list[dict]) -> tuple[datetime, datetime]:
    """When it is legitimate to publish: after the deadline, before the football.

    The binding constraint is the first kickoff, not the clock — GW1 has a full
    90 minutes and there is no reason to waste it, while some gameweeks have far
    less. ``HARD_LIMIT`` only applies when kickoff times are not published yet.
    """
    opens = deadline + OPEN_AFTER
    kickoffs = [parse_utc(f["kickoff_time"]) for f in gw_fixtures if f.get("kickoff_time")]
    if kickoffs:
        return opens, min(kickoffs) - KICKOFF_MARGIN
    return opens, deadline + HARD_LIMIT


def target_gameweek(events: list[dict], fixtures_by_gw: dict[int, list[dict]], now: datetime):
    """The most recent gameweek whose deadline has passed and which is unplayed."""
    for event in sorted(events, key=lambda e: e["id"], reverse=True):
        deadline = parse_utc(event["deadline_time"])
        if deadline > now:
            continue
        gw = int(event["id"])
        if all(f.get("finished") for f in fixtures_by_gw.get(gw, [])) and fixtures_by_gw.get(gw):
            return None, None  # the latest passed gameweek is already done
        return gw, deadline
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true", help="only score, do not call")
    parser.add_argument("--force", action="store_true", help="ignore the timing window")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--gw", type=int, help="override the target gameweek")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    now = datetime.now(timezone.utc)

    data = load_json(DATA_JSON)
    if not data:
        log.error("docs/data.json is missing — run build.py first")
        return 1

    record = settle_outstanding(data)
    if args.score:
        return 0

    fetcher = Fetcher(offline=args.offline)
    try:
        bootstrap = fetcher.bootstrap()
        all_fixtures = fetcher.fixtures()
    except FetchError as exc:
        log.error("could not reach the FPL API: %s", exc)
        return 2

    fixtures_by_gw: dict[int, list[dict]] = {}
    for fixture in all_fixtures:
        if fixture.get("event") is not None:
            fixtures_by_gw.setdefault(int(fixture["event"]), []).append(fixture)

    if args.gw:
        gw = args.gw
        event = next(e for e in bootstrap["events"] if int(e["id"]) == gw)
        deadline = parse_utc(event["deadline_time"])
    else:
        gw, deadline = target_gameweek(bootstrap["events"], fixtures_by_gw, now)

    if gw is None:
        log.info("no gameweek is awaiting a call right now")
        return 0

    existing = load_json(PREDICTION_JSON)
    if existing and existing.get("gw") == gw and not args.force:
        log.info("GW%d already has a published call", gw)
        return 0

    gw_fixtures = fixtures_by_gw.get(gw, [])
    opens, closes = window_for(deadline, gw_fixtures)
    if not args.force:
        if now < opens:
            log.info("too early for GW%d — the window opens at %s", gw, iso_z(opens))
            return 0
        if now > closes:
            log.error(
                "GW%d window closed at %s and it is now %s — publishing nothing. "
                "A late prediction is worthless and it looks like cheating (§12.1).",
                gw, iso_z(closes), iso_z(now),
            )
            return 1

    # --- squads ---------------------------------------------------------------
    managers = data["managers"]
    elements = {int(e["id"]): e for e in bootstrap["elements"]}
    teams = {int(t["id"]): t for t in bootstrap["teams"]}
    by_team = fixtures_by_team(gw_fixtures)

    projections = []
    squads: dict[str, list[dict]] = {}
    for manager in managers:
        try:
            picks = fetcher.entry_picks(manager["entry_id"], gw, final=False)
        except FetchError as exc:
            log.error("picks unavailable for %s: %s", manager["id"], exc)
            return 2
        if not picks or not picks.get("picks"):
            log.error(
                "GW%d picks are not public for %s — publishing nothing rather than a "
                "partial field", gw, manager["id"],
            )
            return 1
        projection = project_manager(
            manager["id"], picks, elements, by_team, teams
        )
        projections.append(projection)
        squads[manager["id"]] = [
            {"name": p.name, "team": p.team} for p in projection.players[:11]
        ]

    contract_projections = [p.to_contract() for p in projections]
    shortlist = swing_candidates(projections)
    names = {m["id"]: m["display_name"] for m in managers}

    prompt = build_prompt(
        gw=gw,
        deadline=iso_z(deadline),
        projections=contract_projections,
        squads=squads,
        fixtures=gw_fixtures,
        teams=teams,
        managers=managers,
        swing_shortlist=shortlist,
        record=record,
    )
    call, model = request_call(
        prompt=prompt,
        allowed=allowed_numbers(contract_projections, gw, gw_fixtures, len(managers)),
        manager_ids={m["id"] for m in managers},
        swing_names={c["name"] for c in shortlist},
        projections=contract_projections,
        names=names,
    )
    fallback = call.pop("_fallback", False)

    prediction = {
        "gw": gw,
        "generated_at": iso_z(now),
        "model": model,
        "projections": sorted(contract_projections, key=lambda p: -p["xp"]),
        "call": call,
        "result": None,
        "record": record,
    }
    write_prediction(prediction)
    log.info(
        "published GW%d call: 1st %s, 2nd %s%s (%s)",
        gw, call["first"]["manager"], call["second"]["manager"],
        " [projection fallback]" if fallback else "", fetcher.summary(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
