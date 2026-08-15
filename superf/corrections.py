"""Spec §3.9.4 — corrections never rewrite history.

Under deferred settlement the book is the product. An error posted in November
survives seven months and only surfaces when someone is asked for RM400, so
"the numbers changed and nobody knows when" is the failure mode that destroys
trust.

If an error is found in March, **do not recompute and silently republish.** Post
an adjusting entry. Corrections appear as their own rows in the statement, must
themselves sum to zero, and are committed like everything else. The original
entry stays visible.

    {
      "type": "correction",
      "date": "2027-03-14",
      "affects_gw": 12,
      "reason": "GW12 tiebreak applied cards before goals conceded",
      "adjustments": { "jack": 3500, "sam": -3500 }     // sen
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .money import LedgerError


def load(path: Path) -> list[dict]:
    """Read ``corrections.json``. Absent or empty is the normal case."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text() or "[]")
    entries = raw.get("corrections", raw) if isinstance(raw, dict) else raw
    validate(entries)
    return list(entries)


def validate(entries: Sequence[Mapping], known: Iterable[str] | None = None) -> None:
    """Each correction must balance, or it is not a correction — it is a payout."""
    known_set = set(known) if known is not None else None
    for index, entry in enumerate(entries):
        for field in ("date", "reason", "adjustments"):
            if field not in entry:
                raise LedgerError(f"correction {index} is missing {field!r}")
        adjustments = entry["adjustments"]
        if not adjustments:
            raise LedgerError(f"correction {index} adjusts nobody")
        total = sum(int(v) for v in adjustments.values())
        if total != 0:
            raise LedgerError(
                f"correction {index} ({entry['reason']!r}) does not sum to zero: "
                f"residue {total} sen"
            )
        if known_set is not None:
            unknown = set(adjustments) - known_set
            if unknown:
                raise LedgerError(
                    f"correction {index} adjusts unknown managers: {sorted(unknown)}"
                )


def apply(entries: Sequence[Mapping], managers: Iterable[str]) -> dict[str, int]:
    """Net adjustment per manager, in sen. Sums to zero across the league."""
    net = {m: 0 for m in managers}
    for entry in entries:
        for manager, amount in entry["adjustments"].items():
            net[manager] = net.get(manager, 0) + int(amount)
    return net


def statement_rows(entries: Sequence[Mapping], manager: str) -> list[dict]:
    """The correction rows that belong in one manager's statement (§3.9.2)."""
    rows = []
    for entry in entries:
        amount = int(entry["adjustments"].get(manager, 0))
        if amount == 0:
            continue
        rows.append(
            {
                "date": entry["date"],
                "type": "correction",
                "affects_gw": entry.get("affects_gw"),
                "detail": entry["reason"],
                "amount": amount,
            }
        )
    return rows
