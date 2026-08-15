/* Live gameweek layer (§11).
 *
 * The FPL API sends no CORS headers, so the browser cannot call it directly.
 * Everything here goes through the proxy configured in config.js. Nothing is
 * stored: the data is as fresh as the poll, and there is no server-side state
 * to go wrong.
 *
 * A live score is NOT the sum of total_points (§11.3). Three things move after
 * the whistle and each can flip who wins the weekly pot:
 *   (a) bonus      — during a match `bonus` is 0 and only `bps` is populated
 *   (b) auto-subs  — applied by FPL only at gameweek end
 *   (c) captain    — the vice takes over only at gameweek end
 * So this computes provisional bonus from BPS every poll, and reports what is
 * still pending rather than implying the score is settled.
 */

import { PROXY_BASE } from "./config.js";

const BONUS_VALUES = [3, 2, 1];
const WINDOW_TAIL_MINUTES = 150;

export function proxyConfigured() {
  return typeof PROXY_BASE === "string" && /^https?:\/\//.test(PROXY_BASE);
}

async function api(path) {
  const response = await fetch(`${PROXY_BASE}${path}`, { mode: "cors" });
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json();
}

/* §11.3a — rank players in a fixture by bps; top 3, 2nd 2, 3rd 1.
 * Tied 1st: both 3, next gets 1. Tied 2nd: both 2, no 1. Tied 3rd: both 1. */
export function provisionalBonus(entries) {
  const scoring = entries.filter((e) => e.bps > 0).sort((a, b) => b.bps - a.bps);
  const awards = new Map();
  let slot = 0;
  let index = 0;
  while (index < scoring.length && slot < BONUS_VALUES.length) {
    const value = scoring[index].bps;
    const group = scoring.filter((e) => e.bps === value);
    for (const entry of group) awards.set(entry.element, BONUS_VALUES[slot]);
    slot += group.length;
    index += group.length;
  }
  return awards;
}

/* §11.2 — open at first kickoff, close at last kickoff + 150 minutes.
 * Outside this window we do not poll at all. */
export function inMatchWindow(fixtures, now = new Date()) {
  const kickoffs = fixtures
    .map((f) => f.kickoff_time && new Date(f.kickoff_time).getTime())
    .filter(Boolean);
  if (!kickoffs.length) return false;
  const opens = Math.min(...kickoffs);
  const closes = Math.max(...kickoffs) + WINDOW_TAIL_MINUTES * 60000;
  return now.getTime() >= opens && now.getTime() <= closes;
}

function fixtureState(fixture) {
  if (!fixture) return "played"; // a blank gameweek: nothing is coming
  if (fixture.finished) return "played";
  if (fixture.started) return "in_play";
  return "to_play";
}

