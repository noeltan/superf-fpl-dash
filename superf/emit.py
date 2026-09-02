"""Assemble ``data.json`` in exactly the shape the prototype reads.

The prototype's ``DATA`` object is the canonical schema (spec §5 is updated to
match it in ``SCHEMA.md``). Every key it reads is emitted here, key for key.

Money crosses this boundary as ringgit, because that is what the view formats.
Everything upstream of here is integer sen.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from . import copy as copytext
from . import corrections as corrections_mod
from . import summary as summary_mod
from .config import (
    CURRENCY,
    EXPECTED_GAMEWEEKS,
    MONTHLY_SPLIT,
    MONTHLY_STAKE_PER_GW_RM,
    SEASON_SPLIT,
    SEASON_STAKE_RM,
    SEASON,
    TZ_NAME,
    TZ_OFFSET_HOURS,
    WEEKLY_SPLIT,
    WEEKLY_STAKE_RM,
)
from .copy import ordinal
from .ledger import Gameweek, Settlement
from .money import LedgerError, advertised_net, minimum_transfers, rm_to_sen, sen_to_rm
from .tiebreak import ladder as tiebreak_ladder


def _rm(sen: int) -> float | int:
    return sen_to_rm(sen)


def stakes_block(n: int, month_gameweeks: int) -> dict:
    """§7.2F — generated from ``N``, never hardcoded prose.

    ``monthly`` describes the month bucket currently in view: its length drives
    the stake, so a 2-gameweek August and a 6-gameweek December differ here.
    """
    weekly_stake = rm_to_sen(WEEKLY_STAKE_RM)
    monthly_stake = rm_to_sen(MONTHLY_STAKE_PER_GW_RM * month_gameweeks)
    season_stake = rm_to_sen(SEASON_STAKE_RM)
    return {
        "weekly": {
            "stake": WEEKLY_STAKE_RM,
            "pot": _rm(weekly_stake * n),
            "split": list(WEEKLY_SPLIT),
            "net": [_rm(v) for v in advertised_net(weekly_stake, n, WEEKLY_SPLIT)],
        },
        "monthly": {
            "stake_per_gw": MONTHLY_STAKE_PER_GW_RM,
            "gameweeks": month_gameweeks,
            "stake": _rm(monthly_stake),
            "pot": _rm(monthly_stake * n),
            "split": list(MONTHLY_SPLIT),
            "net": [_rm(v) for v in advertised_net(monthly_stake, n, MONTHLY_SPLIT)],
        },
        "season": {
            "stake": SEASON_STAKE_RM,
            "pot": _rm(season_stake * n),
            "split": list(SEASON_SPLIT),
            "net": [_rm(v) for v in advertised_net(season_stake, n, SEASON_SPLIT)],
        },
    }


def rules_block(n: int, month_buckets, total_gameweeks: int = EXPECTED_GAMEWEEKS) -> dict:
    """Everything the rules tab states, derived from N and the real calendar.

    The tab explains the money to eight-to-twelve people who did not write it,
    which makes it exactly the place a hardcoded figure would do the most
    damage: it would read as authoritative and be wrong from the moment the
    league changed size. So every number it shows is computed here, from the
    same ``advertised_net`` the stakes block uses.
    """
    weekly_stake = rm_to_sen(WEEKLY_STAKE_RM)
    season_stake = rm_to_sen(SEASON_STAKE_RM)

    months = []
    for bucket in month_buckets:
        gameweeks = len(bucket["gameweeks"])
        stake = rm_to_sen(MONTHLY_STAKE_PER_GW_RM * gameweeks)
        months.append({
            "month": bucket["month"],
            "gameweeks": gameweeks,
            "stake": _rm(stake),
            "pot": _rm(stake * n),
            "net": [_rm(v) for v in advertised_net(stake, n, MONTHLY_SPLIT)],
        })

    # The floor table (§3.2, generalised). Shown around the current league size
    # so the reader can see which side of the line we are on and how far.
    sizes = sorted({3, 4, 6, 7, 8, n, n + 1, 12})
    floors = []
    for size in sizes:
        if size < 2:
            continue
        season_third = advertised_net(season_stake, size, SEASON_SPLIT)
        weekly_second = advertised_net(weekly_stake, size, WEEKLY_SPLIT)
        floors.append({
            "n": size,
            "is_us": size == n,
            "season_third": _rm(season_third[2]) if len(season_third) > 2 else None,
            "weekly_second": _rm(weekly_second[1]) if len(weekly_second) > 1 else None,
        })

    best_weekly = advertised_net(weekly_stake, n, WEEKLY_SPLIT)[0] * total_gameweeks
    best_monthly = sum(
        advertised_net(rm_to_sen(MONTHLY_STAKE_PER_GW_RM * len(b["gameweeks"])), n, MONTHLY_SPLIT)[0]
        for b in month_buckets
    )
    best_season = advertised_net(season_stake, n, SEASON_SPLIT)[0]

    return {
        "gameweek_cost": WEEKLY_STAKE_RM + MONTHLY_STAKE_PER_GW_RM,
        "weekly_stake": WEEKLY_STAKE_RM,
        "monthly_stake_per_gw": MONTHLY_STAKE_PER_GW_RM,
        "season_stake": SEASON_STAKE_RM,
        "months": months,
        "floors": floors,
        "tiebreak": tiebreak_ladder(),
        "best_breakdown": {
            "weekly": _rm(best_weekly),
            "monthly": _rm(best_monthly),
            "season": _rm(best_season),
        },
        # N-1 payments clear any book; naive pairwise settlement is N(N-1)/2.
        "max_payments": max(n - 1, 0),
        "naive_payments": n * (n - 1) // 2,
    }


def exposure_block(n: int, total_gameweeks: int = EXPECTED_GAMEWEEKS) -> dict:
    """§3.4 / §7.2F — "how much can this cost me?", derived from N.

    §3.4 says RM480 staked at N=8; §3.7's closing "RM380 -> RM420" line is stale,
    written before the season stake was raised from RM40 to RM100. Deriving it
    means the two can never disagree again.
    """
    weekly_stake = rm_to_sen(WEEKLY_STAKE_RM)
    monthly_stake_total = rm_to_sen(MONTHLY_STAKE_PER_GW_RM) * total_gameweeks
    season_stake = rm_to_sen(SEASON_STAKE_RM)

    staked = weekly_stake * total_gameweeks + monthly_stake_total + season_stake

    weekly_best = advertised_net(weekly_stake, n, WEEKLY_SPLIT)[0] * total_gameweeks
    # Winning every month is worth (0.7N - 1) x the season's total monthly stake,
    # whatever shape the buckets happen to be.
    monthly_best = (
        advertised_net(monthly_stake_total, n, MONTHLY_SPLIT)[0]
        if monthly_stake_total
        else 0
    )
    season_best = advertised_net(season_stake, n, SEASON_SPLIT)[0]

    return {
        "staked": _rm(staked),
        "best": _rm(weekly_best + monthly_best + season_best),
        "worst": _rm(-staked),
    }


def _place_counts(settlement: Settlement, managers: Sequence[str]) -> tuple[dict, dict]:
    """Podium finishes (display only, §2) and weeks won."""
    podiums = {m: 0 for m in managers}
    weeks_won = {m: 0 for m in managers}
    for ranking in settlement.rankings.values():
        if not ranking.groups:
            continue
        for manager in ranking.winners:
            weeks_won[manager] = weeks_won.get(manager, 0) + 1
        for manager in managers:
            place = ranking.place_of(manager)
            if place is not None and place <= 3:
                podiums[manager] = podiums.get(manager, 0) + 1
    return podiums, weeks_won


def _ranked_ids(settlement: Settlement, managers: Sequence[str]) -> list[str]:
    if settlement.season_ranking and settlement.season_ranking.groups:
        ordered = [m for group in settlement.season_ranking.groups for m in group]
        return ordered + [m for m in managers if m not in ordered]
    return list(managers)


def build_payload(
    *,
    generated_at: str,
    league_name: str,
    league_id: int,
    managers: Sequence[Mapping],
    teams: Mapping[int, Mapping],
    events: Sequence[Mapping],
    breaks: Sequence[Mapping],
    month_buckets: Sequence[Mapping],
    fixtures_by_gw: Mapping[int, Sequence[Mapping]],
    pl_table: Sequence[Mapping],
    gameweeks: Mapping[int, Gameweek],
    settlement: Settlement,
    current: Mapping,
    settled_dates: Mapping[int, str] | None = None,
    corrections: Sequence[Mapping] | None = None,
    provisional: Mapping | None = None,
    you: str | None = None,
) -> dict:
    ids = [m["id"] for m in managers]
    n = len(ids)
    settled_dates = settled_dates or {}
    corrections = list(corrections or [])
    corrections_mod.validate(corrections, ids)
    final_gws = sorted(gw for gw, g in gameweeks.items() if g.is_final)
    settled_through = final_gws[-1] if final_gws else 0
    you = you or (ids[0] if ids else "")

    bucket_by_month = {b["month"]: list(b["gameweeks"]) for b in month_buckets}

    # --- season points, standings -------------------------------------------
    totals_points = {m: 0 for m in ids}
    for gw in final_gws:
        for manager, score in gameweeks[gw].scores.items():
            if score.active:
                totals_points[manager] += score.net_points

    rank = _ranked_ids(settlement, ids)
    leader_points = totals_points[rank[0]] if rank else 0
    behind = {m: leader_points - totals_points[m] for m in ids}

    # Rank before the most recent settled gameweek (§5 `rank_prev`).
    #
    # Empty until two gameweeks have settled. With one, "before" is a book of
    # zeros sorted by id — and the movement arrows on the league table would
    # read that as Sam up six and Boon down twelve on the one week nobody has
    # moved from anywhere.
    rank_prev: dict[str, int] = {}
    if len(final_gws) >= 2:
        prior = {m: 0 for m in ids}
        for gw in final_gws[:-1]:
            for manager, score in gameweeks[gw].scores.items():
                if score.active:
                    prior[manager] += score.net_points
        ordered_prior = sorted(ids, key=lambda m: (-prior[m], m))
        rank_prev = {m: i + 1 for i, m in enumerate(ordered_prior)}

    # --- gameweeks -----------------------------------------------------------
    gameweek_rows = []
    for gw in final_gws:
        gameweek = gameweeks[gw]
        ranking = settlement.rankings.get(gw)
        scores = {}
        for manager in ids:
            score = gameweek.scores.get(manager)
            if score is None or not score.active:
                # A manager who was not in the league yet. Kept present so the
                # view can index every manager in every settled gameweek
                # without a lookup miss (§3.8.6 joiner safety).
                scores[manager] = {
                    "points": None, "hits": 0, "transfers": 0, "chip": None,
                    "did_not_set": False, "active": False,
                }
            else:
                scores[manager] = {
                    "points": score.net_points,
                    "hits": score.hits,
                    "transfers": score.transfers,
                    "chip": score.chip,
                    "did_not_set": score.did_not_set,
                    "active": True,
                }
        pot_sen = rm_to_sen(WEEKLY_STAKE_RM) * len(gameweek.active_managers)
        gameweek_rows.append(
            {
                "gw": gw,
                "month": gameweek.month,
                "note": gameweek.note,
                "scores": scores,
                "winners": ranking.winners if ranking else [],
                # Second place is paid on the weekly pot (70/30), so the view
                # needs to name it — a card showing only the winner would hide
                # a credit somebody is owed.
                "runners_up": ranking.runners_up if ranking else [],
                # What this gameweek actually paid, in sen, read straight off the
                # settled ledger. NOT the advertised `stakes.weekly.net`: that is
                # the figure at today's league size, and a gameweek played before
                # somebody joined paid a smaller pot. Showing the advertised
                # number against an older row would print money nobody received.
                "winner_net": (
                    settlement.weekly.get(gw, {}).get(ranking.winners[0])
                    if ranking and ranking.winners else None
                ),
                "runner_up_net": (
                    settlement.weekly.get(gw, {}).get(ranking.runners_up[0])
                    if ranking and ranking.runners_up else None
                ),
                "pot": _rm(pot_sen) if gameweek.pays_pot else 0,
                "tiebreak": ranking.tiebreak if ranking else None,
                # §11.4 — a permanent, timestamped note when confirmed bonus
                # flipped the pot. That is precisely the moment people accuse
                # each other of cheating.
                "bonus_change": gameweek.bonus_change,
            }
        )

    # --- months --------------------------------------------------------------
    # Only *complete* months go in `months[]`: the view labels the last entry
    # SETTLED, so an in-progress bucket would lie. The running month lives in
    # `month_current` instead.
    month_rows = []
    for bucket in month_buckets:
        month, bucket_gws = bucket["month"], list(bucket["gameweeks"])
        played = [gw for gw in bucket_gws if gw in final_gws]
        if not played or len(played) != len(bucket_gws):
            continue
        ledger = settlement.monthly.get(month)
        ranking = settlement.month_rankings.get(month)
        if not ledger or not ranking:
            continue

        month_totals = {}
        for gw in played:
            for manager, score in gameweeks[gw].scores.items():
                if score.active:
                    month_totals[manager] = month_totals.get(manager, 0) + score.net_points
        order = [m for group in ranking.groups for m in group]
        stake_sen = rm_to_sen(MONTHLY_STAKE_PER_GW_RM * len(bucket_gws))
        pot_sen = sum(
            rm_to_sen(MONTHLY_STAKE_PER_GW_RM) * len(gameweeks[gw].active_managers)
            for gw in played
            if gameweeks[gw].pays_pot
        )
        nets = [ledger.get(m, 0) for m in order[:2]]
        margin = (
            month_totals.get(order[0], 0) - month_totals.get(order[1], 0)
            if len(order) > 1
            else 0
        )
        month_rows.append(
            {
                "month": month,
                "gameweeks": bucket_gws,
                "played": played,
                "complete": True,
                "stake": _rm(stake_sen),
                "pot": _rm(pot_sen),
                "split": list(MONTHLY_SPLIT),
                "net": [_rm(v) for v in nets],
                "totals": month_totals,
                "order": order,
                # `winners` is a list for the same reason `gameweeks[].winners`
                # is: a level-5 tie for the month splits both paid shares, and
                # `order[0]` alone would name one of them as if it had won
                # outright. `runners_up` is empty when a tie swallowed second.
                "winners": ranking.winners,
                "runners_up": ranking.runners_up,
                # SEN, per manager, straight off the settled ledger. A view or a
                # message that charges "everyone else the stake" is wrong for
                # a manager who joined mid-bucket (stake is per gameweek
                # active, §3.8.6) and for the third name in a three-way tie.
                "ledger": {m: ledger.get(m, 0) for m in ids if m in ledger},
                "gap_to_first": margin,
                "callout": copytext.settled_month_callout(
                    month,
                    _display(managers, order[0]),
                    month_totals.get(order[0], 0),
                    _rm(nets[0]) if nets else 0,
                    _display(managers, order[1]) if len(order) > 1 else "-",
                    month_totals.get(order[1], 0) if len(order) > 1 else 0,
                    _rm(nets[1]) if len(nets) > 1 else 0,
                    _rm(stake_sen),
                    max(len(order) - 2, 0),
                    len(bucket_gws),
                ),
            }
        )

    # --- the running month ---------------------------------------------------
    current_month = _current_month(month_buckets, final_gws, current)
    month_current = None
    if current_month:
        month, bucket_gws = current_month["month"], current_month["gameweeks"]
        played = [gw for gw in bucket_gws if gw in final_gws]
        stake_sen = rm_to_sen(MONTHLY_STAKE_PER_GW_RM * len(bucket_gws))
        nets = advertised_net(stake_sen, n, MONTHLY_SPLIT)
        opens_gw = bucket_gws[0]
        if played and len(played) < len(bucket_gws):
            note = copytext.month_in_progress_note(
                month, len(played), len(bucket_gws), _rm(stake_sen * n)
            )
        else:
            note = copytext.month_opens_note(
                month, len(bucket_gws), opens_gw, _rm(stake_sen), _rm(stake_sen * n)
            )
        month_current = {
            "month": month,
            "gameweeks": len(bucket_gws),
            "opens_gw": opens_gw,
            "stake": _rm(stake_sen),
            "pot": _rm(stake_sen * n),
            "net": [_rm(v) for v in nets],
            "note": note,
        }

    # --- ledger --------------------------------------------------------------
    weekly_totals = {m: 0 for m in ids}
    for gw_ledger in settlement.weekly.values():
        for manager, value in gw_ledger.items():
            weekly_totals[manager] += value
    monthly_totals = {m: 0 for m in ids}
    for month_ledger in settlement.monthly.values():
        for manager, value in month_ledger.items():
            monthly_totals[manager] += value

    last_gw = final_gws[-1] if final_gws else None
    delta_source = {m: 0 for m in ids}
    if last_gw is not None:
        for manager, value in settlement.weekly.get(last_gw, {}).items():
            delta_source[manager] += value
        for month, month_ledger in settlement.monthly.items():
            if bucket_by_month.get(month, [])[-1:] == [last_gw]:
                for manager, value in month_ledger.items():
                    delta_source[manager] += value

    # §3.9.4 — adjusting entries, applied on top of the settled pots.
    correction_net = corrections_mod.apply(corrections, ids)

    ledger_block = {}
    for manager in ids:
        statement = _statement_for(
            manager, ids, final_gws, gameweeks, settlement, month_rows,
            settled_dates, corrections,
        )
        accrued = settlement.totals.get(manager, 0) + correction_net.get(manager, 0)
        ledger_block[manager] = {
            # SEN, not ringgit (§3.8.1). Only the ledger, statement and
            # settlement are sen — stakes and exposure stay in RM as §5 has them.
            "weekly": weekly_totals[manager],
            "monthly": monthly_totals[manager],
            "accrued": accrued,
            "projected_season": settlement.season.get(
                manager, settlement.projected_season.get(manager, 0)
            ),
            "delta_last_gw": delta_source[manager],
            # Per-gameweek weekly amount, matching §5's own [-500, -500] example
            # and the prototype's reading — not a running cumulative total.
            "by_gameweek": [settlement.weekly.get(gw, {}).get(manager, 0) for gw in final_gws],
            "statement": statement,
        }
        if statement and statement[-1]["balance"] != accrued:
            raise LedgerError(
                f"{manager}'s statement ends at {statement[-1]['balance']} sen but the "
                f"accrued balance is {accrued} sen — every row must trace (§3.9.2)"
            )

    # §3.9.3 — who pays whom. A preview until GW38, the deliverable after it.
    balances = {m: ledger_block[m]["accrued"] for m in ids}
    settlement_block = {
        "settled": settlement.season_is_final,
        "payments": minimum_transfers(balances),
    }
    if len(settlement_block["payments"]) > max(len(ids) - 1, 0):
        raise LedgerError(
            f"settlement produced {len(settlement_block['payments'])} payments, "
            f"which exceeds the N-1 bound of {len(ids) - 1}"
        )

    # --- the provisional round (§11.1 / §11.4) -------------------------------
    # Display only, never money: the round is at full time but not closed, so
    # nothing here is booked and the ledger never reads it.
    #
    # These are NOT the scores the ledger will settle on, and the gap is not
    # small: confirmed bonus, auto-subs and the vice-captain fallback all land
    # at the Final transition, and in GW1 that moved one manager 11 points and
    # five places. The page says so — this block exists to show a standing
    # while FPL confirms, not to predict the settled one.
    #
    # Emitted only while the named round is actually provisional, so a settled
    # gameweek's leftover raw/ record cannot resurface.
    provisional_block = None
    if (
        provisional
        and current.get("state") == "provisional"
        and int(provisional.get("gw", 0)) == int(current.get("gameweek", 0))
    ):
        raw_scores = provisional.get("scores") or {}
        prov_scores = {
            m: {"points": int(v.get("points", 0)), "hits": int(v.get("hits", 0))}
            for m, v in raw_scores.items()
            if m in ids
        }
        prov_gw = int(provisional["gw"])
        order = sorted(prov_scores, key=lambda m: (-prov_scores[m]["points"], m))
        field = len(prov_scores) or n
        stake_sen = rm_to_sen(WEEKLY_STAKE_RM)

        # The month bucket this round sits in, carried to date. Without it the
        # money tab's pot card falls back to "opens GW<n>" over a round that has
        # been played. Aggregated here, never in the view: the page formats
        # money, it does not sum it.
        prov_month = None
        bucket = next(
            (b for b in month_buckets if prov_gw in b["gameweeks"]), None
        )
        if bucket and prov_scores:
            bucket_gws = list(bucket["gameweeks"])
            settled_in_bucket = [gw for gw in bucket_gws if gw in final_gws]
            month_totals = {m: 0 for m in prov_scores}
            for gw in settled_in_bucket:
                for manager, score in gameweeks[gw].scores.items():
                    if manager in month_totals and score.active:
                        month_totals[manager] += score.net_points
            for manager, row in prov_scores.items():
                month_totals[manager] += row["points"]
            month_stake_sen = rm_to_sen(MONTHLY_STAKE_PER_GW_RM * len(bucket_gws))
            prov_month = {
                "month": bucket["month"],
                "gameweeks": len(bucket_gws),
                "played": settled_in_bucket + [prov_gw],
                "remaining": len(bucket_gws) - len(settled_in_bucket) - 1,
                "stake": _rm(month_stake_sen),
                "pot": _rm(month_stake_sen * field),
                "net": [
                    _rm(v) for v in advertised_net(month_stake_sen, field, MONTHLY_SPLIT)
                ],
                "totals": month_totals,
                "order": sorted(month_totals, key=lambda m: (-month_totals[m], m)),
            }

        provisional_block = {
            "gw": prov_gw,
            "observed_at": provisional.get("observed_at"),
            "leader": order[0] if order else provisional.get("leader"),
            "runner_up": order[1] if len(order) > 1 else None,
            "margin": (
                prov_scores[order[0]]["points"] - prov_scores[order[1]]["points"]
                if len(order) > 1 else None
            ),
            "pot": _rm(stake_sen * field),
            # What 1st and 2nd would take if the standing held. Advertised,
            # not accrued — no row of this exists in the ledger, and the
            # standing itself still has bonus and auto-subs to come.
            "net": [_rm(v) for v in advertised_net(stake_sen, field, WEEKLY_SPLIT)],
            "scores": prov_scores,
            "order": order,
            "month": prov_month,
        }

    podiums, weeks_won = _place_counts(settlement, ids)

    active_totals = [totals_points[m] for m in ids if totals_points[m] or final_gws]
    avg_points = round(sum(active_totals) / len(active_totals), 1) if active_totals else 0
    high = None
    for gw in final_gws:
        for manager, score in gameweeks[gw].scores.items():
            if score.active and (high is None or score.net_points > high["points"]):
                high = {"gw": gw, "manager": manager, "points": score.net_points}

    month_for_stakes = len(current_month["gameweeks"]) if current_month else 0

    payload = {
        "generated_at": generated_at,
        "league": {"id": league_id, "name": league_name, "currency": CURRENCY, "players": n},
        "current": dict(current),
        "tz": {"name": TZ_NAME, "offset": TZ_OFFSET_HOURS},
        "stakes": stakes_block(n, month_for_stakes),
        "exposure": exposure_block(n),
        # Everything the rules tab states, derived rather than written down.
        "rules": rules_block(n, month_buckets),
        "managers": [dict(m) for m in managers],
        "teams": {
            str(team_id): {
                "id": team_id, "name": team["name"], "short": team["short_name"]
            }
            for team_id, team in sorted(teams.items())
        },
        "events": [dict(e) for e in events],
        "breaks": [dict(b) for b in breaks],
        "month_buckets": [dict(b) for b in month_buckets],
        "fixtures": {
            str(gw): [dict(f) for f in rows] for gw, rows in sorted(fixtures_by_gw.items())
        },
        "pl_table": [dict(r) for r in pl_table],
        "gameweeks": gameweek_rows,
        "provisional": provisional_block,
        "months": month_rows,
        "month_current": month_current,
        "totals": totals_points,
        "rank": rank,
        "rank_prev": rank_prev,
        "behind": behind,
        "ledger": ledger_block,
        "corrections": [dict(c) for c in corrections],
        "settlement": settlement_block,
        "podiums": podiums,
        "weeks_won": weeks_won,
        "stats": {"avg_points": avg_points, "high": high},
        "settled": {
            "through_gw": settled_through,
            "projected": (
                "season pot settled"
                if settlement.season_is_final
                else "season pot projected, not banked"
            ),
        },
        "checks": {
            "zero_sum": sum(ledger_block[m]["accrued"] for m in ids) == 0,
            "gameweeks_expected": EXPECTED_GAMEWEEKS,
            "gameweeks_present": len(events),
        },
    }

    # Composed last, from the finished payload: the message that goes out to
    # the group chat has to quote the page, not a second derivation of it.
    payload["summary"] = summary_mod.build(payload)
    return payload


def _statement_for(
    manager: str,
    ids: Sequence[str],
    final_gws: Sequence[int],
    gameweeks: Mapping[int, Gameweek],
    settlement: Settlement,
    month_rows: Sequence[Mapping],
    settled_dates: Mapping[int, str],
    corrections: Sequence[Mapping],
) -> list[dict]:
    """§3.9.2 — one row per settled event, oldest first, with a running balance.

    A manager who disputes their total must be able to find the single row they
    disagree with. That is the whole design goal, so every row traces to exactly
    one settled gameweek, month, or adjusting entry.
    """
    rows: list[dict] = []

    for gw in final_gws:
        gameweek = gameweeks[gw]
        score = gameweek.scores.get(manager)
        if score is None or not score.active:
            continue
        amount = settlement.weekly.get(gw, {}).get(manager)
        if amount is None:
            continue
        ranking = settlement.rankings.get(gw)
        place = ranking.place_of(manager) if ranking else None
        field = len(gameweek.active_managers)
        if score.did_not_set:
            detail = "never set team, 0 pts"
        elif manager in (ranking.winners if ranking else []):
            detail = f"{score.net_points} pts, 1st of {field}, top score"
        else:
            detail = f"{score.net_points} pts, {ordinal(place or field)} of {field}"
        # The weekly pot pays more than one place, so name the share that
        # produced the amount — otherwise a credit on a row that reads "2nd of
        # 8" is a figure nobody can decompose (§3.9.2).
        if not score.did_not_set and place and place <= len(WEEKLY_SPLIT) > 1:
            detail += f" — {round(WEEKLY_SPLIT[place - 1] * 100)}% share"
        rows.append({
            "date": settled_dates.get(gw, ""), "type": "weekly", "gw": gw,
            "detail": detail, "amount": amount, "_sort": (settled_dates.get(gw, ""), 0, gw),
        })

    for month in month_rows:
        ledger = settlement.monthly.get(month["month"])
        if not ledger or manager not in ledger:
            continue
        order = month["order"]
        place = order.index(manager) + 1 if manager in order else len(order)
        points = month["totals"].get(manager, 0)
        name = copytext.month_name(month["month"])
        detail = f"{points} pts, {ordinal(place)} in {name}"
        if place <= len(MONTHLY_SPLIT):
            detail += f" — {round(MONTHLY_SPLIT[place - 1] * 100)}% share"
        last_gw = month["gameweeks"][-1]
        rows.append({
            "date": settled_dates.get(last_gw, ""), "type": "monthly",
            "month": month["month"], "detail": detail, "amount": ledger[manager],
            "_sort": (settled_dates.get(last_gw, ""), 1, last_gw),
        })

    # Once GW38 is Final the season pot moves from projected into the book, so
    # it needs a row like any other event — otherwise the statement stops short
    # of the balance somebody is being asked to pay (§3.9.2).
    if settlement.season_is_final and manager in settlement.season:
        ranking = settlement.season_ranking
        place = ranking.place_of(manager) if ranking else None
        points = sum(
            gameweeks[gw].scores[manager].net_points
            for gw in final_gws
            if gameweeks[gw].scores.get(manager) and gameweeks[gw].scores[manager].active
        )
        detail = f"{points} pts, {ordinal(place or 0)} overall"
        if place and place <= len(SEASON_SPLIT):
            detail += f" — {round(SEASON_SPLIT[place - 1] * 100)}% share"
        last_gw = final_gws[-1] if final_gws else 0
        rows.append({
            "date": settled_dates.get(last_gw, ""), "type": "season",
            "detail": detail, "amount": settlement.season[manager],
            "_sort": (settled_dates.get(last_gw, ""), 2, last_gw),
        })

    for row in corrections_mod.statement_rows(corrections, manager):
        rows.append({**row, "_sort": (row["date"], 3, row.get("affects_gw") or 0)})

    rows.sort(key=lambda r: r.pop("_sort"))
    balance = 0
    for row in rows:
        balance += row["amount"]
        row["balance"] = balance
    return rows


def _display(managers: Sequence[Mapping], manager_id: str) -> str:
    for manager in managers:
        if manager["id"] == manager_id:
            return manager.get("short") or manager["display_name"]
    return manager_id


def _current_month(
    month_buckets: Sequence[Mapping], final_gws: Sequence[int], current: Mapping
) -> dict | None:
    """The bucket in play: the one holding next_gw, else the first incomplete one."""
    next_gw = current.get("next_gw") or current.get("gameweek") or 1
    for bucket in month_buckets:
        if next_gw in bucket["gameweeks"]:
            return dict(bucket)
    for bucket in month_buckets:
        if not all(gw in final_gws for gw in bucket["gameweeks"]):
            return dict(bucket)
    return dict(month_buckets[-1]) if month_buckets else None
