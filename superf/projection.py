"""Spec §12.2 Layer 1 — the deterministic projection.

    xP(player)  = P(plays) x minutes_factor
                x [ position_baseline
                  + xGI_per_90 x fixture_adjustment(difficulty, home/away)
                  + clean_sheet_expectation (DEF/GK)
                  + form_weighting ]
    xP(manager) = sum(xP of the XI, each times its multiplier) - transfer_hits

This is arithmetic, reproducible and testable, and it is the *only* source of
numbers the model in Layer 2 is allowed to quote. A language model asked to
estimate points produces confident, unfalsifiable nonsense; this produces
something you can check.

Multiplying by each pick's own multiplier covers the captain (x2), the triple
captain (x3) and the bench boost (all fifteen at x1) without special cases.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

# FPL scoring, by element_type: 1 GK, 2 DEF, 3 MID, 4 FWD.
GOAL_POINTS = {1: 10, 2: 6, 3: 5, 4: 4}
CLEAN_SHEET_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_POINTS = 3
APPEARANCE_POINTS = 2.0

# An average fixture is difficulty 3. Each step either side moves expected
# attacking return by 12%, and home advantage is worth 6%.
DIFFICULTY_STEP = 0.12
HOME_ADVANTAGE = 0.06
ROTATION_PRIOR = 0.60  # a player with no starts on record is a rotation risk


@dataclass
class PlayerProjection:
    element: int
    name: str
    team: int
    position: int
    xp: float
    p_plays: float
    fixtures: int = 1


@dataclass
class ManagerProjection:
    manager: str
    xp: float
    captain: str
    captain_xp: float
    hits: int
    concentration: dict
    chip: str | None = None
    players: list[PlayerProjection] = field(default_factory=list)

    def to_contract(self) -> dict:
        """The §12.4 `projections[]` shape."""
        return {
            "manager": self.manager,
            "xp": round(self.xp, 1),
            "captain": self.captain,
            "captain_xp": round(self.captain_xp, 1),
            "hits": self.hits,
            "concentration": self.concentration,
        }


def p_plays(element: Mapping) -> float:
    """Probability the player features at all."""
    status = element.get("status", "a")
    if status in ("i", "s", "u", "n"):
        return 0.0
    chance = element.get("chance_of_playing_next_round")
    if chance is not None:
        return max(0.0, min(1.0, float(chance) / 100.0))
    return 0.5 if status == "d" else 1.0


def minutes_factor(element: Mapping) -> float:
    """Expected share of 90 minutes, from starts and minutes on record."""
    starts = int(element.get("starts") or 0)
    minutes = int(element.get("minutes") or 0)
    if starts <= 0 or minutes <= 0:
        return ROTATION_PRIOR
    return max(0.15, min(1.0, (minutes / starts) / 90.0))


def fixture_adjustment(difficulty: int | None, is_home: bool) -> float:
    """Easier opponents and home ties raise expected attacking return."""
    level = 3 if difficulty is None else int(difficulty)
    adjustment = 1.0 + (3 - level) * DIFFICULTY_STEP
    adjustment *= 1.0 + (HOME_ADVANTAGE if is_home else -HOME_ADVANTAGE)
    return max(0.4, adjustment)


def clean_sheet_probability(xgc_per_90: float, difficulty: int | None, is_home: bool) -> float:
    """Poisson P(0 conceded), with the opponent's difficulty moving the rate."""
    rate = float(xgc_per_90 or 0.0)
    if rate <= 0:
        rate = 1.35  # league-average goals conceded, for a player with no record
    level = 3 if difficulty is None else int(difficulty)
    rate *= 1.0 + (level - 3) * DIFFICULTY_STEP
    rate *= 1.0 - (HOME_ADVANTAGE if is_home else -HOME_ADVANTAGE)
    return math.exp(-max(0.05, rate))


def form_weighting(element: Mapping) -> float:
    """A modest nudge for players in form. Never negative — form is a bonus,
    not a penalty, and the structural terms already carry the base rate."""
    form = float(element.get("form") or 0.0)
    return 0.25 * max(0.0, form - 2.0)


