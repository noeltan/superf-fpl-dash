"""Backups — the permanent record the end-of-season settle-up is paid from.

Money is not moved gameweek by gameweek. It accrues all season and is squared
up once at the end, so the running per-pot tally *is* the debt: if this record
is lost, nobody can prove what anybody owes. That makes the backup part of the
system, not a convenience.

Two layers, so the first one works before anything is configured:

* **CSV, always.** Written to ``backups/`` and committed, so every finalised
  gameweek leaves an immutable, diffable record in ``git log`` alongside the
  ledger that produced it.
* **Google Sheets, when configured.** Set ``GOOGLE_SERVICE_ACCOUNT_JSON`` and
  ``SHEET_ID`` as repository secrets and each CSV is mirrored to a tab. Absent
  those, this no-ops cleanly rather than failing the build.

``settle_up.csv`` is the one people will actually read in May: the minimum set
of payments that takes everybody to zero.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from pathlib import Path

from .config import BACKUPS
from .money import minimum_transfers

log = logging.getLogger(__name__)

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


# --- the settle-up ----------------------------------------------------------

def net_settlement(totals: dict[str, int]) -> list[dict]:
    """§3.9.3, in sen. Thin wrapper so the CSV and data.json cannot disagree."""
    return minimum_transfers(totals)


# --- table builders ----------------------------------------------------------

def _name(payload: dict, manager_id: str) -> str:
    for manager in payload["managers"]:
        if manager["id"] == manager_id:
            return manager["display_name"]
    return manager_id


def _rm(sen: int) -> str:
    return f"{sen / 100:.2f}"


def _gw_points(gameweek: dict, manager_id: str):
    """One manager's score in one gameweek, as the sheet should carry it.

    Blank for a manager who was not in the league yet (§3.8.6) — the cell has
    no value rather than a zero, so a column average is not dragged down by
    gameweeks somebody could not have played. A manager who was in the league
    and never set a team really did score 0, and that 0 is the number.
    """
    score = gameweek["scores"].get(manager_id) or {}
    if not score.get("active"):
        return ""
    points = score.get("points")
    return 0 if points is None else points


def ledger_rows(payload: dict) -> list[list]:
    """The league table: the accrued book (§3.9.1), a column per gameweek.

    The gameweek columns are the shape the league read for seven seasons on the
    old sheet — one row per manager, one column per week, the season total at
    the end of them. The `gameweeks` tab holds the same scores long-form, with
    the money each one moved; this is the at-a-glance grid, and having both is
    the point. Neither computes anything: the scores are read out of the
    settled book exactly as `data.json` published them.
    """
    settled = payload["settlement"]["settled"]
    gameweeks = payload["gameweeks"]
    gw_headers = [f"GW{gameweek['gw']}" for gameweek in gameweeks]
    rows = [[
        "manager", "team", "entry_id", *gw_headers, "points", "weekly_rm",
        "monthly_rm", "season_rm", "season_is_settled", "accrued_rm", "position",
    ]]
    for manager_id in payload["rank"]:
        entry = next(m for m in payload["managers"] if m["id"] == manager_id)
        led = payload["ledger"][manager_id]
        accrued = led["accrued"]
        rows.append([
            entry["display_name"], entry["team_name"], entry["entry_id"],
            *[_gw_points(gameweek, manager_id) for gameweek in gameweeks],
            payload["totals"].get(manager_id, 0),
            _rm(led["weekly"]), _rm(led["monthly"]), _rm(led["projected_season"]),
            "yes" if settled else "no (projected)",
            _rm(accrued),
            "square" if accrued == 0 else ("is owed" if accrued > 0 else "owes"),
        ])
    rows.append([
        "TOTAL", "", "", *[""] * len(gameweeks), "", "", "", "", "",
        _rm(sum(payload["ledger"][m]["accrued"] for m in payload["ledger"])), "",
    ])
    return rows


def statement_rows(payload: dict) -> list[list]:
    """§3.9.2 — every manager's line, so a disputed total can be decomposed."""
    rows = [["manager", "date", "event", "detail", "amount_rm", "balance_rm"]]
    for manager_id in payload["rank"]:
        for row in payload["ledger"][manager_id].get("statement", []):
            event = (
                f"GW{row['gw']} weekly" if row["type"] == "weekly"
                else f"{row['month']} monthly" if row["type"] == "monthly"
                else "correction"
            )
            rows.append([
                _name(payload, manager_id), row["date"], event, row["detail"],
                _rm(row["amount"]), _rm(row["balance"]),
            ])
    return rows


