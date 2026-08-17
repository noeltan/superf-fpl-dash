# SuperF — FPL league dashboard

The data pipeline behind the SuperF dashboard: an eight-person Malaysian FPL
money league, classic league `310479`, season 2026/27.

The league previously ran on a seven-season Google Sheet typed in by hand, which
stalled mid-February. **Nothing here is manually entered.** The FPL API is the
sole source of truth for scores; this repo owns the weekly, monthly and season
money on top of it.

**The league settles once, after GW38** (§3.9). No cash moves during the season:
every stake and payout is an *accrual*. That changes what this is — not a
dashboard reporting money that has already moved, but **the book of record for
money nobody has paid yet**. An error posted in November survives seven months
and only surfaces when someone is asked for RM400, so correctness and
auditability are the product, not nice properties.

The words matter and are used exactly (§3.9.1): *provisional* → *accrued*
("owes" / "is owed") → *projected* (not in the book at all) → *settled* (paid,
after GW38). Never "won" or "collected".

```
data/2026-27/raw/gw-NN.json   IMMUTABLE pruned FPL snapshot — the source of truth
data/2026-27/corrections.json append-only adjusting entries (§3.9.4)
data/2026-27/data.json        derived ledger, rebuildable from the two above
docs/                         GitHub Pages — the published copy and the page
worker/                       Cloudflare Worker — the one header Pages cannot send
```

**`raw/` is append-only; `data.json` is disposable.** `raw/gw-NN.json` freezes
each settled gameweek's scores and `raw/season.json` mirrors the calendar, clubs
and roster, so `build.py --offline` rebuilds the published file byte-identically
with the API switched off. CI proves it on every push, because a tiebreak bug
found in March has to be recomputed from source, not from the numbers you no
longer trust.

## The money, in one sentence

Every gameweek costs RM15 — RM10 to the week, RM5 to the month — plus RM100 on
the season. Every pot is zero-sum: the league banks nothing, and the build
refuses to publish a ledger that does not balance.

| Pot | Stake | Pot at N=8 | Split | Net at N=8 | Frequency |
|---|---|---|---|---|---|
| Weekly | RM10 | RM80 | 70/30 | 1st **+RM46** · 2nd **+RM14** · rest −RM10 | 38× |
| Monthly | RM5 × gameweeks | RM80–RM240 | 70/30 | 3-GW month: **+RM69** · **+RM21** | 10× |
| Season | RM100 | RM800 | 60/25/15 | **+RM380** · **+RM100** · **+RM20** | 1× |

Nothing above is hardcoded. Every figure is derived from `N`, so a ninth manager
rescales the site without a code change.

## Running it

```bash
pip install -r requirements.txt

python build.py            # fetch, settle, assert, write docs/data.json
python predict.py          # call the upcoming gameweek (inside its window)
python predict.py --score  # score a finished gameweek into result/record
python -m pytest -q        # the ledger maths

python build.py --offline  # rebuild from raw/ snapshots, API switched off
```

Serve the page locally with `python -m http.server -d docs 8000`.

### Deploying

1. **Pages** — Settings → Pages → deploy from branch, `/docs`. Committing the
   JSON *is* the publish step, so `git log` is the payout audit trail.
2. **Secrets** — `ANTHROPIC_API_KEY` for the prediction. Optional:
   `GOOGLE_SERVICE_ACCOUNT_JSON` + `SHEET_ID` for the Sheets mirror.
3. **Variable** — `ANTHROPIC_MODEL`, e.g. via
   `gh variable set ANTHROPIC_MODEL`. There is deliberately no default: the
   model is configuration, not source.
4. **Live scores** — `cd worker && npx wrangler deploy`, then paste the URL into
   `docs/config.js`. Until you do, everything works except the in-match live
   layer, which stays hidden.

Scheduled workflows only fire from the default branch, so the crons start once
this is merged.

## How it is put together

