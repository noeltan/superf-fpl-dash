# Data contract — canonical

This supersedes spec §5. The prototype's `DATA`, `LIVE` and `PREDICTION` objects
are the contract; §5 was written before the design existed and is missing twelve
fields the page reads. Where the two disagree, **this file wins** — and the way
to keep it from drifting is that `tests/test_schema.py` asserts the emitter
against these shapes, so a change here that nobody implements fails the build.

If a number appears on screen it exists in one of these files. The page never
computes money.

---

## `data.json`

Written by `build.py`. **Only `ledger`, `statement` and `settlement` are in
sen** (§3.8.1) — `stakes`, `exposure`, `months[]` and `gameweeks[].pot` stay in
ringgit, as §5 has them. The design's own handoff note pins this split.

```jsonc
{
  "generated_at": "2026-08-25T06:00:00Z",
  "league":  { "id": 310479, "name": "SuperF", "currency": "RM", "players": 8 },
  "current": { "season": "2026/27", "gameweek": 2, "next_gw": 3, "state": "final" },
  "tz":      { "name": "Asia/Kuala_Lumpur", "offset": 8 },
```

`current.state` is one of `upcoming | locked | live | provisional | final`
(§11.1). **The prototype's state map only had four** — `locked` would have
thrown — so it was added, styled as the warning state.

```jsonc
  // All derived from N. Never hardcoded. `monthly` describes the bucket
  // currently in view, so a 2-gameweek August and a 6-gameweek December differ.
  "stakes": {
    "weekly":  { "stake": 10, "pot": 80, "split": [0.7, 0.3], "net": [46, 14] },
    "monthly": { "stake_per_gw": 5, "gameweeks": 3, "stake": 15, "pot": 120,
                 "split": [0.70, 0.30], "net": [69, 21] },
    "season":  { "stake": 100, "pot": 800, "split": [0.60, 0.25, 0.15],
                 "net": [380, 100, 20] }
  },

  "exposure": { "staked": 480, "best": 2584, "worst": -480 },   // ← not in §5
```

> **`exposure` resolves a contradiction.** §3.4 says RM480 staked; §3.7's closing
> line says RM420. §3.7 predates decision #5 raising the season stake RM40 →
> RM100, so §3.4 is right. Deriving all three from `N` means they cannot
> disagree again: `staked = 38×5 + 38×5 + 100`, and
> `best = 38×(5N−5) + 190×(0.7N−1) + 100×(0.6N−1)`.

```jsonc
  "managers": [
    { "id": "noel",              // stable slug from entry_id, never changes
      "display_name": "Noel Tan",
      "short": "Noel",           // ← not in §5; avoids the view splitting strings
      "team_name": "JEONSOMI", "entry_id": 1652821 }
  ],

  "teams":  { "1": { "id": 1, "name": "Arsenal", "short": "ARS" } },
  "events": [ { "gw": 1, "deadline": "2026-08-21T17:30:00Z", "month": "AUG" } ],
  "breaks": [ { "after_gw": 5, "next_gw": 6, "days": 21,
                "resumes": "2026-10-10T12:30:00Z" } ],
  "month_buckets": [ { "month": "AUG", "gameweeks": [1, 2] } ],

  // Every gameweek, not just the next few. Array order is the join key the
  // live layer uses, so it must not be re-sorted downstream.
  "fixtures": {
    "3": [ { "h": 12, "a": 14, "ko": "2026-09-04T19:00:00Z", "dh": 4, "da": 2,
             "hs": null, "as": null, "started": false, "finished": false,
             "finished_provisional": false, "id": 21 } ]
  },

  // Derived from finished fixtures — the FPL API publishes no table.
  // Empty until a match has been played, so the view shows its designed
  // pre-season empty state rather than twenty rows of zeros.
  "pl_table": [ { "pos": 1, "team": 6, "p": 2, "w": 2, "d": 0, "l": 0,
                  "gf": 7, "ga": 0, "gd": 7, "pts": 6, "form": ["W", "W"] } ],
```

### `gameweeks[]` — settled gameweeks only

```jsonc
  "gameweeks": [
    { "gw": 1, "month": "AUG",
      "note": null,              // "double gameweek" | "blank gameweek" | "postponed"
      "pot": 80,
      "winners": ["soonlee"],    // array — a level-5 tie splits the pot
      "runners_up": ["jack"],    // ← not in §5: 70/30 pays 2nd, so it must be nameable
      "winner_net": 4600,        // ← not in §5: SEN, what THIS gameweek paid
      "runner_up_net": 1400,     // ← not in §5: SEN, ditto
      "tiebreak": null,          // ← not in §5: { level, text } e.g. "Won on assists (4 v 2)"
      "bonus_change": null,      // ← not in §5: §11.4's permanent note
      "scores": {
        "noel": { "points": 46, "hits": 0, "transfers": 1, "chip": null,
                  "did_not_set": false, "active": true }
      } }
  ],
```

Two things worth stating plainly:

- **`gameweeks[]` holds Final gameweeks only.** §3.8.5's `count(gameweeks) == 38`
  is about the *calendar*, which is `events[]`. Both are asserted, separately.
