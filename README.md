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
data/2026-27/raw/gw-NN.projection.json  what the projection knew at the deadline
data/2026-27/corrections.json append-only adjusting entries (§3.9.4)
data/2026-27/data.json        derived ledger, rebuildable from the two above
docs/                         GitHub Pages — the published copy and the page
worker/                       Cloudflare Worker — the missing header, and the reminder
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

## The gameweek summary

Nobody opens a dashboard on a Sunday night; they read WhatsApp. So the thing
that actually circulates is a block of text — who took the week, what everybody
scored, the month **once its last gameweek is final**, and the season so far.
It is one button in the league-table header — share sheet where the browser
has one, clipboard otherwise — and it prints from the terminal:

```bash
python -m superf.summary              # from docs/data.json
python -m superf.summary path/to/data.json
```

It is composed in `superf/summary.py` **from the assembled payload**, not from
the ledger, and lands in `data.json` as `summary`. That direction matters: the
message must say what the page says. A message retyped by hand — or derived a
second time from the settlement — can be wrong while the page is right, and
nobody would find out until May.

The vocabulary is §3.9.1's throughout. The weekly and monthly pots are
*accrued* ("is owed", "owe"); the season pot is *projected* and never appears
as a credit; the footer repeats that nothing is paid until after GW38, because
a forwarded message arrives without the rest of the site attached. A month that
is still running says what first *would* take, and says "would".

## Running it

