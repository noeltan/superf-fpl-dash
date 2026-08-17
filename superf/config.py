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
# Every gameweek costs RM15 — RM10 to the week, RM5 to the month.
# The weekly pot pays the top two, 70/30, like the monthly.
WEEKLY_STAKE_RM = 10
MONTHLY_STAKE_PER_GW_RM = 5  # §3.7: explicit config entry, never inferred
SEASON_STAKE_RM = 100

WEEKLY_SPLIT = [0.70, 0.30]
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

# --- paths (§4.1 — the git repo is the database) ------------------------------
SEASON_SLUG = SEASON.replace("/", "-")            # "2026/27" -> "2026-27"
DATA_DIR = ROOT / "data" / SEASON_SLUG            # canonical, append-only history
RAW = DATA_DIR / "raw"                            # immutable pruned API snapshots
DOCS = ROOT / "docs"                              # GitHub Pages root (published copy)
CACHE = ROOT / ".cache"                           # volatile HTTP cache, safe to delete
BACKUPS = ROOT / "backups"                        # CSV snapshots per finalised gameweek
PREDICTIONS = DOCS / "predictions"

# `data.json` is a derived artifact: disposable, rebuildable byte-identically
# from raw/ plus corrections.json (§4.2). `raw/` and `corrections.json` are not.
DATA_JSON = DOCS / "data.json"
PREDICTION_JSON = DOCS / "prediction.json"
CANONICAL_DATA = DATA_DIR / "data.json"
CANONICAL_PREDICTION = DATA_DIR / "prediction.json"
CORRECTIONS_JSON = DATA_DIR / "corrections.json"

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


def paid_place_floor_ok(n: int, split: list[float]) -> bool:
    """No paid place may lose money: the smallest paid share must be >= 1/N.

    §3.2 states this for the season's third place, but it is a property of any
    split, and the weekly pot acquired a paid second place when it moved to
    70/30 — so it is checked per pot rather than written once for the season.

    Note the direction. A share ``s`` covers its own stake once ``s >= 1/N``, so
    a bigger league is always safer and a *shrinking* one is what to watch.
    §3.2's "revisit if the league grows past 12" reads the risk backwards.

    At our splits: the season's 15% needs N >= 7; the weekly and monthly 30%
    needs N >= 4.
    """
    if n <= 0 or not split:
        return False
    return split[-1] >= 1 / n


def pot_floors(n: int) -> list[str]:
    """Every pot whose last paid place would finish down at this league size."""
    pots = (("weekly", WEEKLY_SPLIT), ("monthly", MONTHLY_SPLIT), ("season", SEASON_SPLIT))
    return [name for name, split in pots if not paid_place_floor_ok(n, split)]