/* Build the §5 live.json shape in memory. Never written anywhere. */
export function assemble({ data, gw, fixtures, elements, picksByManager, bootstrap }) {
  const byElement = new Map(elements.map((e) => [e.id, e]));
  const teamOfElement = new Map(
    (bootstrap.elements || []).map((e) => [e.id, e.team])
  );
  const nameOfElement = new Map(
    (bootstrap.elements || []).map((e) => [e.id, e.web_name])
  );

  // Provisional bonus only applies while a fixture is unfinished — once it is
  // finished, confirmed bonus is already inside total_points.
  const bonusByElement = new Map();
  const bpsTop = new Map();
  for (const fixture of fixtures) {
    const rows = [];
    for (const element of elements) {
      const team = teamOfElement.get(element.id);
      if (team !== fixture.team_h && team !== fixture.team_a) continue;
      const stats = element.stats || {};
      if (!stats.minutes && !stats.bps) continue;
      rows.push({ element: element.id, bps: stats.bps || 0 });
    }
    const awards = provisionalBonus(rows);
    bpsTop.set(
      fixture.id,
      rows
        .filter((r) => awards.has(r.element))
        .sort((a, b) => b.bps - a.bps)
        .slice(0, 3)
        .map((r) => ({
          player: nameOfElement.get(r.element) || "?",
          bps: r.bps,
          provisional_bonus: awards.get(r.element),
        }))
    );
    if (fixture.started && !fixture.finished) {
      for (const [element, bonus] of awards) bonusByElement.set(element, bonus);
    }
  }

  const fixtureOfTeam = new Map();
  for (const fixture of fixtures) {
    fixtureOfTeam.set(fixture.team_h, fixture);
    fixtureOfTeam.set(fixture.team_a, fixture);
  }

  const managers = [];
  for (const manager of data.managers) {
    const picks = picksByManager[manager.id];
    if (!picks) continue;

    let points = 0;
    let bonusIncluded = 0;
    let played = 0;
    let inPlay = 0;
    let toPlay = 0;
    let subsPending = 0;
    let captain = { name: "-", points: 0, multiplier: 2, fallback_to_vice: false, vice: null };

    const vicePick = (picks.picks || []).find((p) => p.is_vice_captain);
    for (const pick of picks.picks || []) {
      const element = byElement.get(pick.element);
      const stats = (element && element.stats) || {};
      const team = teamOfElement.get(pick.element);
      const fixture = fixtureOfTeam.get(team);
      const state = fixtureState(fixture);

      if (pick.multiplier > 0) {
        const bonus = bonusByElement.get(pick.element) || 0;
        const total = (stats.total_points || 0) + bonus;
        points += total * pick.multiplier;
        bonusIncluded += bonus * pick.multiplier;

        if (state === "played") played += 1;
        else if (state === "in_play") inPlay += 1;
        else toPlay += 1;

        // §11.3b — auto-subs land only at gameweek end.
        if (state === "played" && !stats.minutes) subsPending += 1;

        if (pick.multiplier >= 2) {
          const viceElement = vicePick && byElement.get(vicePick.element);
          const viceStats = (viceElement && viceElement.stats) || {};
          // §11.3c — if the captain's match has finished with 0 minutes, show both.
          const fellBack = state === "played" && !stats.minutes;
          captain = {
            name: nameOfElement.get(pick.element) || "?",
            points: total,
            multiplier: pick.multiplier,
            fallback_to_vice: fellBack,
            vice: fellBack && vicePick
              ? {
                  name: nameOfElement.get(vicePick.element) || "?",
                  points: (viceStats.total_points || 0) +
                    (bonusByElement.get(vicePick.element) || 0),
                }
              : null,
          };
        }
      }
    }

    managers.push({
      id: manager.id,
      live_points: points,
      provisional_bonus_included: bonusIncluded,
      played,
      in_play: inPlay,
      to_play: toPlay,
      captain,
      subs_pending: subsPending,
      live_rank: 0,
      overall_rank_live: 0,
      rank_delta: 0,
    });
  }

  managers.sort((a, b) => b.live_points - a.live_points);
  managers.forEach((m, i) => { m.live_rank = i + 1; });

  // Δ rank reads as where you would sit overall if the gameweek ended now.
  const settled = data.totals || {};
  const projected = managers
    .map((m) => ({ id: m.id, total: (settled[m.id] || 0) + m.live_points }))
    .sort((a, b) => b.total - a.total);
  const projectedRank = new Map(projected.map((p, i) => [p.id, i + 1]));
  const settledRank = new Map((data.rank || []).map((id, i) => [id, i + 1]));
  for (const manager of managers) {
    manager.overall_rank_live = projectedRank.get(manager.id) || 0;
    const before = settledRank.get(manager.id);
    manager.rank_delta = before ? before - manager.overall_rank_live : 0;
  }

  const inPlayCount = fixtures.filter((f) => f.started && !f.finished).length;
  const allProvisional =
    fixtures.length > 0 && fixtures.every((f) => f.finished_provisional);
  const state = inPlayCount > 0 || !allProvisional ? "live" : "provisional";

  const leader = managers[0];
  const runnerUp = managers[1];

  return {
    generated_at: new Date().toISOString(),
    gw,
    state,
    matches_in_play: inPlayCount,
    bonus_watch: state === "provisional"
      ? "All matches finished but bonus is not confirmed, and auto-subs have not been applied. Nothing settles until every fixture reads finished."
      : "Bonus is provisional while matches are in play. One late save or booking moves the BPS, and the pot can change hands with it.",
    pot_leader: leader && runnerUp
      ? { manager: leader.id, margin: leader.live_points - runnerUp.live_points, over: runnerUp.id }
      : null,
    fixtures: fixtures.map((f) => ({
      h: f.team_h,
      a: f.team_a,
      hs: f.team_h_score,
      as: f.team_a_score,
      minutes: f.minutes || 0,
      started: !!f.started,
      finished: !!f.finished,
      bps_top3: bpsTop.get(f.id) || [],
    })),
    managers,
    month_pot: monthPot(data, gw, managers),
  };
}

