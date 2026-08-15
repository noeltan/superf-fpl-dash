/* SuperF — FPL League Dashboard.
 *
 * The view below (renderVals and everything it builds) is the prototype's own
 * code, unchanged except where noted in the commit that introduced this file:
 * the mock DATA / LIVE / PREDICTION objects are replaced by fetch(), the dev
 * state switcher is gone, `locked` was added to the state map because §11.1
 * defines five states and the map only had four, and two table cells learned to
 * render a dash for a manager who was not in the league yet.
 *
 * Derived-value policy is unchanged: the view formats (RM, the true minus sign,
 * dates, MYT conversion) and does layout maths only. It never computes money,
 * ranking or aggregation. Those arrive settled in data.json.
 */

import { render } from "./runtime.js";
import { PROXY_BASE } from "./config.js";
import * as liveFeed from "./live.js";

const POLL_INTERVAL_MS = 60000;

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

    const preferred = data.managers.find((m) => m.id === "noel") || data.managers[0];
    const other = data.managers.find((m) => m.id !== preferred.id) || preferred;
    this.state = {
      tab: "gw",
      you: preferred.id,
      cmp: other.id,
      fxGW: data.current.state === "final" || data.current.state === "upcoming"
        ? data.current.next_gw
        : data.current.gameweek,
      theme: this.detectTheme(),
      showProj: false,
      moneyOpen: null,
      tick: 0,
      liveSince: Date.now(),
    };

    this.render();
    this.startClock();
    this.startLive();
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

  state = { tab:"gw", you:null, cmp:null, fxGW:null, theme:"light",
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
    const stateSub = isPre ? "GW1 not played"
      : cur.state === "locked" ? "GW" + cur.next_gw + " locked · first kickoff soon"
      : isFinal ? "GW" + cur.gameweek + " settled · GW" + cur.next_gw + " next"
      : "GW" + cur.gameweek + " in progress";

    const tabs = [["gw","Gameweek"],["season","Season & money"]].map(([k,label]) => ({
      label, onClick: () => this.setState({ tab:k }),
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
      note: isPre
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
              {label:"Total",align:"right"}, {label:"Behind",align:"right"}, {label:"★ Top 3",align:"right"}, {label:"P&L",align:"right"} ],
      rows: D.rank.map((id, i) => {
        const sc = lastGW ? lastGW.scores[id] : null;
        const dns = sc ? sc.did_not_set : false;
        const pnl = D.ledger[id].total;
        return {
          pos: i + 1, name: byId[id].display_name, team: byId[id].team_name,
          ink: inkOf(id), weight: wOf(id), rowBg: rowBgOf(id), mark: markOf(id),
          gw: dns ? "0 ✕" : (sc && sc.points !== null && sc.points !== undefined ? sc.points : "—"),
          gwInk: dns ? "var(--crit)" : "var(--ink-1)", gwWeight: dns ? 620 : 450,
          total: D.totals[id], behind: D.behind[id] === 0 ? "—" : D.behind[id],
          podiums: D.podiums[id] === 0 ? "—" : D.podiums[id],
          pnl: rm(pnl),
          pnlInk: pnl > 0 ? "var(--good)" : pnl < 0 ? "var(--crit)" : "var(--ink-2)"
        };
      }),
      foot: "★ counts top-three finishes — for bragging only, no money. Forget to set team? Reads 0 ✕ and you still pay RM" +
        D.stakes.weekly.stake + ". P&L here is banked money only, no projection."
    };

    /* ---- PL table ---- */
    const formBg = { W:"var(--good)", D:"var(--ink-muted)", L:"var(--crit)" };
    const pl = {
      sub: isPre ? "Opens with the first whistle" : "Derived from " + D.pl_table[0].p + " finished rounds",
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
                 onToggle:()=>{}, emptyNote:"", verdict:{show:false}, calls:[], swing:{}, reasoning:"",
                 agrees:"", showProj:false, projections:[], projFoot:"" };
    if (P) {
      const res = P.result;
      pred = {
        empty:false, hasCall:true,
        title: "GW" + P.gw + " call" + (res ? " — verdict" : ""),
        sub: res ? "Published " + this.dateShort(P.generated_at) + ", scored once the gameweek went final"
                 : "Published " + this.hhmm(P.generated_at) + " MYT, five minutes after the deadline and before the first kickoff",
        record: "Called correctly " + P.record.exact + " of " + P.record.played +
                " · podium " + P.record.podium + " of " + P.record.played +
                " · pair " + P.record.pair + " of " + P.record.played,
        toggleLabel: S.showProj ? "Hide projections" : "Show projections",
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
          captainXp: p.captain_xp.toFixed(1), hits: p.hits ? "\u2212" + p.hits : "0",
          hitsInk: p.hits ? "var(--crit)" : "var(--ink-muted)",
          conc: p.concentration.players + " × " + p.concentration.club,
          ink: inkOf(p.manager), weight: wOf(p.manager), rowBg: rowBgOf(p.manager)
        })),
        projFoot: "xP comes from code, not from the model: chance of playing × minutes, expected goal involvement adjusted for fixture, clean sheet chance for defenders, form, captain multiplier, minus hits. The model only rank and talk — it never make up a number."
      };
    } else {
      pred.title = "Weekly prediction";
      pred.sub = "Runs at deadline + 5 minutes";
      pred.emptyNote = "Nobody can see squads before the deadline, so the first call only comes out in the 90-minute window between the GW1 deadline and first kickoff. Every call gets marked after — record shows here whether it looks good or looks stupid.";
      pred.record = "No calls yet";
      pred.toggleLabel = "How it works";
    }

    /* ---- season tab ---- */
    const L = D.ledger[you];
    const hero = {
      label: "YOUR SEASON P&L · " + byId[you].display_name.toUpperCase(),
      value: rm(L.total),
      ink: L.total > 0 ? "var(--good)" : L.total < 0 ? "var(--crit)" : "var(--ink-1)",
      delta: rm(L.delta_last_gw),
      deltaInk: L.delta_last_gw > 0 ? "var(--good)" : L.delta_last_gw < 0 ? "var(--crit)" : "var(--ink-2)",
      deltaNote: lastGW ? "since GW" + lastGW.gw : "",
      projected: "On current standings season pot project " + rm(L.projected_season) + " — showing only, nothing banked until GW38 final.",
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
        hasBars:false, rows:[],
        callout: monthCurrent.note, calloutBg:"var(--surface-2)",
        foot: "Bars only fill up once GW" + monthCurrent.opens_gw + " scores come in. Nobody owe anybody before that."
      };
    }

    const maxAbs = Math.max.apply(null, D.managers.map(m => Math.abs(D.ledger[m.id].total))) || 1;
    const ledgerRows = D.managers.slice().sort((a,b) => D.ledger[b.id].total - D.ledger[a.id].total).map(m => {
      const v = D.ledger[m.id].total, w = Math.round(Math.abs(v) / maxAbs * 100) + "%";
      return {
        name: byId[m.id].display_name, ink: inkOf(m.id), weight: wOf(m.id),
        negW: v < 0 ? w : "0%", posW: v > 0 ? w : "0%",
        negFill: v < 0 ? "var(--neg)" : "transparent", posFill: v > 0 ? "var(--pos)" : "transparent",
        value: rm(v), valInk: v > 0 ? "var(--pos)" : v < 0 ? "var(--neg)" : "var(--ink-2)",
        title: byId[m.id].display_name + " — weekly " + rm(D.ledger[m.id].weekly) +
          ", monthly " + rm(D.ledger[m.id].monthly) + ", banked " + rm(v)
      };
    });
    const ledger = { rows: ledgerRows,
      sub: "Banked weekly and monthly pots only, centred on RM0",
      foot: "Blue side up, red side down, and both sides always equal — every ringgit somebody won came out of the eight stakes. League keep nothing." };

    const weekly = {
      sub: "Top score take the whole " + this.rmFlat(D.stakes.weekly.pot),
      rows: settledGWs.slice().reverse().map(g => {
        const w = g.winners[0];
        return { gw:g.gw, winner: byId[w].display_name, ink: inkOf(w),
          points: g.scores[w].points, prize: rm(D.stakes.weekly.net[0]),
          note: g.tiebreak ? g.tiebreak.text
            : g.winners.length > 1 ? "Split " + g.winners.length + " ways"
            : "Everybody else pay RM" + D.stakes.weekly.stake + (g.note ? " · " + g.note : "") };
      }),
      foot: "Every gameweek cost RM10 — RM5 to the week, RM5 to the month. Tie? Goals first, then assists, then goals conceded, then cards. Official FPL rule, cannot argue."
    };

    const detail = {
      cols: settledGWs.map(g => "GW" + g.gw),
      pots: settledGWs.map(g => this.rmFlat(g.pot)),
      rows: D.rank.map(id => ({
        name: byId[id].display_name, ink: inkOf(id), weight: wOf(id), rowBg: rowBgOf(id), mark: markOf(id),
        cells: settledGWs.map(g => {
          const sc = g.scores[id] || {}, won = g.winners.indexOf(id) >= 0;
          const absent = sc.points === null || sc.points === undefined;
          return { text: sc.did_not_set ? "0 ✕" : absent ? "—" : sc.points + (won ? " ★" : ""),
            ink: sc.did_not_set ? "var(--crit)" : absent ? "var(--ink-muted)"
              : won ? "var(--good)" : "var(--ink-1)",
            weight: won || sc.did_not_set ? 640 : 450 };
        }),
        total: D.totals[id], weekly: rm(D.ledger[id].weekly), monthly: rm(D.ledger[id].monthly),
        pnl: rm(D.ledger[id].total),
        pnlInk: D.ledger[id].total > 0 ? "var(--good)" : D.ledger[id].total < 0 ? "var(--crit)" : "var(--ink-2)"
      }))
    };

    const moneyOpen = S.moneyOpen === null ? (isPre || settledGWs.length <= 3) : S.moneyOpen;
    const st = D.stakes;
    const money = {
      sub: "Read straight from the stakes, so if a 9th manager join the numbers fix themselves",
      onToggle: () => this.setState({ moneyOpen: !moneyOpen }),
      toggleLabel: moneyOpen ? "Collapse" : "Expand",
      open: moneyOpen,
      pots: [
        { name:"Weekly", stake:"RM" + st.weekly.stake + " × " + N, prize: rm(st.weekly.net[0]),
          note:"Top score take everything, " + this.rmFlat(st.weekly.pot) + ", 38 times a season" },
        { name:"Monthly", stake:"RM" + st.monthly.stake_per_gw + " per gameweek", prize: rm(st.monthly.net[0]) + " · " + rm(st.monthly.net[1]),
          note:"70/30 of RM" + st.monthly.stake_per_gw + " × gameweeks × " + N + " — ten buckets, Aug all the way to May" },
        { name:"Season", stake:"RM" + st.season.stake + " × " + N, prize: rm(st.season.net[0]) + " · " + rm(st.season.net[1]) + " · " + rm(st.season.net[2]),
          note:"60/25/15 of " + this.rmFlat(st.season.pot) + ", only settle after GW38" }
      ],
      oneLiner: "Every gameweek cost RM10 — RM5 to the week, RM5 to the month. Third place share stay above 1/" + N + " of the season pot, so podium never lose money.",
      staked: this.rmFlat(D.exposure.staked), best: rm(D.exposure.best), worst: rm(D.exposure.worst),
      zeroInk: (isLive || isProv) ? "var(--ink-muted)" : D.checks.zero_sum ? "var(--good)" : "var(--crit)",
      zeroLabel: (isLive || isProv) ? "Settles at full time"
        : D.checks.zero_sum ? "Ledger balances · RM0" : "Ledger does not balance",
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
      onYou: e => { const v = e.target.value; this.setState({ you:v, cmp: v === S.cmp ? D.managers.find(m => m.id !== v).id : S.cmp }); },
      onCmp: e => { const v = e.target.value; this.setState({ cmp:v, you: v === S.you ? D.managers.find(m => m.id !== v).id : S.you }); },
      toggleTheme: () => { const dark = S.theme === "dark";
        document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
        this.setState({ theme: dark ? "light" : "dark" }); },
      chrome: { dot: sm.dot, dotAnim: sm.anim, dotInk: sm.ink, stateText: sm.text, stateSub: stateSub,
                themeLabel: S.theme === "dark" ? "Light" : "Dark" },
      tabs, banner,
      isGW: S.tab === "gw", isSeason: S.tab === "season",
      showCountdown: !showLive, cd, brk,
      showLive, live, fx, cal, standings, pl, pred,
      hero, pot, ledger, weekly, detail, money, season,
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