def project_player(element: Mapping, difficulty: int | None, is_home: bool) -> float:
    """xP for one player in one fixture."""
    plays = p_plays(element)
    if plays <= 0:
        return 0.0
    position = int(element.get("element_type", 3))

    xg = float(element.get("expected_goals_per_90") or 0.0)
    xa = float(element.get("expected_assists_per_90") or 0.0)
    attacking = (xg * GOAL_POINTS.get(position, 5) + xa * ASSIST_POINTS)
    attacking *= fixture_adjustment(difficulty, is_home)

    defensive = 0.0
    if position in (1, 2, 3):
        cs = clean_sheet_probability(
            float(element.get("expected_goals_conceded_per_90") or 0.0), difficulty, is_home
        )
        defensive += cs * CLEAN_SHEET_POINTS.get(position, 0)
    if position == 1:
        defensive += float(element.get("saves_per_90") or 0.0) / 3.0

    bracket = APPEARANCE_POINTS + attacking + defensive + form_weighting(element)

    # Thin record (a promoted club's new signing, or the opening weeks of a
    # season): fall back to FPL's own expected-points figure rather than
    # projecting everyone onto the appearance points alone.
    if attacking == 0 and defensive == 0:
        ep_next = float(element.get("ep_next") or 0.0)
        if ep_next > 0:
            bracket = max(bracket, ep_next)

    return plays * minutes_factor(element) * bracket


def project_manager(
    manager_id: str,
    picks_payload: Mapping,
    elements: Mapping[int, Mapping],
    fixtures_by_team: Mapping[int, Sequence[tuple[int | None, bool]]],
    teams: Mapping[int, Mapping],
) -> ManagerProjection:
    """xP for one manager's gameweek, plus the framing Layer 2 needs."""
    picks = picks_payload.get("picks", []) or []
    history = picks_payload.get("entry_history", {}) or {}
    hits = int(history.get("event_transfers_cost", 0) or 0)
    chip = picks_payload.get("active_chip")

    total = 0.0
    captain_name, captain_xp = "-", 0.0
    club_counts: dict[int, int] = {}
    players: list[PlayerProjection] = []

    for pick in picks:
        multiplier = int(pick.get("multiplier", 0) or 0)
        if multiplier <= 0:
            continue
        element = elements.get(int(pick["element"]))
        if not element:
            continue
        team_id = int(element["team"])
        club_counts[team_id] = club_counts.get(team_id, 0) + 1

        matches = list(fixtures_by_team.get(team_id, []))
        player_xp = sum(project_player(element, d, home) for d, home in matches)

        players.append(
            PlayerProjection(
                element=int(pick["element"]),
                name=element.get("web_name", "?"),
                team=team_id,
                position=int(element.get("element_type", 3)),
                xp=player_xp,
                p_plays=p_plays(element),
                fixtures=len(matches),
            )
        )
        total += player_xp * multiplier
        if multiplier >= 2:
            captain_name = element.get("web_name", "?")
            captain_xp = player_xp

    concentration = {"club": "-", "players": 0}
    if club_counts:
        top_team = max(club_counts, key=lambda t: (club_counts[t], -t))
        concentration = {
            "club": teams.get(top_team, {}).get("short_name", str(top_team)),
            "players": club_counts[top_team],
        }

    return ManagerProjection(
        manager=manager_id,
        xp=total - hits,
        captain=captain_name,
        captain_xp=captain_xp,
        hits=hits,
        concentration=concentration,
        chip=chip,
        players=sorted(players, key=lambda p: -p.xp),
    )


def fixtures_by_team(gw_fixtures: Sequence[Mapping]) -> dict[int, list[tuple[int | None, bool]]]:
    """``{team_id: [(own_difficulty, is_home), ...]}``. Two entries in a double."""
    mapping: dict[int, list[tuple[int | None, bool]]] = {}
    for fixture in gw_fixtures:
        mapping.setdefault(int(fixture["team_h"]), []).append(
            (fixture.get("team_h_difficulty"), True)
        )
        mapping.setdefault(int(fixture["team_a"]), []).append(
            (fixture.get("team_a_difficulty"), False)
        )
    return mapping


def swing_candidates(projections: Sequence[ManagerProjection], limit: int = 6) -> list[dict]:
    """Players owned by more than one manager, ranked by how much is riding on them.

    Layer 2 picks *the* swing player from this list — it may not invent one, and
    every number attached to it comes from here.
    """
    owners: dict[int, dict] = {}
    for projection in projections:
        for player in projection.players:
            record = owners.setdefault(
                player.element,
                {"name": player.name, "team": player.team, "owned_by": [],
                 "xp": round(player.xp, 1), "captained_by": []},
            )
            record["owned_by"].append(projection.manager)
            if player.name == projection.captain:
                record["captained_by"].append(projection.manager)

    shared = [r for r in owners.values() if len(r["owned_by"]) > 1]
    shared.sort(key=lambda r: (-(len(r["captained_by"]) * 2 + len(r["owned_by"])), -r["xp"]))
    return shared[:limit]
