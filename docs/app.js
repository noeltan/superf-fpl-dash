/* SuperF — FPL League Dashboard.
 *
 * The view below (renderVals and everything it builds) is the prototype's own
 * code, unchanged except where noted in the commit that introduced this file:
 * the mock DATA / LIVE / PREDICTION objects are replaced by fetch(), the dev
 * state switcher is gone, `locked` was added to the state map because §11.1
 * defines five states and the map only had four, and two table cells learned to
 * render a dash for a manager who was not in the league yet.
 *
 * Money vocabulary follows §3.9.1 exactly: nothing is paid until May, so the
 * running figure is "owes" / "is owed" and never "won" or "collected".
 *
 * Derived-value policy is unchanged: the view formats (RM, the true minus sign,
 * dates, MYT conversion) and does layout maths only. It never computes money,
 * ranking or aggregation. Those arrive settled in data.json.
 */

import { render } from "./runtime.js";
import { PROXY_BASE } from "./config.js";
import * as liveFeed from "./live.js";

const POLL_INTERVAL_MS = 60000;

/* Three things the page used to forget on every reload: which of the eight
 * managers you are, who you compare against, and whether you asked for the
 * dark theme. Eight people read this all season on the same phone, so being
 * asked to find yourself in a dropdown again every visit is the single
 * cheapest thing to fix.
 *
 * Preferences only — no money, nothing derived, nothing that would change what
 * the page says. Storage throws in a locked-down browser, so every access is
 * guarded and a failure just means the old behaviour.
 *
 * The key is also read by a one-liner in index.html's <head>, which restores
 * the theme before first paint. Changing the shape means changing both. */
const PREFS_KEY = "superf.prefs";

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
  } catch (error) {
    return {};
  }
}

function savePrefs(patch) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...patch }));
  } catch (error) {
    /* private mode, or storage disabled — the session still works */
  }
}

/* The tab lives in the URL so it survives a reload and can be linked to. It is
 * the only piece of state worth a history entry: "look at how it works" is a
 * thing people send each other, "look at me compared to Jack" is not. */
const TABS = ["gw", "season", "rules"];