- **Every manager appears in every settled gameweek**, including one who had not
  joined yet — as `{"points": null, "active": false}`. The view indexes
  `scores[id]` for everybody in `rank`, so a missing key would throw. Inactive
  managers render as a dash and are charged nothing.
- **`did_not_set`** is true when a manager was in the league for that gameweek
  but has no history row. FPL rolls a team over if you do nothing, so a true
  `0 ✕` is rare by design; it means non-participation, not a quiet week.

### `months[]` — complete buckets only

```jsonc
  "months": [
    { "month": "AUG", "gameweeks": [1, 2], "played": [1, 2], "complete": true,
      "stake": 10, "pot": 80, "split": [0.70, 0.30], "net": [46, 14],  // ← stake/net not in §5
      "totals": { "noel": 114 },
      "order": ["soonlee", "jack"],
      "gap_to_first": 3,          // ← not in §5: first's margin over second
      "callout": "August settled. …" },                                // ← not in §5
  ],

  // ← not in §5. The bucket in play, for the card when no month has settled yet.
  "month_current": { "month": "SEP", "gameweeks": 3, "opens_gw": 3,
                     "stake": 15, "pot": 120, "net": [69, 21], "note": "…" },
```

`months[]` carries **only complete months** because the view labels the last
entry `SETTLED`. A bucket mid-flight would therefore lie; it lives in
`month_current` instead.

### Standings and ledger

```jsonc
  "totals":    { "noel": 114 },              // season points
  "rank":      ["soonlee", "jack"],          // ordered ids, §3.5 applied
  "rank_prev": { "noel": 6 },                // rank before the last settled gameweek
  "behind":    { "noel": 27 },               // ← not in §5
  "podiums":   { "noel": 1 },                // top-3 finishes, display only (§2)
  "weeks_won": { "soonlee": 2 },             // ← not in §5
  "stats":     { "avg_points": 113.5,
                 "high": { "gw": 1, "manager": "soonlee", "points": 72 } },  // ← not in §5

  "ledger": {
    "noel": { "weekly": -1000, "monthly": -1000,   // SEN
              "accrued": -2000,                    // weekly + monthly — the book
              "projected_season": -10000,          // NOT in accrued until GW38 Final
              "delta_last_gw": -500,               // ← not in §5
              "by_gameweek": [-500, -500],
              "statement": [                       // §3.9.2 — one row per event
                { "date": "2026-08-24", "type": "weekly", "gw": 1,
                  "detail": "46 pts, 7th of 8", "amount": -500, "balance": -500 },
                { "date": "2026-08-31", "type": "monthly", "month": "AUG",
                  "detail": "114 pts, 5th in August", "amount": -1000, "balance": -2000 }
              ] }
  },

  // §3.9.4 — append-only adjusting entries; each set sums to zero
  "corrections": [
    { "type": "correction", "date": "2027-03-14", "affects_gw": 12,
      "reason": "GW12 tiebreak applied cards before goals conceded",
      "adjustments": { "jack": 3500, "sam": -3500 } }
  ],

  // §3.9.3 — a muted preview until GW38, the deliverable after it
  "settlement": {
    "settled": false,
    "payments": [ { "from": "noel", "to": "soonlee", "amount": 2000 } ]
  },

  "settled": { "through_gw": 2,
               "projected": "season pot projected, not banked" },   // ← not in §5
  "checks":  { "zero_sum": true, "gameweeks_expected": 38, "gameweeks_present": 38 }
}
```

> **`by_gameweek` is the per-gameweek weekly amount**, not a running total. §5's
> comment says "running, for sparklines" but its own example is `[-500, -500]`,
> and the prototype reads it as per-gameweek. The example and the prototype
> agree, so the comment is the odd one out.

> **`accrued` excludes `projected_season`** until GW38 is Final (§3.9.1:
> "Projected" is not in the book at all). At GW38 the season component moves in,
> a `type: "season"` statement row is appended, `settlement.settled` flips true
> and `settled.projected` becomes `"season pot settled"`.

> **Every statement ends on `accrued`.** The emitter asserts it. A manager who
> disputes their total must be able to find the single row they disagree with
> (§3.9.2) — a statement that does not reconcile is worse than none.

**`rules` drives the "How it works" tab** and is derived, never written down:
per-month stakes and pots from the real calendar, the paid-place floor table
around the current league size, the tiebreak ladder with each level's direction
read out of `TiebreakStats.key`, and the best-case breakdown. A thirteenth
manager rewrites every figure on that tab without anyone touching copy — which
is the point, because a tab that explains the money is the worst place for a
hardcoded number to go stale.

**`runners_up` is empty when a tie for first swallowed second place.** The tied
managers take both paid shares between them, so there is no second place to name
— the only reading that stays zero-sum (§3.5 level 5).

**`winner_net` / `runner_up_net` are what that gameweek paid**, read off the
settled ledger, not `stakes.weekly.net`. The advertised block is today's league
size; a gameweek played before somebody joined settled a smaller pot and is
never recomputed (§3.8.6). The view must show the former or it prints money
nobody received.

