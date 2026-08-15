"""League configuration. Every money figure is derived from these and ``N``.

Nothing in this file is a payout. Payouts come out of :mod:`superf.money`.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LEAGUE_ID = int(os.environ.get("SUPERF_LEAGUE_ID", "310479"))
SEASON = os.environ.get("SUPERF_SEASON", "2026/27")

# Malaysia is UTC+8 year round, no DST. A fixed offset is exact here and avoids
# depending on the tz database being installed on the runner.
TZ_NAME = "Asia/Kuala_Lumpur"
TZ_OFFSET_HOURS = 8
MYT = timezone(timedelta(hours=TZ_OFFSET_HOURS), TZ_NAME)
UTC = timezone.utc

CURRENCY = "RM"

# --- stakes (§3.1, §3.7) -----------------------------------------------------
# Every gameweek costs RM10 — RM5 to the week, RM5 to the month.
WEEKLY_STAKE_RM = 5
MONTHLY_STAKE_PER_GW_RM = 5  # §3.7: explicit config entry, never inferred
SEASON_STAKE_RM = 100

WEEKLY_SPLIT = [1.00]
MONTHLY_SPLIT = [0.70, 0.30]
SEASON_SPLIT = [0.60, 0.25, 0.15]

# --- calendar ----------------------------------------------------------------
EXPECTED_GAMEWEEKS = 38  # §3.9 — assert it, the old sheet stopped at 37
BREAK_MIN_DAYS = 9  # a gap this long between deadlines is an international break

MONTH_ORDER = ["AUG", "SEP", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY"]

# --- API ---------------------------------------------------------------------
FPL_BASE = "https://fantasy.premierleague.com/api"
USER_AGENT = os.environ.get(
    "SUPERF_USER_AGENT",
    "superf-fpl-dash/1.0 (+https://github.com/noeltan/superf-fpl-dash)",
)
REQUEST_DELAY_SECONDS = float(os.environ.get("SUPERF_REQUEST_DELAY", "0.4"))
MAX_REQUESTS_PER_RUN = int(os.environ.get("SUPERF_MAX_REQUESTS", "400"))
REQUEST_TIMEOUT = 30
RETRY_BACKOFF = [2, 4, 8, 16]

# --- paths -------------------------------------------------------------------
DOCS = ROOT / "docs"
RAW = ROOT / "raw"  # immutable per-gameweek API snapshots
CACHE = ROOT / ".cache"  # volatile HTTP cache (ETags), safe to delete
BACKUPS = ROOT / "backups"  # CSV ledger snapshots per finalised gameweek
PREDICTIONS = DOCS / "predictions"

DATA_JSON = DOCS / "data.json"
PREDICTION_JSON = DOCS / "prediction.json"

MANAGERS_FILE = ROOT / "managers.json"


def load_manager_overrides() -> dict[int, dict]:
    """Stable slug + display name per entry_id.

    §5: the slug is "assigned once from entry_id — never changes". The API
    cannot produce these names on its own (Chris arrives as first_name
    "Christopher Chin", last_name "Jing Haur"), so they are pinned here.
    A manager who joins later and is absent from this file gets an
    auto-generated slug, which then wants adding so it is stable from then on.
    """
    if not MANAGERS_FILE.exists():
        return {}
    raw = json.loads(MANAGERS_FILE.read_text())
    return {int(m["entry_id"]): m for m in raw["managers"]}


def season_third_share_ok(n: int) -> bool:
    """§3.2 design constraint: the third-place share must stay >= 1/N.

    Note the direction. 15% >= 1/N holds for every N >= 7 and only gets safer as
    the league grows, so this can only break *downward* — at N <= 6 third place
    finishes down on the season. §3.2's "revisit past 12" reads the risk the
    wrong way round.
    """
    if n <= 0 or len(SEASON_SPLIT) < 3:
        return False
    return SEASON_SPLIT[2] >= 1 / n