```bash
pip install -r requirements.txt

python build.py            # fetch, settle, assert, write docs/data.json
python predict.py          # call the upcoming gameweek (inside its window)
python predict.py --score  # score a finished gameweek into result/record
python -m superf.summary   # print the gameweek summary, ready to paste
python -m pytest -q        # the ledger maths

python build.py --offline  # rebuild from raw/ snapshots, API switched off
python tools/backtest.py   # mark the projection against every settled gameweek
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
5. **Deadline reminders** (optional) — a browser notification a few hours before
   each deadline. Three one-time steps, all on the Worker:

   ```bash
   cd worker
   npx wrangler kv namespace create SUBSCRIPTIONS   # paste the id into wrangler.toml
   node genkeys.mjs                                 # prints a VAPID keypair
   npx wrangler secret put VAPID_PUBLIC_KEY
   npx wrangler secret put VAPID_PRIVATE_JWK
   npx wrangler deploy
   ```

   Then a "🔔 Remind me" button appears in the header and each person opts in on
   their own device. Skip it and nothing changes: `/push/*` answers 503, the
   button never renders, and the rest of the dashboard is untouched.

   On iPhone the site must be added to the home screen first — Apple grants Push
   only to installed sites — and the button says so rather than doing nothing.

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
| `superf/scoring.py` | how good the xP ranking was — Spearman, pairwise, §12.3's three |
| `tools/backtest.py` | replays the frozen inputs and marks the model against results |
| `superf/emit.py` | assembles `data.json` to the contract in `SCHEMA.md` |
| `superf/summary.py` | the weekly message, composed from the finished payload |
| `superf/snapshot.py` | §4.2 pruned immutable snapshots, and §11.4 bonus flips |
| `superf/corrections.py` | §3.9.4 adjusting entries that never rewrite history |
| `superf/backup.py` | CSV + Sheets, including the end-of-season settle-up |
| `docs/runtime.js` | the template runtime the prototype needed and did not ship with |
| `superf/emit.py` → `rules_block` | the "How it works" tab, derived from N so it cannot go stale |
| `tools/gen_workflows.py` | regenerates both workflows from the real calendar |
| `worker/push.js` | VAPID-signed Web Push, without payload encryption |
| `docs/sw.js` | the service worker that writes the reminder from `data.json` |
| `docs/notify.js` | the opt-in half, and the six states it has to be honest about |

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

**The reminder push carries no payload.** An encrypted payload (RFC 8291) is
most of the code in a push library — ECDH, HKDF, aes128gcm — and none of it is
needed to say "the deadline is close", because the page that would say it is
already published. So the Worker sends a bare wake-up and `docs/sw.js` fetches
`data.json` and writes the notification from the same calendar the site renders.
One deadline list, not two, and a subscription's payload cannot leak from
anywhere because there is not one. What is left is VAPID: a signed JWT, about
forty lines of WebCrypto. `tests/test_push.mjs` verifies that signature the way
a push service verifies it, because a bad one fails silently — the push service
answers 401 and the league simply never gets reminded.

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

**The projection records what it knew, not just what it said.** `raw/gw-NN.json`
freezes what *happened*; it cannot say whether the projection was any good,
because the per-90 rates, availability, form and price it read all move daily
and the API offers no way back. So every call also freezes its own inputs, and
`tools/backtest.py` replays them through today's code and marks the ranking
against the settled result — with FPL's own `ep_next` scored alongside as the
control, because a projection that cannot beat the number the API gives away
free is not earning its place. Without this, "the prediction feels off" and
"the prediction is off" are the same sentence. `now_cost` and
`points_per_game` are frozen even though nothing reads them yet: they are what
a prior would be anchored on, and the point of writing them now is that the
next model inherits a history instead of starting from scratch.

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

**The 2 Sep layout revision** ("cluttered, table must be prominent") then
rearranged both tabs, and that did move markup:

- The **league table is the Gameweek tab**: full width, at the top, with the
  deadline as a strip in its own header and the summary button beside it. The
  countdown card — two-thirds of a phone screen, above the table everybody
  opens the site for — is gone. Eight columns now: place and movement (from
  `rank_prev`), manager, gameweek points, total, gap, accrued, ★ weekly pots
  won, chip. ★ switched from podiums to weeks won, which is the column that
  explains the accrued figure next to it.
- The season calendar, the international break and the Premier League table
  are background, so they are behind one **REFERENCE** toggle rather than
  three cards deep in the page.
- The **Season tab** leads with *Who is up, who is down* — a column per month,
  points above and that month's money underneath, tap a month header to open
  its gameweeks in place. That one table replaced the diverging ledger chart,
  the 38-column gameweek grid, and the range picker that existed to make the
  grid readable.
- **The "How it works" tab stays.** It explains the money to the people who
  did not write the rules, and every figure on it is derived from `N` and the
  real calendar by `rules_block`, so a new manager rewrites the whole tab
  without anyone touching copy.
- **Below 720px** (`MOBILE_BREAKPOINT` in `docs/app.js`) the tabs move to a
  bottom bar, the league table shows manager / gameweek / total with the other
  five fields a tap away on the row, and the month table becomes a ranked
  accrued list that opens the same way. The breakpoint, the nav position and
  whether bars survive a phone are constants at the top of `app.js` — they
  were component props in the design canvas.

**Nobody is "you".** The site is opened from a link by whoever has it and
cannot know who that is, so there is no manager picker, no highlighted row,
no personal accrued balance and no per-manager statement. What it shows is the
league: the table, the pots, who is up and who is down, and who would pay whom.
`ledger[].statement` is still emitted and still asserted (§3.9.2) — the audit
trail exists, it just is not a card on a public page.

Since then the chrome has been worked on, which did touch the markup:

**The theme is remembered** across reloads in `localStorage` under
`superf.prefs`, and the tab lives in the URL fragment (`#season`, `#rules`) so
it survives a reload, links, and the back button. Preferences only: nothing
stored changes what the page *says*, and every access is guarded because
storage throws outright in a locked-down browser. A one-liner in `<head>` reads
the theme back before first paint, so a remembered dark theme does not arrive
as a white flash. (It used to remember which manager you were as well; the page
no longer asks.)

**The tabs are a tablist**, not three buttons: left/right/Home/End move between
them, only the selected one is in the tab order, and the panel is labelled by
its tab. Alongside it, the page grew an `<h1>`, `header`/`main`/`footer`
landmarks, a skip link, `scope="col"` on its column headers, labels on the
pickers that remain, a focus ring — every control is inline-styled with `border:none` and
half of them had lost the browser's — and a `prefers-reduced-motion` rule, since
the live dot pulses for as long as a match is on.

**Focus survives a re-render.** The whole tree is replaced on every state
change, so focus landed on `<body>` after every click; `data-focus-key` marks
the controls that come back.

**Three statements that were wrong on the day.** `isPre` means "nothing has
settled", which is not "no football has been played" — the two come apart for
the three days GW1 is on, and the pill read `LIVE · GW1 not played` while GW1
was being played. The hero said "first deadline of the season" over GW2's
deadline for the same reason, and the league table's subtitle would have said it
opens with the first whistle while showing finished rounds. Each now asks the
question it means.

**One dead control removed**: with no prediction published, the card rendered a
button labelled "How it works" wired to a no-op — which is exactly what the page
showed all pre-season.

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
