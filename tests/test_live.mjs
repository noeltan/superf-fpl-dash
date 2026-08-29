/* The live table shows FPL's own points.
 *
 *   node tests/test_live.mjs
 *
 * This is the one property worth a test here: the number on the page must be
 * the number in the FPL app. It used to carry a BPS-derived bonus estimate on
 * top, which meant the two disagreed for the whole of every match and nobody
 * could reconcile the table against the source. A regression would be silent —
 * the scores would simply drift a few points high while every match is on.
 */

import assert from "node:assert/strict";

const { assemble, provisionalBonus } = await import("../docs/live.js");

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log("  ok  " + name);
  } catch (error) {
    console.error("FAIL  " + name + "\n      " + error.message);
    process.exitCode = 1;
  }
}

/* One fixture, in play. Two of our players are in it and both are high in the
 * BPS standings, so under the old behaviour both would have been silently
 * inflated — 3 for the leader, 2 for the runner-up, and the captain's doubled. */
const FIXTURE = {
  id: 1, team_h: 1, team_a: 2, team_h_score: 1, team_a_score: 0,
  minutes: 60, started: true, finished: false, finished_provisional: false,
  kickoff_time: "2026-08-28T19:00:00Z",
};

const ELEMENTS = [
  { id: 10, stats: { minutes: 60, total_points: 8, bps: 40 } }, // top BPS
  { id: 11, stats: { minutes: 60, total_points: 5, bps: 30 } }, // second
  { id: 12, stats: { minutes: 60, total_points: 2, bps: 20 } }, // third
  { id: 13, stats: { minutes: 0, total_points: 0, bps: 0 } },   // bench
];

const BOOTSTRAP = {
  elements: [
    { id: 10, team: 1, web_name: "Top" },
    { id: 11, team: 1, web_name: "Second" },
    { id: 12, team: 2, web_name: "Third" },
    { id: 13, team: 2, web_name: "Benched" },
  ],
};

const DATA = {
  managers: [{ id: "noel", entry_id: 1 }],
  totals: { noel: 0 },
  rank: ["noel"],
  month_buckets: [{ month: "AUG", gameweeks: [2] }],
  gameweeks: [],
  stakes: { monthly: { stake: 10, pot: 130, net: [81, 29] } },
};

function build(picks) {
  return assemble({
    data: DATA,
    gw: 2,
    fixtures: [FIXTURE],
    elements: ELEMENTS,
    picksByManager: { noel: { active_chip: null, picks } },
    bootstrap: BOOTSTRAP,
  });
}

console.log("Live points are FPL's points");

test("no bonus estimate is added, even to the BPS leader", () => {
  const live = build([
    { element: 10, multiplier: 1, is_captain: false, is_vice_captain: false },
  ]);
  // FPL says 8. The old code said 11 — 8 plus a 3-point estimate nobody could
  // check, for as long as the match was on.
  assert.equal(live.managers[0].live_points, 8);
});

test("the captain is doubled on FPL's number, not on an inflated one", () => {
  const live = build([
    { element: 10, multiplier: 2, is_captain: true, is_vice_captain: false },
  ]);
  // 8 x 2. The old behaviour doubled the estimate too, so the error doubled
  // with it: (8+3) x 2 = 22.
  assert.equal(live.managers[0].live_points, 16);
});

test("a whole XI sums to exactly FPL's arithmetic", () => {
  const live = build([
    { element: 10, multiplier: 2, is_captain: true, is_vice_captain: false },
    { element: 11, multiplier: 1, is_captain: false, is_vice_captain: true },
    { element: 12, multiplier: 1, is_captain: false, is_vice_captain: false },
    { element: 13, multiplier: 0, is_captain: false, is_vice_captain: false },
  ]);
  // 8x2 + 5 + 2, bench excluded. Nothing added, nothing assumed.
  assert.equal(live.managers[0].live_points, 23);
});

test("a benched player still contributes nothing", () => {
  const live = build([
    { element: 13, multiplier: 0, is_captain: false, is_vice_captain: false },
  ]);
  assert.equal(live.managers[0].live_points, 0);
});

console.log("\nBonus is reported beside the table, not inside it");

test("the BPS standings are still published for the bonus watch card", () => {
  const live = build([
    { element: 10, multiplier: 1, is_captain: false, is_vice_captain: false },
  ]);
  const top = live.fixtures[0].bps_top3;
  assert.equal(top.length, 3, "FPL's bonus tab shows three");
  assert.deepEqual(
    top.map((p) => [p.player, p.bps, p.provisional_bonus]),
    [["Top", 40, 3], ["Second", 30, 2], ["Third", 20, 1]]
  );
});

test("the award ladder itself is unchanged", () => {
  // Still the real rule, still used — just for the standings card now.
  const awards = provisionalBonus([
    { element: "a", bps: 40 },
    { element: "b", bps: 40 },
    { element: "c", bps: 20 },
  ]);
  assert.equal(awards.get("a"), 3);
  assert.equal(awards.get("b"), 3);
  assert.equal(awards.get("c"), 1);
});

console.log("\nWhat is still pending is still reported");

test("a played-but-zero-minutes starter is flagged as a pending sub", () => {
  const finished = { ...FIXTURE, finished_provisional: true, finished: false };
  const live = assemble({
    data: DATA, gw: 2, fixtures: [finished], elements: ELEMENTS,
    picksByManager: {
      noel: { active_chip: null, picks: [
        { element: 13, multiplier: 1, is_captain: false, is_vice_captain: false },
      ] },
    },
    bootstrap: BOOTSTRAP,
  });
  assert.equal(live.managers[0].subs_pending, 1, "auto-subs still have to land");
});

test("the pot leader ranks on FPL's numbers", () => {
  const live = assemble({
    data: { ...DATA, managers: [{ id: "noel", entry_id: 1 }, { id: "jack", entry_id: 2 }],
            totals: { noel: 0, jack: 0 }, rank: ["noel", "jack"] },
    gw: 2, fixtures: [FIXTURE], elements: ELEMENTS,
    picksByManager: {
      noel: { active_chip: null, picks: [{ element: 10, multiplier: 1 }] }, // 8
      jack: { active_chip: null, picks: [{ element: 11, multiplier: 1 }] }, // 5
    },
    bootstrap: BOOTSTRAP,
  });
  assert.equal(live.pot_leader.manager, "noel");
  // 8 - 5. Under the estimate it would have read 11 - 7 = 4.
  assert.equal(live.pot_leader.margin, 3);
});

console.log(`\n${passed} passed`);