**Statement row types:** `weekly` (carries `gw`), `monthly` (carries `month`),
`season`, `correction` (carries `affects_gw`). Ordered oldest first, and within
a date: weekly, then monthly, then season, then corrections.

---

## Live — in memory, never written

Spec §5 describes a `live.json` file, but §4 says live data is
"straight from FPL via the proxy, never stored". There is no such file: this is
the shape `docs/live.js` assembles in the browser each poll. Fields marked ←
are the prototype's additions to §5.

```jsonc
{
  "generated_at": "2026-08-22T15:42:10Z",
  "gw": 3, "state": "live",            // live | provisional
  "matches_in_play": 3,
  "bonus_watch": "…",                                       // ←
  "pot_leader": { "manager": "soonlee", "margin": 5, "over": "jack" },   // ←
  "fixtures": [ { "h": 1, "a": 7, "hs": 2, "as": 0, "minutes": 63,
                  "started": true, "finished": false,
                  "bps_top3": [ { "player": "Saka", "bps": 34,
                                  "provisional_bonus": 3 } ] } ],
  "managers": [
    { "id": "noel", "live_points": 41, "provisional_bonus_included": 4,
      "played": 8, "in_play": 2, "to_play": 1,
      "captain": { "name": "Haaland", "points": 12, "multiplier": 2,
                   "fallback_to_vice": false, "vice": null },            // ← vice
      "subs_pending": 1, "live_rank": 3,
      "overall_rank_live": 4, "rank_delta": 1 }                          // ←
  ],
  "month_pot": { … }                                                     // ←
}
```

`fixtures[]` is index-aligned with `data.json`'s `fixtures[gw]` because both are
sorted by kickoff then fixture id. The prototype's handoff note flags this as a
build-step guarantee; it is held in `contract_fixtures()` and `live.js`.

A live score is **not** the sum of `total_points` (§11.3): provisional bonus is
computed from BPS every poll, confirmed bonus is left alone once a fixture is
finished, and auto-subs and captain fallback are *reported as pending* rather
than applied.

---

## `prediction.json`

One file, describing the current gameweek. `result` is `null` until that
gameweek is Final, then it is filled in place — which is exactly the
prototype's `PREDICTION` → `PREDICTION_SETTLED` transition. Each version is also
archived to `docs/predictions/gwNN.json`, so the call is timestamped and
immutable (§12.3).

```jsonc
{
  "gw": 3,
  "generated_at": "2026-09-04T17:35:00Z",   // after the deadline, before kickoff
  "model": null,                            // whatever the API answered with
  "projections": [
    { "manager": "noel", "xp": 58.4, "captain": "Haaland", "captain_xp": 11.2,
      "hits": 0, "concentration": { "club": "MCI", "players": 4 } }
  ],
  "call": {
    "first":  { "manager": "soonlee", "confidence": "medium" },
    "second": { "manager": "jack",    "confidence": "low" },
    "agrees_with_projection": true,
    "swing_player": { "name": "Saka", "owned_by": ["jack", "noel"], "why": "…" },
    "reasoning": "<= 120 words"
  },
  "result": null,
  "record": { "played": 12, "exact": 4, "podium": 8, "pair": 3 }
}
```

`model` is never hardcoded. It comes from the `ANTHROPIC_MODEL` repository
variable, and what lands in the file is what the API actually answered with —
`null` when the call fell back to the projection ranking.

`record` counts **scored** predictions, so it excludes the current unscored one.

---

## Known divergences from the spec

| Where | Spec says | Reality |
|---|---|---|
| §3.4 vs §3.7 | RM480 staked / RM420 exposure | RM480. §3.7 predates the RM100 season stake |
| §3.2 | revisit the 1/N floor "past 12" | 15% ≥ 1/N holds for all N ≥ 7 and only gets safer; it breaks at N ≤ 6 |
| §3.8.2 | `distribute(stake_sen, N, …)` | takes a per-manager stake map, because §3.8.6 needs mixed N inside one month |
| §3.8.5 | `count(gameweeks) == 38` | asserted on `events[]`; `gameweeks[]` is settled-only |
| §5 | `live.json` is a file | assembled in the browser, never written (per §4) |
| §5 | `by_gameweek` "running" | per-gameweek, matching its own example |
| §10 | "Ties — per §3.4" | §3.5 |
| §7.1D | "the August problem (§3.5)" | §3.7 |
| §12.4 | `"model": "claude-opus-4-6"` | recorded from the API response at runtime |
| §3.9.2 | statement dates 22/29 Aug | the design's mock uses 24/31 Aug — the last kickoff of each gameweek, which is when it actually stopped moving |
| §4.1 | `data/2026-27/raw/gw-NN.json` | matched, plus `gw-NN.provisional.json` recording who led before bonus confirmed, so §11.4 can name a flip |
| §4.2 | rebuild from `raw/` + `corrections.json` | needs the calendar, clubs and roster too, which no per-gameweek snapshot carries — `raw/season.json` mirrors them so the rebuild is of the *file*, not just the ledger |
