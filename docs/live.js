/* Live gameweek layer (§11).
 *
 * The FPL API sends no CORS headers, so the browser cannot call it directly.
 * Everything here goes through the proxy configured in config.js. Nothing is
 * stored: the data is as fresh as the poll, and there is no server-side state
 * to go wrong.
 *
 * The table shows FPL'S OWN points, unmodified. It used to add a BPS-derived
 * bonus estimate on top; that is gone. The estimate moved with every tackle,
 * disagreed with the FPL app for the whole of every match, and made a number
 * nobody could check against the source — in a league whose product is being
 * the book of record, a figure you cannot reconcile is worse than a smaller
 * one you can.
 *
 * So a live score here is what FPL says it is, and it is still NOT the settled
 * score (§11.3). Three things land only when FPL closes the round, each able to
 * flip who wins the weekly pot:
 *   (a) bonus      — during a match `bonus` is 0 and only `bps` is populated
 *   (b) auto-subs  — applied by FPL only at gameweek end
 *   (c) captain    — the vice takes over only at gameweek end
 * What this does is report all three as pending, and show the BPS standings —
 * the same thing FPL's own bonus tab shows — beside the table rather than
 * inside it.
 */

import { PROXY_BASE } from "./config.js";

const BONUS_VALUES = [3, 2, 1];
const WINDOW_TAIL_MINUTES = 150;
const PUBLISHED_LEAD_MINUTES = 60;

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
 * Outside this window we do not poll at all.
 *
 * Reads `kickoff_time` from the API and `ko` from the published file, so the
 * same window is computed from either source. */
export function inMatchWindow(fixtures, now = new Date(), leadMinutes = 0) {
  const kickoffs = (fixtures || [])
    .map((f) => {
      const at = f.kickoff_time || f.ko;
      return at && new Date(at).getTime();
    })
    .filter(Boolean);
  if (!kickoffs.length) return false;
  const opens = Math.min(...kickoffs) - leadMinutes * 60000;
  const closes = Math.max(...kickoffs) + WINDOW_TAIL_MINUTES * 60000;
  return now.getTime() >= opens && now.getTime() <= closes;
}

/* The cheap gate, answered from data.json alone — no request at all.
 *
 * Without it the first thing a poll did was fetch the bootstrap and all N
 * managers' picks, and only then look at whether a match was on. Before a
 * deadline FPL has no picks to give for the upcoming gameweek, so all N came
 * back 404, the cache stayed empty, and every open tab repeated them once a
 * minute — for months, for data it would have thrown away.
 *
 * The published kickoff times can be a week old, so a mid-week rearrangement
 * could move one earlier than this file knows. Opening an hour early costs at
 * most 60 requests and means a rescheduled match still lights up. */
export function worthPolling(publishedFixtures, now = new Date()) {
  return inMatchWindow(publishedFixtures, now, PUBLISHED_LEAD_MINUTES);
}

export async function fetchFixtures(gw) {
  return api(`/api/fixtures/?event=${gw}`);
}

function fixtureState(fixture) {
  if (!fixture) return "played"; // a blank gameweek: nothing is coming
  // finished_provisional is the final whistle; finished waits on confirmed
  // bonus. For "has this player's match been played" the whistle is the fact.
  if (fixture.finished || fixture.finished_provisional) return "played";
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

  // BPS standings per fixture — who is IN LINE for bonus, which is what FPL's
  // own bonus tab shows. Deliberately not added to anybody's score: see the
  // note on live_points below. Only for fixtures whose bonus is still open —
  // once a fixture reads finished, FPL has confirmed its bonus and folded it
  // into total_points, so "in line for" would be stale over a settled award.
  const bpsTop = new Map();
  for (const fixture of fixtures) {
    if (fixture.finished) continue;
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
        // FPL's own number, unmodified. We used to add a BPS-derived bonus
        // estimate on top; it made the table disagree with the FPL app for
        // the whole of every match, and an estimate that moves with every
        // tackle is not worth a table that cannot be checked against the
        // source. Bonus arrives here when FPL confirms it, inside
        // total_points, exactly as it does for everyone else.
        const total = stats.total_points || 0;
        points += total * pick.multiplier;

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
                  points: viceStats.total_points || 0,
                }
              : null,
          };
        }
      }
    }

    managers.push({
      id: manager.id,
      live_points: points,
      played,
      in_play: inPlay,
      to_play: toPlay,
      // A bench boost scores fifteen, so "played 4 of 11" is wrong for three
      // of the thirteen this week. The counter above already only counts
      // picks with a multiplier, so this is the squad that is actually live.
      squad: played + inPlay + toPlay,
      chip: picks.active_chip || null,
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
      ? "All matches finished, but FPL has not confirmed every bonus or applied auto-subs. Scores below are FPL's own and can still move. Nothing settles until every fixture reads finished."
      : "Scores are FPL's live points. Bonus folds into a score when FPL confirms it at each match's finish — until then the bonus watch shows who is in line, and one late save or booking can move it, and the pot with it.",
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
      finished_provisional: !!f.finished_provisional,
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
  const second = order[1];
  const gap = second === undefined ? 0 : totals[leader] - totals[second];
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
    callout: second === undefined
      ? `${nameOf(leader)} leads the ${bucket.month} pot on ${totals[leader]}, live points included.`
      : `${nameOf(leader)} leads the ${bucket.month} pot on ${totals[leader]}, ${gap} ahead of ${nameOf(second)} — live points included, ${remaining} gameweek${remaining === 1 ? "" : "s"} still to come.`,
  };
}

/* One poll. Returns the assembled live object, or null when there is nothing
 * live to show. Picks are frozen after the deadline, so they are fetched once
 * and cached by the caller. */
export async function poll({ data, gw, fixtures, picksByManager, bootstrap }) {
  if (!fixtures.length) return null;
  if (fixtures.every((f) => f.finished)) return null; // Final — data.json owns it now

  // §11.1's provisional tail: every match is at full time but FPL has not
  // confirmed bonus, so nothing has settled and data.json has no scores to
  // show. The §11.2 window has usually closed by then — GW1's sat overnight —
  // and going dark for it left the page with no scores anywhere. Keep
  // assembling: this is exactly when confirmed bonus can still flip the pot
  // (§11.4), and the tail ends the moment every fixture reads finished.
  const provisionalTail = fixtures.every((f) => f.finished_provisional);
  if (!provisionalTail && !inMatchWindow(fixtures)) return null;

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