| File | Does |
|---|---|
| `superf/money.py` | §3.8.2's `distribute`, in integer sen |
| `superf/ledger.py` | §3.8.4 order of operations, §3.8.5 invariants |
| `superf/tiebreak.py` | §3.5's four-level ladder over the post-auto-sub XI |
| `superf/fplcal.py` | month buckets, breaks, the five gameweek states |
| `superf/pltable.py` | the league table, derived from finished fixtures |
| `superf/projection.py` | §12.2 Layer 1 — deterministic xP |
| `superf/claude_call.py` | §12.2 Layer 2 — and the guard on what it may quote |
| `superf/emit.py` | assembles `data.json` to the contract in `SCHEMA.md` |
| `superf/snapshot.py` | §4.2 pruned immutable snapshots, and §11.4 bonus flips |
| `superf/corrections.py` | §3.9.4 adjusting entries that never rewrite history |
| `superf/backup.py` | CSV + Sheets, including the end-of-season settle-up |
| `docs/runtime.js` | the template runtime the prototype needed and did not ship with |
| `tools/gen_workflows.py` | regenerates both workflows from the real calendar |

### Decisions worth knowing

**Integer sen everywhere.** 70% of RM135 is RM94.50. Floating-point ringgit
drifts until the zero-sum assertion fails for reasons nobody can find.

**`distribute` takes a per-manager stake map**, not §3.8.2's single
`stake_sen × N`. §3.8.6 requires a month bucket spanning a mid-season join to be
computed gameweek by gameweek, so the pot is `Σ_g 5 × N_g` and stakes differ per
manager. With uniform stakes it reduces to the spec's version exactly.

**Ties that survive all four levels split the combined money of the places those
managers occupy.** That is the only reading that stays zero-sum, and it
stays zero-sum: two managers tied for the weekly pot take both paid shares
between them, +RM30 each.

**Snapshot once, never re-fetch.** A Final gameweek's inputs cannot change, so
they are written to `data/2026-27/raw/gw-NN.json` on first sight and read from
disk forever after. Snapshots are **pruned** to the ~120 players the league
actually owns (§4.2) — tens of KB per gameweek rather than half a megabyte. A
steady-state run is about 20 requests.

**A fetch failure publishes nothing.** The previous `data.json` stays live and
ages, which fires the §9.5 stale banner. A stale number that says it is stale
beats a fresh number built from half an API.

**The prediction model may only quote numbers the projection produced**, and
that is enforced: every numeric token in the reasoning is checked against an
allowed set, retried once, then falls back to the projection ranking.

**Every statement must reconcile to the accrued balance.** The emitter asserts
it row by row. Under deferred settlement a total nobody can decompose is a total
nobody will pay without an argument (§3.9.2).

**Corrections are appended, never applied in place.** An adjusting entry carries
its own reason and must itself sum to zero; the original row stays visible
(§3.9.4).

### Changes to the prototype

The prototype was authored for a template runtime that was not shipped with it,
so `docs/runtime.js` implements the eight directives it uses and the markup and
`renderVals()` are otherwise unchanged. Beyond swapping mocks for `fetch()` and
removing the dev-state switcher (as its own handoff note instructed), two fixes:

- `locked` added to the state map — §11.1 defines five states and the map had
  four, so a locked gameweek would have thrown.
- two table cells render a dash for a manager who was not in the league for an
  earlier gameweek, rather than `undefined`.

## Where this disagrees with the spec

`SCHEMA.md` lists all of them with reasoning. The two that change money:

- **§3.7 says total exposure is RM420; §3.4 says RM480.** §3.4 is right — §3.7's
  line predates decision #5 raising the season stake from RM40 to RM100.
- **§3.2 says to revisit the third-place 1/N floor "if the league grows past
  12".** The risk runs the other way: 15% ≥ 1/N holds for every N ≥ 7 and gets
  safer as N grows, but breaks at N ≤ 6. It is also not only the season's
  problem — the 70/30 weekly pays a second place, which needs N ≥ 4 — so
  `build.py` checks every pot's last paid share and fails on any of them.
- **§3.4's RM480 exposure and §3.7's and §3.8.7's worked figures predate the
  weekly pot.** It is RM10 a head split 70/30, not RM5 winner-takes-all, so
  exposure is RM670 and the settled examples are larger. The shapes hold.
