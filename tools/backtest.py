#!/usr/bin/env python3
"""Replay every frozen projection and mark it against what happened.

    python tools/backtest.py            # the table
    python tools/backtest.py --json     # the same, for a script
    python tools/backtest.py --gw 3     # one gameweek

This is the only honest way to change :mod:`superf.projection`. Edit the model,
run this, and the numbers move or they do not. Without it, "the prediction feels
off" and "the prediction is off" are the same sentence.

It reads ``raw/gw-NN.projection.json`` — the inputs as they stood at the
deadline, written by predict.py — and rebuilds the ranking with *today's* code.
So a model change is scored against every gameweek ever played, not just the
next one. Mid-round records are skipped: they predict the remainder of a
gameweek, which is a different question with a different answer.

Alongside our ranking it scores **FPL's own ``ep_next``**, summed over the same
squads. That is the control. A projection that cannot beat the number the API
hands out for free is not earning its place, and knowing that is worth more
than any single Spearman coefficient.

Nothing here fetches. If a gameweek has no frozen inputs it is not scored, and
the tool says so rather than guessing — the record starts when the freezing
started, and pretending otherwise would be inventing history.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from predict import actual_order  # noqa: E402
from superf import snapshot as snapshot_mod  # noqa: E402
from superf.config import DATA_JSON, RAW  # noqa: E402
from superf.projection import fixtures_by_team, project_manager  # noqa: E402
from superf.scoring import aggregate, score_ranking  # noqa: E402


def frozen_gameweeks(root: Path) -> list[int]:
    """Pre-kickoff records only — see the module docstring."""
    return sorted(
        int(path.name[3:5])
        for path in root.glob("gw-??.projection.json")
    )


def our_ranking(record: dict) -> list[str]:
    elements, teams, fixtures, picks, scored = snapshot_mod.replay_projection_inputs(record)
    by_team = fixtures_by_team(fixtures)
    xp = {
        manager: project_manager(manager, payload, elements, by_team, teams, scored).xp
        for manager, payload in picks.items()
    }
    return sorted(xp, key=lambda m: (-xp[m], m))


def ep_next_ranking(record: dict) -> list[str]:
    """The control: FPL's own expected points over the same squads."""
    elements, _, _, picks, _ = snapshot_mod.replay_projection_inputs(record)
    totals = {}
    for manager, payload in picks.items():
        hits = (payload.get("entry_history") or {}).get("event_transfers_cost", 0) or 0
        totals[manager] = sum(
            float((elements.get(p["element"]) or {}).get("ep_next") or 0) * p["multiplier"]
            for p in payload["picks"] if p["multiplier"] > 0
        ) - hits
    return sorted(totals, key=lambda m: (-totals[m], m))


def settled_rows(data: dict) -> dict[int, dict]:
    return {int(row["gw"]): row for row in data.get("gameweeks", [])}


def run(root: Path, data: dict, only_gw: int | None = None) -> dict:
    rows = settled_rows(data)
    report = {"gameweeks": [], "skipped": []}

    for gw in frozen_gameweeks(root):
        if only_gw is not None and gw != only_gw:
            continue
        record = snapshot_mod.load_projection_inputs(gw, root=root)
        if gw not in rows:
            report["skipped"].append({"gw": gw, "why": "not settled yet"})
            continue
        actual = actual_order(rows[gw])
        if len(actual) < 2:
            report["skipped"].append({"gw": gw, "why": "no scores to rank"})
            continue
        report["gameweeks"].append({
            "gw": gw,
            "ours": score_ranking(our_ranking(record), actual),
            "ep_next": score_ranking(ep_next_ranking(record), actual),
        })

    for gw in sorted(rows):
        if not snapshot_mod.projection_path(gw, root=root).exists():
            report["skipped"].append({"gw": gw, "why": "no frozen inputs"})

    report["total"] = {
        "ours": aggregate([g["ours"] for g in report["gameweeks"]]),
        "ep_next": aggregate([g["ep_next"] for g in report["gameweeks"]]),
    }
    return report


def render(report: dict) -> str:
    lines = []
    if not report["gameweeks"]:
        lines.append("Nothing to score yet.")
        lines.append("")
        lines.append("The projection is only markable for gameweeks that have BOTH frozen")
        lines.append("inputs and a settled result. predict.py started freezing inputs when")
        lines.append("this tool was added, so the record begins from the next call it makes.")
    else:
        lines.append(f"{'GW':>4}  {'':8}{'Spearman':>10}{'pairwise':>10}{'exact':>7}{'podium':>8}{'pair':>6}")
        for row in report["gameweeks"]:
            for name in ("ours", "ep_next"):
                s = row[name]
                gw = f"{row['gw']:>4}" if name == "ours" else "    "
                lines.append(
                    f"{gw}  {name:8}{s['spearman']:+10.3f}"
                    f"{s['pairwise']:9.0%} {'yes' if s['exact'] else '—':>6}"
                    f"{'yes' if s['podium'] else '—':>8}{'yes' if s['pair'] else '—':>6}"
                )
        lines.append("")
        for name in ("ours", "ep_next"):
            t = report["total"][name]
            lines.append(
                f"  season  {name:8}{t['spearman']:+10.3f}{t['pairwise']:9.0%} "
                f"{t['exact']:>5} {t['podium']:>7} {t['pair']:>5}"
                f"   over {t['gameweeks']} gameweek(s)"
            )
        lines.append("")
        lines.append("`ep_next` is FPL's own expected points over the same squads — the")
        lines.append("control. Our projection has to beat it to be worth running.")

    for skip in report["skipped"]:
        lines.append(f"  GW{skip['gw']:02d} not scored — {skip['why']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--gw", type=int, help="score a single gameweek")
    parser.add_argument("--raw", type=Path, default=RAW, help="where the frozen inputs live")
    parser.add_argument("--data", type=Path, default=DATA_JSON)
    args = parser.parse_args()

    if not args.data.exists():
        print(f"{args.data} is missing — run build.py first", file=sys.stderr)
        return 1
    report = run(args.raw, json.loads(args.data.read_text()), args.gw)
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