function tabFromHash() {
  const hash = location.hash.replace(/^#/, "");
  return TABS.includes(hash) ? hash : null;
}

class Dashboard {
  constructor(root, template) {
    this.root = root;
    this.template = template;
    this.data = null;
    this.live = null;
    this.prediction = null;
    this._picks = null;
    this._bootstrap = null;
    this._pollTimer = null;
  }

  setState(patch) {
    Object.assign(this.state, patch);
    this.render();
  }

  render() {
    if (!this.data) return;
    render(this.root, this.template, this.renderVals());
  }

  async boot() {
    const bust = `?t=${Math.floor(Date.now() / 60000)}`;
    const data = await fetch(`data.json${bust}`).then((r) => {
      if (!r.ok) throw new Error(`data.json ${r.status}`);
      return r.json();
    });
    this.data = data;

    // prediction.json is optional and may lag data.json by a gameweek. If it
    // names anybody the ledger does not know, drop it rather than render a
    // half-resolved card.
    try {
      const prediction = await fetch(`prediction.json${bust}`).then((r) =>
        r.ok ? r.json() : null
      );
      const known = new Set(data.managers.map((m) => m.id));
      const named = prediction
        ? [
            prediction.call.first.manager,
            prediction.call.second.manager,
            ...prediction.projections.map((p) => p.manager),
          ]
        : [];
      this.prediction = named.every((id) => known.has(id)) ? prediction : null;
    } catch (error) {
      this.prediction = null;
    }

    // A remembered manager is checked against the current roster before it is
    // trusted: somebody who left the league must not go on being "you".
    const prefs = loadPrefs();
    const known = new Set(data.managers.map((m) => m.id));
    const fallback = data.managers.find((m) => m.id === "noel") || data.managers[0];
    const preferred = known.has(prefs.you)
      ? data.managers.find((m) => m.id === prefs.you)
      : fallback;
    const other =
      known.has(prefs.cmp) && prefs.cmp !== preferred.id
        ? data.managers.find((m) => m.id === prefs.cmp)
        : data.managers.find((m) => m.id !== preferred.id) || preferred;
    this.state = {
      tab: tabFromHash() || "gw",
      you: preferred.id,
      cmp: other.id,
      fxGW: data.current.state === "final" || data.current.state === "upcoming"
        ? data.current.next_gw
        : data.current.gameweek,
      theme: this.detectTheme(),
      detailRange: "all",
      // Viewport width, tracked in state: below 560px the pot and ledger bar
      // charts become lists, because a bar chart squeezed into a phone is a
      // decoration, not a reading of who owes what.
      vw: typeof window === "undefined" ? 1200 : window.innerWidth,
      potView: "chart",
      ledgerView: "chart",
      showProj: false,
      moneyOpen: null,
      tick: 0,
      liveSince: Date.now(),
    };

    this.render();
    this.startClock();
    this.watchViewport();
    this.watchHistory();
    this.startLive();
  }

  /* Rotating a phone crosses the 560px threshold, so the chart/list choice has
   * to follow the viewport rather than be decided once at load. The 8px dead
   * band stops a re-render on every pixel of an address-bar collapse. */
  watchViewport() {
    window.addEventListener("resize", () => {
      const width = window.innerWidth;
      if (Math.abs(width - this.state.vw) > 8) this.setState({ vw: width });
    });
  }

  /* Back and forward move between tabs. A fragment navigation fires popstate
   * as well as hashchange, so one listener covers both the buttons and a hash
   * typed into the bar. An unrecognised fragment (#panel, from the skip link)
   * is somebody jumping within the page, not changing tab — leave it alone. */
  watchHistory() {
    window.addEventListener("popstate", () => {
      const tab = tabFromHash();
      if (tab) this.setState({ tab });
      else if (!location.hash) this.setState({ tab: "gw" });
    });
  }

  goTab(tab) {
    if (tabFromHash() !== tab) history.pushState({ tab }, "", "#" + tab);
    this.setState({ tab });
  }

  detectTheme() {
    const attribute = document.documentElement.getAttribute("data-theme");
    if (attribute) return attribute;
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  /* The countdown re-renders every 30s, every second while live so the
   * staleness figure keeps moving. No count-up animation on any score (§11.5). */
  startClock() {
    let ticks = 0;
    setInterval(() => {
      ticks += 1;
      if (this.live || ticks % 30 === 0) this.setState({ tick: ticks });
    }, 1000);
  }

  /* §11.2 — poll only inside match windows, and only through the proxy. */
  async startLive() {
    if (!liveFeed.proxyConfigured()) {
      console.info("Live disabled: no CORS proxy configured in config.js");
      return;
    }
    const poll = () => this.pollOnce().catch(() => {});
    await poll();
    this._pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  async pollOnce() {
    const data = this.data;
    const gw = data.current.state === "final" ? data.current.next_gw : data.current.gameweek;
    if (!gw) return;

    // Free gate first: data.json already carries every kickoff time, so a week
    // with no football in progress costs no requests at all. Everything below
    // this line only runs when a match plausibly is on.
    if (!liveFeed.worthPolling(data.fixtures[gw] || data.fixtures[String(gw)])) return;

    // Then one request to confirm it against the real calendar, before the
    // expensive part. Kickoffs move.
    const fixtures = await liveFeed.fetchFixtures(gw);
    if (!fixtures.length || !liveFeed.inMatchWindow(fixtures)) return;

    if (!this._bootstrap) this._bootstrap = await liveFeed.fetchBootstrap();
    if (!this._picks) {
      // Picks are frozen after the deadline, so this happens once per gameweek.
      const entries = await Promise.all(
        data.managers.map(async (manager) => {
          try {
            return [manager.id, await liveFeed.fetchPicks(manager.entry_id, gw)];
          } catch (error) {
            return [manager.id, null];
          }
        })
      );
      this._picks = Object.fromEntries(entries.filter(([, picks]) => picks));
      if (!Object.keys(this._picks).length) {
        this._picks = null;
        return;
      }
    }

    const assembled = await liveFeed.poll({
      data,
      gw,
      fixtures,
      picksByManager: this._picks,
      bootstrap: this._bootstrap,
    });
    if (assembled) {
      assembled._dev_age_seconds = 0;
      this.live = assembled;
      this.setState({ liveSince: Date.now() });
    } else if (this.live) {
      // The gameweek went Final between polls — data.json owns it from here.
      this.live = null;
      this._picks = null;
      this.render();
    }
  }

  state = { tab:"gw", you:null, cmp:null, fxGW:null, theme:"light", detailRange:"all", vw:1200, potView:"chart", ledgerView:"chart",
            showProj:false, moneyOpen:null, tick:0, liveSince:Date.now() };

  /* ---------- formatting only — no money maths ---------- */
  rm(v){
    if (v === null || v === undefined) return "—";
    const a = Math.abs(v);
    const s = Number.isInteger(a) ? a.toLocaleString() : a.toFixed(2);
    return (v > 0 ? "+RM" : v < 0 ? "\u2212RM" : "RM") + s;
  }
  rmFlat(v){
    const a = Math.abs(v);
    return (v < 0 ? "\u2212RM" : "RM") + (Number.isInteger(a) ? a.toLocaleString() : a.toFixed(2));
  }
  myt(iso){ return new Date(new Date(iso).getTime() + 8 * 3600e3); }
  hhmm(iso){ const d = this.myt(iso);
    return String(d.getUTCHours()).padStart(2,"0") + ":" + String(d.getUTCMinutes()).padStart(2,"0"); }
  dayKey(iso){ const d = this.myt(iso);
    return ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][d.getUTCDay()] + " " + d.getUTCDate() + " " +
      ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getUTCMonth()]; }
  dateShort(iso){ const d = this.myt(iso);
    return d.getUTCDate() + " " + ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getUTCMonth()]; }
  sen(v){ return this.rm(v / 100); }
  senFlat(v){ return this.rmFlat(v / 100); }
  sen2(v){ const a = Math.abs(v / 100).toFixed(2);
    return (v > 0 ? "+RM" : v < 0 ? "\u2212RM" : "RM") + a; }
  sen2Flat(v){ return (v < 0 ? "\u2212RM" : "RM") + Math.abs(v / 100).toFixed(2); }
  senAbs(v){ return "RM" + Math.abs(v / 100).toFixed(2); }
  monthName(m){ return {AUG:"August",SEP:"September",OCT:"October",NOV:"November",DEC:"December",
    JAN:"January",FEB:"February",MAR:"March",APR:"April",MAY:"May"}[m] || m; }
  ago(sec){ if (sec < 60) return sec + "s ago";
    const m = Math.floor(sec/60); return m < 60 ? m + "m ago" : Math.floor(m/60) + "h ago"; }

  get D(){ return this.data; }
  get feed(){ return this.live; }
  get cur(){ return this.D.current; }

  renderVals(){
    const S = this.state, D = this.D, F = this.feed, cur = this.cur, T = D.teams;
    const rm = v => this.rm(v), N = D.league.players;
    const byId = {}; D.managers.forEach(m => { byId[m.id] = m; });
    const monthCurrent = D["month_current"] ||
      { month:"\u2014", gameweeks:0, opens_gw:0, stake:0, pot:0, net:[0,0], note:"" };
    const you = byId[S.you] ? S.you : D.managers[0].id;
    D._you = you;   // read by the live month-pot callout
    const cmp = byId[S.cmp] ? S.cmp : D.managers[1].id;
    const isLive = cur.state === "live", isProv = cur.state === "provisional";
    const isFinal = cur.state === "final";
    const isPre = !D.gameweeks.length;   // nothing settled yet — the designed empty state
    const settledGWs = D.gameweeks;
    const lastGW = settledGWs.length ? settledGWs[settledGWs.length - 1] : null;

    const fillOf = id => id === you ? "var(--accent)" : id === cmp ? "var(--compare)" : "var(--dim)";
    const inkOf  = id => id === you ? "var(--accent)" : id === cmp ? "var(--compare)" : "var(--ink-2)";
    const wOf    = id => (id === you || id === cmp) ? 620 : 450;
    const rowBgOf = id => id === you ? "var(--tint)" : "transparent";
    const markOf = id => id === you ? "var(--accent)" : id === cmp ? "var(--compare)" : "transparent";
    const name = id => byId[id] ? byId[id].short : id;

    /* ---- chrome ---- */
    const stateMap = {
      upcoming:    { text:"PRE-SEASON",  dot:"var(--warn)",  ink:"var(--warn-ink)", anim:"none" },
      final:       { text:"FINAL",       dot:"var(--good)",  ink:"var(--good)",     anim:"none" },
      live:        { text:"LIVE",        dot:"var(--crit)",  ink:"var(--crit)",     anim:"pulse 1.5s ease-in-out infinite" },
      provisional: { text:"PROVISIONAL", dot:"var(--warn)",  ink:"var(--warn-ink)", anim:"none" },
      locked:      { text:"LOCKED",      dot:"var(--warn)",  ink:"var(--warn-ink)", anim:"none" }
    };
    const sm = stateMap[cur.state];
    /* isPre is "nothing has settled", which is not "no football has been
     * played" — the two come apart for the three days GW1 is actually on, and
     * the pill was reading LIVE next to "GW1 not played". State first, then
     * the empty-book copy. */
    const stateSub = cur.state === "locked" ? "GW" + cur.next_gw + " locked · first kickoff soon"
      : cur.state === "live" ? "GW" + cur.gameweek + " in progress"
      : isPre ? "GW1 not played"
      : isFinal ? "GW" + cur.gameweek + " settled · GW" + cur.next_gw + " next"
      : "GW" + cur.gameweek + " in progress";

    /* A tablist, not three buttons: left/right move between tabs and only the
     * selected one is in the tab order, which is what a screen reader and a
     * keyboard both expect of something shaped like this. */
    const tabLabels = { gw:"Gameweek", season:"Season & money", rules:"How it works" };
    const tabs = TABS.map(k => ({
      key: k, label: tabLabels[k],
      selected: S.tab === k, tabindex: S.tab === k ? 0 : -1,
      onClick: () => this.goTab(k),
      onKeydown: e => {
        const step = { ArrowRight:1, ArrowLeft:-1 }[e.key];
        const next = step !== undefined
          ? TABS[(TABS.indexOf(k) + step + TABS.length) % TABS.length]
          : e.key === "Home" ? TABS[0]
          : e.key === "End" ? TABS[TABS.length - 1] : null;
        if (!next) return;
        e.preventDefault();
        this.goTab(next);
        // setState renders synchronously, so the new tab exists to focus.
        const moved = this.root.querySelector('[data-focus-key="tab-' + next + '"]');
        if (moved) moved.focus();
      },
      bg: S.tab === k ? "var(--surface-1)" : "transparent",
      ink: S.tab === k ? "var(--ink-1)" : "var(--ink-2)",
      weight: S.tab === k ? 620 : 520,
      shadow: S.tab === k ? "0 1px 3px rgba(0,0,0,.08)" : "none"
    }));

    /* stale-data banner (§9.5) — time comparison only */
    let banner = { show:false, text:"" };
    const lastPassed = D.events.filter(e => new Date(e.deadline).getTime() < Date.now()).pop();
    if (lastPassed && !isPre) {
      const gap = new Date(lastPassed.deadline).getTime() + 48*3600e3 - new Date(D.generated_at).getTime();
      if (gap > 0 && new Date(D.generated_at).getTime() < new Date(lastPassed.deadline).getTime()) {
        banner = { show:true, text:"Data was last generated " + this.dateShort(D.generated_at) +
          ", before the GW" + lastPassed.gw + " deadline. Numbers on this page are stale — the fetcher has not run." };
      }
    }

    /* ---- A. countdown ---- */
    const nextEv = D.events.find(e => e.gw === cur.next_gw) || D.events[0];
    const ms = new Date(nextEv.deadline).getTime() - Date.now();
    let big = "LOCKED";
    if (ms > 0) {
      const d = Math.floor(ms/864e5), h = Math.floor(ms/36e5) % 24, m = Math.floor(ms/6e4) % 60;
      big = d > 0 ? d + "d " + h + "h " + m + "m" : h + "h " + m + "m";
    }
    const cd = {
      label: "GW" + nextEv.gw + " DEADLINE",
      big,
      myt: this.dayKey(nextEv.deadline) + ", " + this.hhmm(nextEv.deadline),
      utc: nextEv.deadline.slice(11,16),
      note: nextEv.gw === 1
        ? "First deadline of the season, lock at 01:30 Saturday morning here. Set your team Friday night lah, don't sleep first then cry after."
        : "Malaysia time first, UTC after. Every kickoff this round lands between 19:30 and 03:00 our side — so ya, some of us watching at 3am again."
    };

    const brkRec = D.breaks.find(b => b.after_gw >= cur.next_gw) || D.breaks[D.breaks.length - 1];
    const gwsTo = brkRec.after_gw - cur.next_gw + 1;
    const brk = {
      head: "After GW" + brkRec.after_gw + " — " + brkRec.days + " days off, back for GW" +
            brkRec.next_gw + " on " + this.dateShort(brkRec.resumes) + ".",
      body: gwsTo > 0 ? gwsTo + (gwsTo > 1 ? " gameweeks to go." : " gameweek to go.") + " Six breaks this season, two of them damn long — 20 days plus no football."
                      : "Six breaks this season, two of them 20 days plus. Sien.",
      tz: "We are UTC+8, so nothing starts before 19:00 our time and the late games only finish after 02:00. A gameweek's day here is our day, not their day."
    };

    /* ---- fixtures ---- */
    const fxGW = D.fixtures[String(S.fxGW)] ? S.fxGW : Number(Object.keys(D.fixtures)[0]);
    const liveJoin = F && F.gw === fxGW ? F.fixtures : null;
    const fxRows = D.fixtures[String(fxGW)] || [];
    const groups = [];
    fxRows.forEach((f, i) => {
      const key = this.dayKey(f.ko);
      let g = groups.find(x => x.day === key);
      if (!g) { g = { day:key, note:"", rows:[] }; groups.push(g); }
      const lf = liveJoin ? liveJoin[i] : null;
      const hasScore = lf ? lf.hs !== null : f.hs !== null;
      const hs = lf ? lf.hs : f.hs, as = lf ? lf.as : f.as;
      const inPlay = lf ? (lf.started && !lf.finished && lf.minutes > 0) : false;
      g.rows.push({
        home: T[f.h].name, away: T[f.a].name, dh: f.dh, da: f.da,
        dhBg: "var(--f" + f.dh + ")", dhInk: "var(--f" + f.dh + "t)",
        daBg: "var(--f" + f.da + ")", daInk: "var(--f" + f.da + "t)",
        mid: hasScore ? hs + " – " + as : this.hhmm(f.ko),
        midBg: inPlay ? "var(--tint-warn)" : "var(--surface-2)",
        midSize: hasScore ? "14.5px" : "13px",
        midWeight: hasScore ? 660 : 560,
        midInk: inPlay ? "var(--warn-ink)" : hasScore ? "var(--ink-1)" : "var(--ink-2)"
      });
    });
    groups.forEach(g => {
      const times = g.rows.map(r => r.mid).filter(t => t.indexOf(":") > 0);
      g.note = times.length ? "MYT" : "";
    });
    const fx = {
      title: "GW" + fxGW + " fixtures",
      sub: (liveJoin ? "Live scores · " : "") + "grouped by Malaysian day · " + fxRows.length + " matches",
      gw: String(fxGW),
      onGW: e => this.setState({ fxGW: Number(e.target.value) }),
      options: Object.keys(D.fixtures).map(k => ({ v:k, label:"GW" + k + (Number(k) === cur.next_gw ? " · next" : "") })),
      groups,
      legend: [1,2,3,4,5].map(n => ({ n, bg:"var(--f"+n+")", ink:"var(--f"+n+"t)" }))
    };

    /* ---- calendar ---- */
    const brkAfter = {}; D.breaks.forEach(b => { brkAfter[b.after_gw] = true; });
    const playedSet = {}; settledGWs.forEach(g => { playedSet[g.gw] = true; });
    const cal = {
      chips: D.events.map(e => ({
        gw: e.gw,
        title: "GW" + e.gw + " · " + e.month + " · " + this.dateShort(e.deadline),
        bg: playedSet[e.gw] ? "var(--s250)" : e.gw === cur.gameweek && (isLive || isProv) ? "var(--warn)"
          : e.gw === cur.next_gw ? "var(--accent)" : "var(--surface-2)",
        ink: playedSet[e.gw] ? "var(--ink-1)" : e.gw === cur.next_gw ? "#fff"
          : e.gw === cur.gameweek && (isLive || isProv) ? "#0b0b0b" : "var(--ink-muted)",
        weight: e.gw === cur.next_gw || e.gw === cur.gameweek ? 660 : 450,
        ring: brkAfter[e.gw] ? "3px 0 0 var(--warn)" : "none"
      })),
      foot: "Gameweeks per monthly pot — " + D.month_buckets.map(m => m.month + " " + m.gameweeks.length).join(" · ") +
        ". August only got two, December got six, so monthly stake is RM" + D.stakes.monthly.stake_per_gw +
        " per gameweek and every gameweek in the season carries the same RM" + (D.stakes.monthly.stake_per_gw * N) + " of monthly pot, fair for everybody."
    };

    /* ---- standings ---- */
    const standings = {
      sub: isPre ? N + " managers signed up" : "Overall points after GW" + D.settled.through_gw +
        (isLive || isProv ? " — settled figures, GW" + cur.gameweek + " is live above" : ""),
      empty: isPre, hasRows: !isPre,
      emptyNote: "Standings only fill up after GW1 final. First RM" + D.stakes.weekly.pot +
        " weekly pot settle same night, so don't forget to set team ah.",
      signups: D.managers.map(m => ({ name:m.display_name, team:m.team_name, ink:inkOf(m.id), weight:wOf(m.id) })),
      head: [ {label:"#",align:"left"}, {label:"Manager",align:"left"}, {label:"GW" + (lastGW ? lastGW.gw : ""),align:"right"},
              {label:"Total",align:"right"}, {label:"Behind",align:"right"}, {label:"★ Top 3",align:"right"}, {label:"Accrued",align:"right"} ],
      rows: D.rank.map((id, i) => {
        const sc = lastGW ? lastGW.scores[id] : null;
        const dns = sc ? sc.did_not_set : false;
        const pnl = D.ledger[id].accrued;
        return {
          pos: i + 1, name: byId[id].display_name, team: byId[id].team_name,
          ink: inkOf(id), weight: wOf(id), rowBg: rowBgOf(id), mark: markOf(id),
          gw: dns ? "0 ✕" : (sc && sc.points !== null && sc.points !== undefined ? sc.points : "—"),
          gwInk: dns ? "var(--crit)" : "var(--ink-1)", gwWeight: dns ? 620 : 450,
          total: D.totals[id], behind: D.behind[id] === 0 ? "—" : D.behind[id],
          podiums: D.podiums[id] === 0 ? "—" : D.podiums[id],
          pnl: this.sen(pnl),
          pnlInk: pnl > 0 ? "var(--good)" : pnl < 0 ? "var(--crit)" : "var(--ink-2)"
        };
      }),
      foot: "★ counts top-three finishes — for bragging only, no money. Miss the deadline also never mind — FPL roll your last team over and it score as usual. 0 ✕ only show up if somebody never enter a team at all, and that one still pay RM" +
        D.stakes.weekly.stake + ". Accrued only ah — nobody pay anything yet, we settle in May."
    };

    /* ---- PL table ---- */
    const formBg = { W:"var(--good)", D:"var(--ink-muted)", L:"var(--crit)" };
    const pl = {
      sub: D.pl_table.length ? "Derived from " + D.pl_table[0].p + " finished rounds" : "Opens with the first whistle",
      empty: D.pl_table.length === 0, hasRows: D.pl_table.length > 0,
      emptyNote: "Nobody kick ball yet. Table only shows up after GW1 finish — FPL API don't give one, so we build it from results ourselves. First up: Arsenal v Coventry City, " +
        this.dayKey(D.fixtures["1"][0].ko) + " at " + this.hhmm(D.fixtures["1"][0].ko) + " Malaysia time.",
      clubs: Object.keys(T).map(k => ({ name:T[k].name })),
      head: ["P","W","D","L","GD","Pts"],
      rows: D.pl_table.map(r => ({
        pos: r.pos, club: T[r.team].name, p:r.p, w:r.w, d:r.d, l:r.l,
        gd: (r.gd > 0 ? "+" : "") + r.gd, pts: r.pts,
        zone: r.pos <= 4 ? "var(--accent)" : r.pos >= 18 ? "var(--neg)" : "transparent",
        form: r.form.map(f => ({ l:f, bg:formBg[f] }))
      }))
    };

    /* ---- live block ---- */
    let live = null, showLive = false;
    if (F) {
      showLive = true;
      const age = (F._dev_age_seconds || 0) + Math.floor((Date.now() - S.liveSince) / 1000);
      const prov = F.state === "provisional";
      live = {
        gw: F.gw,
        bar: {
          title: prov ? "ALL MATCHES FINISHED" : "LIVE",
          detail: prov ? "bonus not confirmed · no money settles"
                       : F.matches_in_play + " matches in play",
          age: this.ago(age),
          dot: prov ? "var(--warn)" : "var(--crit)",
          anim: prov ? "none" : "pulse 1.5s ease-in-out infinite",
          ink: prov ? "var(--warn-ink)" : "var(--crit)",
          rule: prov ? "var(--warn)" : "var(--crit)",
          note: F.bonus_watch,
          onRefresh: () => this.setState({ liveSince: Date.now() })
        },
        rows: F.managers.map(m => ({
          points: m.live_points, name: byId[m.id].display_name,
          sub: "incl. +" + m.provisional_bonus_included + " provisional bonus" +
               (m.subs_pending ? " · " + m.subs_pending + " sub pending" : ""),
          played: m.played + "/11", inPlay: m.in_play, toPlay: m.to_play,
          toPlayInk: m.to_play >= 3 ? "var(--ink-1)" : "var(--ink-2)",
          toPlayWeight: m.to_play >= 3 ? 620 : 450,
          captain: m.captain.name, captainPts: m.captain.points + " ×" + m.captain.multiplier,
          captainNote: m.captain.fallback_to_vice ? "0 mins — vice " + m.captain.vice.name + " takes over at full time" : "",
          delta: m.rank_delta === 0 ? "—" : (m.rank_delta > 0 ? "▲ " : "▼ ") + Math.abs(m.rank_delta),
          deltaInk: m.rank_delta > 0 ? "var(--good)" : m.rank_delta < 0 ? "var(--crit)" : "var(--ink-muted)",
          ink: inkOf(m.id), weight: wOf(m.id), rowBg: rowBgOf(m.id), mark: markOf(m.id)
        })),
        tableFoot: "Provisional bonus already inside, that's why every number got the hairline. Auto-subs and vice captain only kick in when FPL close the gameweek, not now. Δ rank = where you'd sit overall if it end like this.",
        pot: {
          name: byId[F.pot_leader.manager].display_name,
          margin: "Leads the GW" + F.gw + " pot by " + F.pot_leader.margin + " from " +
            byId[F.pot_leader.over].display_name + " · " + this.rmFlat(D.stakes.weekly.pot) + " on the table",
          note: prov ? "Bonus can still move after the whistle and flip the pot. If it happen, this page will name names and keep it there forever — no arguing after that."
                     : "Nothing settle until every fixture says finished. Pot is shown only, not paid yet."
        },
        tickerSub: prov ? "All ten finished, awaiting confirmation" : F.matches_in_play + " in play · " +
          F.fixtures.filter(f => f.finished).length + " finished",
        ticker: F.fixtures.map(f => {
          const started = f.started, done = f.finished;
          const inPlay = started && !done;
          return {
            home: T[f.h].short, away: T[f.a].short,
            score: f.hs === null ? "–" : f.hs + " – " + f.as,
            state: done ? "full time" : inPlay ? f.minutes + "'" : this.hhmm((D.fixtures[String(F.gw)].find(x => x.h === f.h) || {}).ko || F.generated_at) + " MYT",
            stateInk: done ? "var(--ink-muted)" : inPlay ? "var(--crit)" : "var(--ink-muted)",
            hInk: "var(--ink-1)", aInk: "var(--ink-1)", hWeight: 450, aWeight: 450
          };
        }),
        caps: (() => {
          const byPlayer = {};
          F.managers.forEach(m => {
            const k = m.captain.name;
            if (!byPlayer[k]) byPlayer[k] = { player:k, owners:[], pts:m.captain.points, mult:m.captain.multiplier };
            byPlayer[k].owners.push(byId[m.id].short);
          });
          return Object.keys(byPlayer).map(k => {
            const c = byPlayer[k];
            return { player:c.player, owners: c.owners.join(", ") + " (" + c.owners.length + ")",
              pts: c.pts * c.mult,
              ink: c.pts * c.mult >= 20 ? "var(--good)" : c.pts === 0 ? "var(--crit)" : "var(--ink-1)" };
          });
        })(),
        bonus: F.fixtures.filter(f => f.bps_top3.length).map(f => ({
          fixture: T[f.h].short + " " + (f.hs === null ? "" : f.hs + "–" + f.as) + " " + T[f.a].short,
          players: f.bps_top3.map(p => ({ name:p.player, bps:p.bps, bonus:p.provisional_bonus }))
        }))
      };
    }

    /* ---- prediction ---- */
    const P = this.prediction;
    const confRule = c => c === "high" ? "var(--good)" : c === "medium" ? "var(--accent)" : "var(--axis)";
    const confInk  = c => c === "high" ? "var(--good)" : c === "medium" ? "var(--accent)" : "var(--ink-muted)";
    let pred = { empty:true, hasCall:false, title:"Weekly prediction", sub:"", record:"", toggleLabel:"",
                 showToggle:false, expanded:false, midRound:null, tag:"",
                 onToggle:()=>{}, emptyNote:"", verdict:{show:false}, calls:[], swing:{}, reasoning:"",
                 agrees:"", showProj:false, projections:[], projFoot:"" };
    if (P) {
      const res = P.result;
      /* A mid-round call is a different bet: half the round was already on the
       * board when it was made. The card has to say so, and the record beside
       * it is about blind calls only — see predict.py's settle_outstanding. */
      const mid = P.mode === "mid_round" ? P.mid_round : null;
      pred = {
        empty:false, hasCall:true,
        midRound: mid,
        tag: mid ? "MID-ROUND" : "",
        title: "GW" + P.gw + " call" + (mid ? " — from here" : "") + (res ? " — verdict" : ""),
        sub: mid
          ? "Called " + this.hhmm(P.generated_at) + " MYT with " + mid.remaining + " of " +
            mid.total + " matches still to kick off — made with " + mid.played +
            " already played, so it is not a blind call and does not count towards the record"
          : res ? "Published " + this.dateShort(P.generated_at) + ", scored once the gameweek went final"
                : "Published " + this.hhmm(P.generated_at) + " MYT, five minutes after the deadline and before the first kickoff",
        record: P.record.played
          ? "Called correctly " + P.record.exact + " of " + P.record.played +
            " · podium " + P.record.podium + " of " + P.record.played +
            " · pair " + P.record.pair + " of " + P.record.played
          : "No blind calls scored yet",
        toggleLabel: S.showProj ? "Hide projections" : "Show projections",
        showToggle: true, expanded: S.showProj,
        onToggle: () => this.setState({ showProj: !S.showProj }),
        verdict: res ? {
          show:true,
          tag: res.exact_hit ? "EXACT HIT" : res.podium_hit ? "PODIUM HIT" : "MISSED",
          rule: res.exact_hit ? "var(--good)" : res.podium_hit ? "var(--warn)" : "var(--crit)",
          bg: res.exact_hit ? "var(--tint)" : "var(--tint-warn)",
          text: "Called " + byId[P.call.first.manager].short + " then " + byId[P.call.second.manager].short +
            ". Actual: " + byId[res.actual_first].short + " then " + byId[res.actual_second].short +
            ". Mean rank error " + res.mean_rank_error + "."
        } : { show:false },
        calls: [
          { slot:"1ST", name: byId[P.call.first.manager].display_name, conf: P.call.first.confidence.toUpperCase(),
            confRule: confRule(P.call.first.confidence), confInk: confInk(P.call.first.confidence), ink: inkOf(P.call.first.manager) },
          { slot:"2ND", name: byId[P.call.second.manager].display_name, conf: P.call.second.confidence.toUpperCase(),
            confRule: confRule(P.call.second.confidence), confInk: confInk(P.call.second.confidence), ink: inkOf(P.call.second.manager) }
        ],
        swing: { name: P.call.swing_player.name,
          owners: "Owned by " + P.call.swing_player.owned_by.map(id => byId[id].short).join(" and "),
          why: P.call.swing_player.why },
        reasoning: P.call.reasoning,
        agrees: P.call.agrees_with_projection
          ? "The call agrees with the projection ranking. Model: " + P.model + "."
          : "The call disagrees with the projection ranking, deliberately. Model: " + P.model + ".",
        showProj: S.showProj,
        projections: P.projections.map(p => ({
          name: byId[p.manager].display_name, xp: p.xp.toFixed(1), captain: p.captain,
          banked: p.banked === undefined ? "" : p.banked.toFixed(1),
          remaining: p.remaining === undefined ? "" : p.remaining.toFixed(1),
          captainXp: p.captain_xp.toFixed(1), hits: p.hits ? "\u2212" + p.hits : "0",
          hitsInk: p.hits ? "var(--crit)" : "var(--ink-muted)",
          conc: p.concentration.players + " × " + p.concentration.club,
          ink: inkOf(p.manager), weight: wOf(p.manager), rowBg: rowBgOf(p.manager)
        })),
        projFoot: (mid ? "Banked is already on the board and cannot change; to come is " +
          "the projection over players whose match has not kicked off. xP is the two added " +
          "together. " : "") +
          "xP comes from code, not from the model: chance of playing × minutes, expected goal involvement adjusted for fixture, clean sheet chance for defenders, form, captain multiplier, minus hits. The model only rank and talk — it never make up a number."
      };
    } else {
      pred.title = "Weekly prediction";
      pred.sub = "Runs at deadline + 5 minutes";
      pred.emptyNote = "Nobody can see squads before the deadline, so the first call only comes out in the 90-minute window between the GW1 deadline and first kickoff. Every call gets marked after — record shows here whether it looks good or looks stupid.";
      pred.record = "No calls yet";
    }

    /* ---- rules tab (§7.2F) — explains the money, never restates it ----
     * Everything here reads out of D.rules, which build.py derives from N and
     * the real calendar. Nothing is typed in: a thirteenth manager rewrites
     * every figure on this tab without anyone touching copy. */
    const R = D.rules;
    const byPot = R.months.slice().sort((a, b) => a.pot - b.pot);
    const monthlyLow = byPot[0], monthlyHigh = byPot[byPot.length - 1];
    const potRow = (name, stake, pot, split, net, note) => ({
      name, stake, pot: this.rmFlat(pot),
      split: split.map(x => Math.round(x * 100) + "%").join(" / "),
      pays: net.map(v => rm(v)).join(" · "), note
    });
    const rules = {
      intro: "Every gameweek cost RM" + R.gameweek_cost + " — RM" + R.weekly_stake +
        " to the week, RM" + R.monthly_stake_per_gw + " to the month. Plus RM" +
        R.season_stake + " once for the season. Over " + D.checks.gameweeks_expected +
        " gameweeks that come to " + this.rmFlat(D.exposure.staked) + " each.",
      zeroSum: "League keep nothing. Every ringgit that leave one column land in somebody else column, " +
        "which is why every table here add up to RM0. If it ever don't, the site refuse to publish " +
        "instead of showing you a wrong number.",
      pots: [
        potRow("Weekly", "RM" + R.weekly_stake + " × " + N, D.stakes.weekly.pot,
               D.stakes.weekly.split, D.stakes.weekly.net,
               D.checks.gameweeks_expected + " times a season"),
        // The monthly pot is not one number — it follows how many gameweeks the
        // month has. Showing the current bucket here would read as "the"
        // monthly figure, so the rules tab shows the range and the table below
        // breaks it down month by month.
        {
          name: "Monthly", stake: "RM" + R.monthly_stake_per_gw + " per gameweek",
          pot: this.rmFlat(monthlyLow.pot) + "–" + this.rmFlat(monthlyHigh.pot),
          split: D.stakes.monthly.split.map(x => Math.round(x * 100) + "%").join(" / "),
          pays: rm(monthlyLow.net[0]) + " … " + rm(monthlyHigh.net[0]),
          note: R.months.length + " buckets, Aug to May — depend how many gameweeks"
        },
        potRow("Season", "RM" + R.season_stake + " × " + N, D.stakes.season.pot,
               D.stakes.season.split, D.stakes.season.net, "Only settle after GW38")
      ],
      months: R.months.map(m => ({
        name: this.monthName(m.month), gws: m.gameweeks,
        stake: this.rmFlat(m.stake), pot: this.rmFlat(m.pot),
        first: rm(m.net[0]), second: rm(m.net[1]), rest: rm(-m.stake)
      })),
      monthsNote: "Stake is per gameweek, not per month. December got " +
        Math.max.apply(null, R.months.map(m => m.gameweeks)) + " gameweeks and August got " +
        Math.min.apply(null, R.months.map(m => m.gameweeks)) +
        " — flat monthly fee would make one August gameweek worth a few December ones for the same effort.",
      tiebreak: R.tiebreak.map(t => ({
        level: t.level, label: t.label.charAt(0).toUpperCase() + t.label.slice(1), who: t.direction
      })).concat([{ level: R.tiebreak.length + 1, label: "Split the pot", who: "ours" }]),
      floors: R.floors.map(f => ({
        n: f.n,
        seasonThird: rm(f.season_third), seasonInk: f.season_third < 0 ? "var(--crit)" : "var(--good)",
        weeklySecond: rm(f.weekly_second), weeklyInk: f.weekly_second < 0 ? "var(--crit)" : "var(--good)",
        mark: f.is_us ? "us" : "", isUs: f.is_us,
        rowBg: f.is_us ? "var(--tint)" : "transparent"
      })),
      floorNote: "Any paid place only worth having while its share beat one stake. It get safer as we grow " +
        "and break if we shrink. Build check every pot every run and stop rather than publish a podium that cost money.",
      settleNote: N + " people settling one by one could be " + R.naive_payments +
        " separate payments. Site work out the shortest set instead: at most " + R.max_payments +
        ". Same answer every time it run, so nothing to negotiate.",
      steps: [
        "Each weekly pot, as soon as that gameweek final.",
        "Each monthly pot, but only once every gameweek in that month final.",
        "The season pot, only after GW" + D.checks.gameweeks_expected + ".",
        "Your balance — those three added up, and checked to sum to RM0 across the league."
      ],
      best: this.rm(D.exposure.best), worst: this.rm(D.exposure.worst),
      staked: this.rmFlat(D.exposure.staked),
      bestNote: "Best case break down as " + this.rmFlat(R.best_breakdown.weekly) + " from the weekly pots, " +
        this.rmFlat(R.best_breakdown.monthly) + " from the monthly, " + this.rmFlat(R.best_breakdown.season) +
        " from the season. Nobody going to do that — it just show the shape: downside is capped and known " +
        "from day one, upside is a few times bigger.",
      terms: [
        { term: "Provisional", meaning: "Live, mid-match, still can change. Bonus not confirmed until the match end, auto-subs and vice captain only apply when the whole gameweek close." },
        { term: "Accrued", meaning: "Settled maths, already in the book. You owe or you are owed. Won't change unless a correction get posted, and corrections come in as their own visible row." },
        { term: "Projected", meaning: "What something would pay if it end today. Deliberately kept OUT of your balance. Season pot sit here until GW38." },
        { term: "Settled", meaning: "Actually paid. That happen once, in May." }
      ]
    };

    /* ---- season tab ---- */
    const L = D.ledger[you];
    const owes = L.accrued < 0;
    const hero = {
      label: "ACCRUED BALANCE · " + byId[you].display_name.toUpperCase() + " · NOTHING PAID YET",
      value: this.sen2(L.accrued),
      position: L.accrued === 0 ? "You are square with the league."
        : owes ? "You owe " + this.senAbs(L.accrued) + " — only settle after GW38."
               : "You are owed " + this.senAbs(L.accrued) + " — only settle after GW38.",
      ink: L.accrued > 0 ? "var(--good)" : L.accrued < 0 ? "var(--crit)" : "var(--ink-1)",
      delta: this.sen(L.delta_last_gw),
      deltaInk: L.delta_last_gw > 0 ? "var(--good)" : L.delta_last_gw < 0 ? "var(--crit)" : "var(--ink-2)",
      deltaNote: lastGW ? "since GW" + lastGW.gw : "",
      projected: "On current standings the season pot project " + this.sen(L.projected_season) + " — showing only, not inside the accrued balance until GW38 final.",
      kpis: [
        { label:"LEAGUE RANK", value: (D.rank.indexOf(you) + 1),
          sub: D.behind[you] === 0 ? "leading" : D.behind[you] + " points behind" },
        { label:"TOTAL POINTS", value: D.totals[you], sub:"league average " + D.stats.avg_points },
        { label:"WEEKS WON", value: D.weeks_won[you], sub:"of " + settledGWs.length + " played" }
      ]
    };

    /* month pot — live/provisional feed takes over when the month has live gameweeks */
    const mp = F && F.month_pot ? F.month_pot : null;
    const settledMonth = D.months.length ? D.months[D.months.length - 1] : null;
    let pot;
    if (mp) {
      const max = Math.max.apply(null, Object.keys(mp.totals).map(k => mp.totals[k])) || 1;
      pot = {
        title: this.monthName(mp.month) + " pot", tag:"PROVISIONAL", tagRule:"var(--warn)", tagInk:"var(--warn-ink)",
        sub: "Month to date including GW" + F.gw + " live points · " + mp.gameweeks + " gameweeks in the bucket",
        potLabel: this.rmFlat(mp.pot), prizeLabel: rm(mp.net[0]) + " · " + rm(mp.net[1]),
        hasBars: true,
        rows: mp.order.map((id, i) => ({
          pos: i + 1, name: byId[id].display_name, ink: inkOf(id), weight: wOf(id),
          width: Math.round(mp.totals[id] / max * 100) + "%", fill: fillOf(id),
          pts: mp.totals[id], title: byId[id].display_name + " · " + mp.totals[id] + " points this month",
          money: i === 0 ? rm(mp.net[0]) : i === 1 ? rm(mp.net[1]) : rm(-mp.stake),
          moneyInk: i < 2 ? "var(--pos)" : "var(--ink-muted)", moneyWeight: i < 2 ? 620 : 450
        })),
        isTable: S.potView === "table",
        isChart: S.potView === "chart" && S.vw >= 560,
        isList: S.potView === "chart" && S.vw < 560,
        onView: () => this.setState({ potView: S.potView === "chart" ? "table" : "chart" }),
        viewLabel: S.potView === "chart" ? "Table view" : (S.vw < 560 ? "List view" : "Chart view"),
        callout: mp.callout, calloutBg:"var(--tint-warn)",
        foot: "Money shown is what each position would pay if the month end right now. Only settle when the last gameweek in the bucket is final."
      };
    } else if (settledMonth) {
      const max = Math.max.apply(null, Object.keys(settledMonth.totals).map(k => settledMonth.totals[k])) || 1;
      pot = {
        title: this.monthName(settledMonth.month) + " pot", tag:"SETTLED", tagRule:"var(--good)", tagInk:"var(--good)",
        sub: settledMonth.gameweeks.length + " gameweeks · RM" + settledMonth.stake + " each · 70/30",
        potLabel: this.rmFlat(settledMonth.pot), prizeLabel: rm(settledMonth.net[0]) + " · " + rm(settledMonth.net[1]),
        hasBars: true,
        rows: settledMonth.order.map((id, i) => ({
          pos: i + 1, name: byId[id].display_name, ink: inkOf(id), weight: wOf(id),
          width: Math.round(settledMonth.totals[id] / max * 100) + "%", fill: fillOf(id),
          pts: settledMonth.totals[id], title: byId[id].display_name + " · " + settledMonth.totals[id] + " points in " + settledMonth.month,
          money: i === 0 ? rm(settledMonth.net[0]) : i === 1 ? rm(settledMonth.net[1]) : rm(-settledMonth.stake),
          moneyInk: i < 2 ? "var(--pos)" : "var(--ink-muted)", moneyWeight: i < 2 ? 620 : 450
        })),
        isTable: S.potView === "table",
        isChart: S.potView === "chart" && S.vw >= 560,
        isList: S.potView === "chart" && S.vw < 560,
        onView: () => this.setState({ potView: S.potView === "chart" ? "table" : "chart" }),
        viewLabel: S.potView === "chart" ? "Table view" : (S.vw < 560 ? "List view" : "Chart view"),
        callout: settledMonth.callout, calloutBg:"var(--tint)",
        foot: monthCurrent.note
      };
    } else {
      pot = {
        title: this.monthName(monthCurrent.month) + " pot", tag:"OPENS GW" + monthCurrent.opens_gw,
        tagRule:"var(--axis)", tagInk:"var(--ink-muted)",
        sub: monthCurrent.gameweeks + " gameweeks · RM" + monthCurrent.stake + " each · 70/30",
        potLabel: this.rmFlat(monthCurrent.pot),
        prizeLabel: rm(monthCurrent.net[0]) + " · " + rm(monthCurrent.net[1]),
        hasBars:false, rows:[], isChart:false, isList:false, isTable:false,
        onView: () => {}, viewLabel:"",
        callout: monthCurrent.note, calloutBg:"var(--surface-2)",
        foot: "Bars only fill up once GW" + monthCurrent.opens_gw + " scores come in. Nobody owe anybody before that."
      };
    }

    const maxAbs = Math.max.apply(null, D.managers.map(m => Math.abs(D.ledger[m.id].accrued))) || 1;
    const ledgerRows = D.managers.slice().sort((a,b) => D.ledger[b.id].accrued - D.ledger[a.id].accrued).map(m => {
      const v = D.ledger[m.id].accrued, w = Math.round(Math.abs(v) / maxAbs * 100) + "%";
      return {
        name: byId[m.id].display_name, ink: inkOf(m.id), weight: wOf(m.id),
        negW: v < 0 ? w : "0%", posW: v > 0 ? w : "0%",
        negFill: v < 0 ? "var(--neg)" : "transparent", posFill: v > 0 ? "var(--pos)" : "transparent",
        value: this.sen(v), valInk: v > 0 ? "var(--pos)" : v < 0 ? "var(--neg)" : "var(--ink-2)",
        weeklyTxt: this.sen(D.ledger[m.id].weekly), monthlyTxt: this.sen(D.ledger[m.id].monthly),
        title: byId[m.id].display_name + " — weekly " + this.sen(D.ledger[m.id].weekly) +
          ", monthly " + this.sen(D.ledger[m.id].monthly) + ", accrued " + this.sen2(v)
      };
    });
    const ledger = { rows: ledgerRows,
      isChart: S.ledgerView === "chart" && S.vw >= 560,
      isList: S.ledgerView === "chart" && S.vw < 560,
      isTable: S.ledgerView === "table",
      onView: () => this.setState({ ledgerView: S.ledgerView === "chart" ? "table" : "chart" }),
      viewLabel: S.ledgerView === "chart" ? "Table view" : "Chart view",
      sub: "Accrued weekly and monthly pots, centred on RM0 — none of it paid yet",
      foot: "Blue side is owed money, red side owe money, and both sides always equal — every ringgit came out of the eight stakes. League keep nothing, and nothing move until May." };

    const weekly = {
      sub: "70/30 of " + this.rmFlat(D.stakes.weekly.pot) + " — top two get paid",
      rows: settledGWs.slice().reverse().map(g => {
        const w = g.winners[0];
        // Second place is a paid place, so name it. A tie for first swallows it:
        // the two winners take both shares between them.
        const up = (g.runners_up || [])[0];
        return { gw:g.gw, winner: byId[w].display_name, ink: inkOf(w),
          // What this gameweek paid, not what today's league size would pay.
          points: g.scores[w].points, prize: this.sen(g.winner_net),
          hasSecond: !!up,
          second: up ? byId[up].display_name : "",
          secondInk: up ? inkOf(up) : "var(--ink-2)",
          secondPoints: up ? g.scores[up].points : "",
          secondPrize: up ? this.sen(g.runner_up_net) : "",
          chip: g.scores[w].chip ? g.scores[w].chip.toUpperCase() : "",
          hasChip: !!g.scores[w].chip,
          gwNote: g.note ? g.note.toUpperCase() : "", hasGWNote: !!g.note,
          bonusNote: g.bonus_change
            ? "Bonus changed the winner: " + byId[g.bonus_change.from].short + " → " +
              byId[g.bonus_change.to].short + ", confirmed " + this.hhmm(g.bonus_change.at) + " MYT."
            : "",
          hasBonusNote: !!g.bonus_change,
          note: g.tiebreak ? g.tiebreak.text
            : g.winners.length > 1 ? "Split " + g.winners.length + " ways — they take both shares"
            : "Everybody else pay RM" + D.stakes.weekly.stake };
      }),
      foot: "Every gameweek cost RM15 — RM10 to the week, RM5 to the month. Week pay top two, 70/30. Tie? Goals first, then assists, then goals conceded, then cards. Official FPL rule, cannot argue."
    };

    const playedMonths = D.month_buckets.filter(mb => settledGWs.some(g => g.month === mb.month));
    const range = playedMonths.some(mb => mb.month === S.detailRange) ? S.detailRange : "all";
    const shownGWs = range === "all" ? settledGWs : settledGWs.filter(g => g.month === range);
    const detail = {
      rangeOptions: [{ v:"all", label:"All " + settledGWs.length + " GWs" }]
        .concat(playedMonths.map(mb => ({ v:mb.month, label:this.monthName(mb.month) })))
        .map(o => ({ label:o.label, key:o.v, selected: range === o.v,
          onClick:() => this.setState({ detailRange:o.v }),
          bg: range === o.v ? "var(--ink-1)" : "var(--surface-1)",
          ink: range === o.v ? "var(--plane)" : "var(--ink-2)",
          border: range === o.v ? "var(--ink-1)" : "var(--border)" })),
      rangeNote: range === "all"
        ? "Showing every settled gameweek. Weekly, monthly and accrued columns are always season to date."
        : "Showing " + this.monthName(range) + " only — " + shownGWs.length +
          " gameweek(s). Weekly, monthly and accrued columns stay season to date, not filtered.",
      cols: shownGWs.map(g => "GW" + g.gw),
      pots: shownGWs.map(g => this.rmFlat(g.pot)),
      rows: D.rank.map(id => ({
        name: byId[id].display_name, ink: inkOf(id), weight: wOf(id), rowBg: rowBgOf(id), mark: markOf(id),
        stickyBg: id === you ? "var(--surface-2)" : "var(--surface-1)",
        cells: shownGWs.map(g => {
          const sc = g.scores[id] || {}, won = g.winners.indexOf(id) >= 0;
          const absent = sc.points === null || sc.points === undefined;
          return { text: sc.did_not_set ? "0 ✕" : absent ? "—" : sc.points + (won ? " ★" : "") + (sc.chip ? " ◆" : ""),
            title: byId[id].short + " GW" + g.gw + " — " + (sc.did_not_set ? "never set team" : absent ? "not in the league yet" : sc.points + " pts") +
              (sc.hits ? ", " + sc.hits + " pt hit" : "") + ", " + (sc.transfers || 0) + " transfer(s)" +
              (sc.chip ? ", played " + sc.chip : "") + (g.note ? " · " + g.note : ""),
            ink: sc.did_not_set ? "var(--crit)" : absent ? "var(--ink-muted)" : won ? "var(--good)" : "var(--ink-1)",
            weight: won || sc.did_not_set ? 640 : 450 };
        }),
        total: D.totals[id], weekly: this.sen(D.ledger[id].weekly), monthly: this.sen(D.ledger[id].monthly),
        pnl: this.sen(D.ledger[id].accrued),
        pnlInk: D.ledger[id].accrued > 0 ? "var(--good)" : D.ledger[id].accrued < 0 ? "var(--crit)" : "var(--ink-2)"
      }))
    };

    const moneyOpen = S.moneyOpen === null ? (isPre || settledGWs.length <= 3) : S.moneyOpen;
    const st = D.stakes;
    const money = {
      sub: "Read straight from the stakes, so when somebody new join the numbers fix themselves",
      onToggle: () => this.setState({ moneyOpen: !moneyOpen }),
      toggleLabel: moneyOpen ? "Collapse" : "Expand",
      expanded: moneyOpen,
      open: moneyOpen,
      pots: [
        { name:"Weekly", stake:"RM" + st.weekly.stake + " × " + N, prize: rm(st.weekly.net[0]) + " · " + rm(st.weekly.net[1]),
          note:"70/30 of " + this.rmFlat(st.weekly.pot) + ", 38 times a season" },
        { name:"Monthly", stake:"RM" + st.monthly.stake_per_gw + " per gameweek", prize: rm(st.monthly.net[0]) + " · " + rm(st.monthly.net[1]),
          note:"70/30 of RM" + st.monthly.stake_per_gw + " × gameweeks × " + N + " — ten buckets, Aug all the way to May" },
        { name:"Season", stake:"RM" + st.season.stake + " × " + N, prize: rm(st.season.net[0]) + " · " + rm(st.season.net[1]) + " · " + rm(st.season.net[2]),
          note:"60/25/15 of " + this.rmFlat(st.season.pot) + ", only settle after GW38" }
      ],
      oneLiner: "Every gameweek cost RM15 — RM10 to the week, RM5 to the month. Every pot last paid place stay above 1/" + N + " of that pot, so nobody get paid and still lose money.",
      staked: this.rmFlat(D.exposure.staked), best: rm(D.exposure.best), worst: rm(D.exposure.worst),
      zeroInk: (isLive || isProv) ? "var(--ink-muted)" : D.checks.zero_sum ? "var(--good)" : "var(--crit)",
      zeroLabel: (isLive || isProv) ? "Settles at full time"
        : D.checks.zero_sum ? "Book balances · RM0" : "Book does not balance",
      settledLine: isPre ? "Nothing settled yet · " + D.checks.gameweeks_present + " of " + D.checks.gameweeks_expected + " gameweeks scheduled"
        : "Settled through GW" + D.settled.through_gw + " · " + D.settled.projected,
      ties: [
        {n:1,rule:"Goals scored in the gameweek",dir:"most"},
        {n:2,rule:"Assists in the gameweek",dir:"most"},
        {n:3,rule:"Goals conceded, first XI only",dir:"fewest"},
        {n:4,rule:"Yellow and red cards",dir:"fewest"},
        {n:5,rule:"Split the pot",dir:"ours"}
      ]
    };

    /* §7.2G statement — accrual ledger, per manager */
    const stRows = (D.ledger[you].statement || []).map(r => ({
      date: this.dateShort(r.date + "T00:00:00Z"),
      event: r.type === "weekly" ? "GW" + r.gw + " weekly" : this.monthName(r.month) + " monthly",
      detail: r.detail,
      amount: this.sen2(r.amount),
      amountInk: r.amount > 0 ? "var(--pos)" : r.amount < 0 ? "var(--neg)" : "var(--ink-2)",
      balance: this.sen2Flat(r.balance),
      balanceInk: r.balance > 0 ? "var(--pos)" : r.balance < 0 ? "var(--neg)" : "var(--ink-2)"
    }));
    const stmt = {
      has: stRows.length > 0, empty: stRows.length === 0,
      who: byId[you].display_name,
      head: L.accrued === 0 ? byId[you].display_name + " is square with the league"
        : L.accrued < 0 ? byId[you].display_name + " owes " + this.senAbs(L.accrued)
                        : byId[you].display_name + " is owed " + this.senAbs(L.accrued),
      headInk: L.accrued > 0 ? "var(--good)" : L.accrued < 0 ? "var(--crit)" : "var(--ink-1)",
      sub: "One row per settled event, oldest first. Change the You selector up top to read somebody else's line.",
      rows: stRows,
      emptyNote: "Nothing settled yet, so nothing in the book. First row appear after GW1 go final.",
      corrections: (D.corrections || []).length,
      correctionNote: (D.corrections || []).length
        ? (D.corrections || []).length + " correction(s) posted — shown as their own rows, original entries never deleted."
        : "No corrections posted. If we ever find a mistake, it comes in as a new row with a reason — we never quietly rewrite an old one.",
      onCSV: () => this.downloadCSV(
        "superf-statement-" + you + ".csv",
        [["date","event","detail","amount_rm","balance_rm"]].concat(
          (D.ledger[you].statement || []).map(r => [ r.date,
            r.type === "weekly" ? "GW" + r.gw + " weekly" : r.month + " monthly",
            r.detail, (r.amount / 100).toFixed(2), (r.balance / 100).toFixed(2) ])))
    };

    /* §7.2H settlement sheet */
    const sPay = (D.settlement && D.settlement.payments) || [];
    const settle = {
      has: sPay.length > 0, empty: sPay.length === 0,
      final: !!(D.settlement && D.settlement.settled),
      tag: (D.settlement && D.settlement.settled) ? "FINAL" : "NOT FINAL",
      tagRule: (D.settlement && D.settlement.settled) ? "var(--good)" : "var(--axis)",
      tagInk: (D.settlement && D.settlement.settled) ? "var(--good)" : "var(--ink-muted)",
      title: (D.settlement && D.settlement.settled) ? "Settlement sheet" : "Settlement preview",
      sub: (D.settlement && D.settlement.settled)
        ? "GW38 is final. This is who pays whom — " + sPay.length + " transfers, nothing more."
        : "If the season ended today, " + sPay.length + " payments would settle the whole league. Nobody owe anything yet.",
      rows: sPay.map(p => ({
        from: byId[p.from].display_name, to: byId[p.to].display_name,
        amount: this.sen2Flat(p.amount),
        fromInk: inkOf(p.from), toInk: inkOf(p.to),
        fromWeight: wOf(p.from), toWeight: wOf(p.to)
      })),
      total: this.sen2Flat(sPay.reduce((a,p) => a + p.amount, 0)),
      foot: "Minimum-transfer set: at most " + (N - 1) + " payments instead of up to " + (N * (N - 1)) +
        ". Sorted by amount then id, so the sheet come out the same every time you run it.",
      emptyNote: "Nothing to settle yet. Once a gameweek go final the preview shows who would pay whom.",
      onCSV: () => this.downloadCSV("superf-settlement.csv",
        [["from","to","amount_rm"]].concat(sPay.map(p => [p.from, p.to, (p.amount / 100).toFixed(2)])))
    };

    const season = {
      empty: isPre, hasData: !isPre,
      emptyNote: "No money moved yet. First weekly pot settle after GW1 on " +
        this.dayKey(D.events[0].deadline) + ", then August's RM80 monthly pot two gameweeks after that.",
      foot: settledGWs.length >= 6
        ? "" : "Season race chart only comes out after six gameweeks settled — two lines got nothing to show, waste space only."
    };

    return {
      league: D.league, managerOpts: D.managers.map(m => ({ id:m.id, name:m.display_name })),
      you, cmp,
      onYou: e => { const v = e.target.value;
        const cmp = v === S.cmp ? D.managers.find(m => m.id !== v).id : S.cmp;
        savePrefs({ you:v, cmp }); this.setState({ you:v, cmp }); },
      onCmp: e => { const v = e.target.value;
        const you = v === S.you ? D.managers.find(m => m.id !== v).id : S.you;
        savePrefs({ you, cmp:v }); this.setState({ cmp:v, you }); },
      toggleTheme: () => { const theme = S.theme === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", theme);
        savePrefs({ theme }); this.setState({ theme }); },
      chrome: { dot: sm.dot, dotAnim: sm.anim, dotInk: sm.ink, stateText: sm.text, stateSub: stateSub,
                themeLabel: S.theme === "dark" ? "Light" : "Dark",
                themeAria: S.theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme" },
      tabs, banner,
      isGW: S.tab === "gw", isSeason: S.tab === "season", isRules: S.tab === "rules",
      activeTabId: "tab-" + S.tab,
      showCountdown: !showLive, cd, brk,
      showLive, live, fx, cal, standings, pl, pred,
      hero, pot, ledger, weekly, detail, stmt, settle, money, season, rules,
      footer: "Generated " + this.dateShort(D.generated_at) + " · league 310479 · " + N +
        " managers · everything in Asia/Kuala_Lumpur (UTC+8) · " + D.checks.gameweeks_present +
        " of " + D.checks.gameweeks_expected + " gameweeks in the calendar · " +
        "scores from the official FPL API, money computed in this repo."
    };
  }
}

const root = document.getElementById("root");
const template = document.getElementById("template");
const dashboard = new Dashboard(root, template);

dashboard.boot().catch((error) => {
  console.error(error);
  root.innerHTML =
    '<div style="max-width:640px;margin:80px auto;padding:0 24px;font:15px/1.6 system-ui,-apple-system,sans-serif;color:var(--ink-2)">' +
    '<div style="font-size:21px;font-weight:670;color:var(--ink-1);margin-bottom:10px">SuperF</div>' +
    "<p>Could not load <code>data.json</code>. The weekly job may not have run yet — " +
    "nothing is published until the ledger balances, so a missing file means the " +
    "build refused rather than that a number is wrong.</p>" +
    '<p style="color:var(--ink-muted);font-size:13px">' +
    String(error) +
    "</p></div>";
});