def gameweek_rows(payload: dict) -> list[list]:
    """Per manager per gameweek: score, that week's money, running total."""
    gameweeks = payload["gameweeks"]
    rows = [["manager", "gw", "month", "points", "hits", "chip", "did_not_set",
             "won_pot", "weekly_rm", "running_weekly_rm"]]
    for manager_id in payload["rank"]:
        running = 0
        for index, gameweek in enumerate(gameweeks):
            score = gameweek["scores"].get(manager_id, {})
            if not score.get("active", True):
                continue
            amount = payload["ledger"][manager_id]["by_gameweek"][index]
            running += amount
            rows.append([
                _name(payload, manager_id), gameweek["gw"], gameweek["month"],
                score.get("points"), score.get("hits", 0), score.get("chip") or "",
                "yes" if score.get("did_not_set") else "",
                "yes" if manager_id in gameweek["winners"] else "",
                _rm(amount), _rm(running),
            ])
    return rows


def month_rows(payload: dict) -> list[list]:
    rows = [["month", "gameweeks", "pot_rm", "stake_rm", "position", "manager",
             "points", "net_rm"]]
    for month in payload["months"]:
        for position, manager_id in enumerate(month["order"], start=1):
            net = month["net"][position - 1] if position <= len(month["net"]) else -month["stake"]
            rows.append([
                month["month"], len(month["gameweeks"]), month["pot"], month["stake"],
                position, _name(payload, manager_id),
                month["totals"].get(manager_id, 0), net,
            ])
    return rows


def settle_up_rows(payload: dict) -> list[list]:
    """§3.9.3 — read straight from data.json so the sheet and the site agree."""
    rows = [["from", "to", "amount_rm"]]
    for payment in payload["settlement"]["payments"]:
        rows.append([
            _name(payload, payment["from"]), _name(payload, payment["to"]),
            _rm(payment["amount"]),
        ])
    if len(rows) == 1:
        rows.append(["nobody owes anybody", "", "0.00"])
    return rows


TABLES = {
    "ledger": ledger_rows,
    "statement": statement_rows,
    "gameweeks": gameweek_rows,
    "months": month_rows,
    "settle_up": settle_up_rows,
}


# --- CSV ---------------------------------------------------------------------

def write_csv_snapshots(payload: dict, root: Path | None = None) -> int:
    """Write one directory per settled gameweek, plus a `latest` mirror."""
    root = root or BACKUPS
    through = payload["settled"]["through_gw"]
    if not through:
        return 0

    written = 0
    for directory in (root / f"gw{through:02d}", root / "latest"):
        directory.mkdir(parents=True, exist_ok=True)
        for name, builder in TABLES.items():
            path = directory / f"{name}.csv"
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(builder(payload))
            written += 1
    return written


def _csv_text(rows: list[list]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


# --- Google Sheets -----------------------------------------------------------

def push_to_sheets(payload: dict) -> bool:
    """Mirror each table to a tab. No-ops unless both secrets are present."""
    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = os.environ.get("SHEET_ID", "").strip()
    if not credentials_json or not sheet_id:
        log.info("Sheets backup skipped — GOOGLE_SERVICE_ACCOUNT_JSON / SHEET_ID not set")
        return False

    try:
        from google.oauth2.service_account import Credentials  # type: ignore
        from google.auth.transport.requests import AuthorizedSession  # type: ignore
    except ImportError:
        log.warning("Sheets backup skipped — google-auth is not installed")
        return False

    try:
        info = json.loads(credentials_json)
        credentials = Credentials.from_service_account_info(info, scopes=[SHEETS_SCOPE])
        session = AuthorizedSession(credentials)
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"

        meta = session.get(base).json()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        missing = [name for name in TABLES if name not in existing]
        if missing:
            session.post(
                f"{base}:batchUpdate",
                json={"requests": [
                    {"addSheet": {"properties": {"title": name}}} for name in missing
                ]},
            ).raise_for_status()

        for name, builder in TABLES.items():
            rows = builder(payload)
            session.post(
                f"{base}/values/{name}!A1:ZZ10000:clear", json={}
            ).raise_for_status()
            session.put(
                f"{base}/values/{name}!A1",
                params={"valueInputOption": "RAW"},
                json={"values": rows},
            ).raise_for_status()

        log.info("mirrored %d tables to Google Sheets", len(TABLES))
        return True
    except Exception as exc:  # noqa: BLE001 — a backup must never fail the build
        log.warning("Sheets backup failed (the CSVs are still written): %s", exc)
        return False
