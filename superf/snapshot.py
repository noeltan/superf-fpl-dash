"""Spec §4.2 — store the raw API responses, not just the ledger.

`data.json` is a *derived artifact*. The source of truth is the FPL API response
as it existed **at the moment the gameweek settled**, snapshotted to
``data/<season>/raw/gw-NN.json``, committed, and never touched again.

Three reasons this is not optional:

1. **The FPL API is not an archive.** It serves current state. Players get
   renamed and repriced and stats are occasionally adjusted retrospectively. The
   GW1 data you read in May is not guaranteed to be the GW1 data you read in
   August.
2. **Bugs are found late.** A tiebreak error discovered in March has to be
   recomputed *from source*. Store only the output and you would be
   reconstructing the book from the very numbers you no longer trust.
3. **The tiebreak needs per-player detail** (§3.5) that `data.json` does not
   carry — goals, assists, conceded and cards for each manager's XI.

**Pruned, not whole.** The live endpoint carries every player in the game; the
league owns roughly 120 of them. Only those are kept, which is the difference
between a few hundred KB and tens of KB per gameweek.

The rule that falls out: **`raw/` is append-only; `data.json` is disposable.**
`tests/test_rebuild.py` holds us to it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Mapping, Sequence

from .config import RAW

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1

# The only element stats the ledger and the tiebreak ladder need. Keeping the
# list explicit means a snapshot cannot silently start carrying half the API.
KEPT_STATS = (
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "total_points",
)


def path_for(gw: int, root: Path | None = None) -> Path:
    return (root or RAW) / f"gw-{gw:02d}.json"


def exists(gw: int, root: Path | None = None) -> bool:
    return path_for(gw, root).exists()


def load(gw: int, root: Path | None = None) -> dict | None:
    path = path_for(gw, root)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def prune_elements(live: Mapping, owned: set[int]) -> dict[str, dict]:
    """Keep only the players the league actually owns, and only the stats used."""
    kept: dict[str, dict] = {}
    for element in (live or {}).get("elements", []):
        element_id = int(element["id"])
        if element_id not in owned:
            continue
        stats = element.get("stats", {}) or {}
        kept[str(element_id)] = {k: stats.get(k, 0) for k in KEPT_STATS}
    return kept


def build(
    *,
    gw: int,
    captured_at: str,
    managers: Sequence[Mapping],
    fixtures: Sequence[Mapping],
    histories: Mapping[int, Mapping],
    picks: Mapping[int, Mapping],
    live: Mapping,
) -> dict:
    """Assemble a self-contained, pruned record of one settled gameweek."""
    owned: set[int] = set()
    for payload in picks.values():
        for pick in (payload or {}).get("picks", []):
            owned.add(int(pick["element"]))

    history_rows: dict[str, dict] = {}
    for manager in managers:
        entry_id = int(manager["entry_id"])
        history = histories.get(entry_id) or {}
        row = next((r for r in history.get("current", []) if int(r["event"]) == gw), None)
        if row is not None:
            history_rows[str(entry_id)] = {
                k: row.get(k)
                for k in ("event", "points", "event_transfers", "event_transfers_cost",
                          "total_points", "rank", "points_on_bench")
            }
        chip = next(
            (c.get("name") for c in history.get("chips", []) if int(c.get("event", 0)) == gw),
            None,
        )
        if chip:
            history_rows.setdefault(str(entry_id), {})["chip"] = chip

    return {
        "version": SNAPSHOT_VERSION,
        "gw": gw,
        "captured_at": captured_at,
        "fixtures": [
            {
                k: fixture.get(k)
                for k in ("id", "event", "team_h", "team_a", "team_h_score",
                          "team_a_score", "team_h_difficulty", "team_a_difficulty",
                          "kickoff_time", "minutes", "started", "finished",
                          "finished_provisional")
            }
            for fixture in fixtures
        ],
        "history": history_rows,
        "picks": {
            str(entry_id): {
                "active_chip": (payload or {}).get("active_chip"),
                "entry_history": {
                    k: ((payload or {}).get("entry_history") or {}).get(k)
                    for k in ("event", "points", "event_transfers", "event_transfers_cost")
                },
                "picks": [
                    {
                        "element": p["element"], "position": p.get("position"),
                        "multiplier": p.get("multiplier"),
                        "is_captain": p.get("is_captain"),
                        "is_vice_captain": p.get("is_vice_captain"),
                    }
                    for p in (payload or {}).get("picks", [])
                ],
                "automatic_subs": [
                    {"element_in": s.get("element_in"), "element_out": s.get("element_out")}
                    for s in ((payload or {}).get("automatic_subs") or [])
                ],
            }
            for entry_id, payload in picks.items()
        },
        "elements": prune_elements(live, owned),
    }


def write(snapshot: Mapping, root: Path | None = None) -> Path:
    """Write once. A snapshot that already exists is never overwritten."""
    path = path_for(int(snapshot["gw"]), root)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so a rerun is byte-identical, which is what makes the rebuild
    # test meaningful.
    path.write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    log.info("snapshotted GW%d (%d bytes)", snapshot["gw"], path.stat().st_size)
    return path


# --- provisional pot leader (§11.4) ------------------------------------------
# Recorded separately from the immutable snapshot: it is an observation of a
# state that is explicitly not final, and it exists only so that a bonus flip can
# be named permanently once the gameweek settles.

def provisional_path(gw: int, root: Path | None = None) -> Path:
    return (root or RAW) / f"gw-{gw:02d}.provisional.json"


def record_provisional_leader(
    gw: int, leader: str, observed_at: str, root: Path | None = None
) -> None:
    path = provisional_path(gw, root)
    if path.exists() or not leader:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"gw": gw, "leader": leader, "observed_at": observed_at}, sort_keys=True) + "\n"
    )
    log.info("recorded provisional GW%d leader: %s", gw, leader)


def bonus_change_for(gw: int, final_winner: str, at: str, root: Path | None = None) -> dict | None:
    """§11.4 — name it, permanently, when confirmed bonus moved the pot.

    "That is precisely the moment people accuse each other of cheating, and a
    timestamped note ends it."
    """
    path = provisional_path(gw, root)
    if not path.exists() or not final_winner:
        return None
    record = json.loads(path.read_text())
    leader = record.get("leader")
    if not leader or leader == final_winner:
        return None
    return {"from": leader, "to": final_winner, "at": at}


# --- season-level mirror ------------------------------------------------------
# Per-gameweek snapshots freeze the scores, but `data.json` also needs the
# calendar, the clubs and the roster. Without those, "delete data.json and
# rebuild it from raw/" (§4.2) is only true of the ledger, not of the file.
#
# This one is refreshed every run rather than frozen: fixtures fill in scores and
# a manager can still join. It is the current-state mirror that makes a full
# offline rebuild possible; the immutable history lives in gw-NN.json.

def season_path(root: Path | None = None) -> Path:
    return (root or RAW) / "season.json"


def write_season(
    *,
    events: Sequence[Mapping],
    teams: Sequence[Mapping],
    managers: Sequence[Mapping],
    fixtures: Sequence[Mapping],
    league_name: str,
    root: Path | None = None,
) -> Path:
    path = season_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SNAPSHOT_VERSION,
        "league_name": league_name,
        "events": [
            {"id": e["id"], "deadline_time": e["deadline_time"]}
            for e in sorted(events, key=lambda e: e["id"])
        ],
        "teams": [
            {"id": t["id"], "name": t["name"], "short_name": t["short_name"]}
            for t in sorted(teams, key=lambda t: t["id"])
        ],
        "managers": [dict(m) for m in managers],
        "fixtures": [
            {
                k: f.get(k)
                for k in ("id", "event", "team_h", "team_a", "team_h_score",
                          "team_a_score", "team_h_difficulty", "team_a_difficulty",
                          "kickoff_time", "minutes", "started", "finished",
                          "finished_provisional")
            }
            for f in sorted(fixtures, key=lambda f: f.get("id", 0))
        ],
    }
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def load_season(root: Path | None = None) -> dict | None:
    path = season_path(root)
    return json.loads(path.read_text()) if path.exists() else None