function monthPot(data, gw, liveManagers) {
  const bucket = (data.month_buckets || []).find((b) => b.gameweeks.includes(gw));
  if (!bucket) return null;

  const settledGws = new Set((data.gameweeks || []).map((g) => g.gw));
  const totals = {};
  for (const manager of data.managers) totals[manager.id] = 0;
  for (const gameweek of data.gameweeks || []) {
    if (!bucket.gameweeks.includes(gameweek.gw)) continue;
    for (const [id, score] of Object.entries(gameweek.scores)) {
      if (score.points != null) totals[id] = (totals[id] || 0) + score.points;
    }
  }
  for (const manager of liveManagers) {
    totals[manager.id] = (totals[manager.id] || 0) + manager.live_points;
  }

  const order = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
  const stakes = data.stakes.monthly;
  const played = bucket.gameweeks.filter((g) => settledGws.has(g) || g === gw);
  const remaining = bucket.gameweeks.length - played.length;
  const leader = order[0];
  const you = data._you || order[0];
  const gap = totals[leader] - totals[you];
  const nameOf = (id) => {
    const manager = data.managers.find((m) => m.id === id);
    return manager ? manager.short : id;
  };

  return {
    month: bucket.month,
    gameweeks: bucket.gameweeks.length,
    played_live: [gw],
    stake: stakes.stake,
    pot: stakes.pot,
    net: stakes.net,
    provisional: true,
    totals,
    order,
    callout: you === leader
      ? `You lead the ${bucket.month} pot on ${totals[you]} including live points. ${remaining} gameweek${remaining === 1 ? "" : "s"} still to come, and none of it is banked yet.`
      : `You need ${gap} more than ${nameOf(leader)} to take the +RM${stakes.net[0]} — ${totals[you]} against ${totals[leader]}, live points included. ${remaining} gameweek${remaining === 1 ? "" : "s"} left.`,
  };
}

/* One poll. Returns the assembled live object, or null when there is nothing
 * live to show. Picks are frozen after the deadline, so they are fetched once
 * and cached by the caller. */
export async function poll({ data, gw, picksByManager, bootstrap }) {
  const fixtures = await api(`/api/fixtures/?event=${gw}`);
  if (!fixtures.length || !inMatchWindow(fixtures)) return null;
  if (fixtures.every((f) => f.finished)) return null; // Final — data.json owns it now

  const live = await api(`/api/event/${gw}/live/`);
  return assemble({
    data,
    gw,
    fixtures,
    elements: live.elements || [],
    picksByManager,
    bootstrap,
  });
}

export async function fetchBootstrap() {
  return api("/api/bootstrap-static/");
}

export async function fetchPicks(entryId, gw) {
  return api(`/api/entry/${entryId}/event/${gw}/picks/`);
}
