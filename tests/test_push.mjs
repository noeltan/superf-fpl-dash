/* Deadline reminders: the parts that can be wrong silently.
 *
 *   node tests/test_push.mjs
 *
 * A bad VAPID signature does not throw anywhere we would see it. The push
 * service answers 401, the Worker logs nothing anybody reads, and the league
 * simply never gets reminded — which looks exactly like "nobody turned it on".
 * So the signature is verified here against the public half, the same way a
 * push service verifies it.
 */

import { webcrypto } from "node:crypto";
import assert from "node:assert/strict";

// The Worker reaches for the WebCrypto globals a runtime gives it. Node has
// had them since 20, so this only fills the gap on anything older.
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const { vapidToken, reminderDue, isDeadSubscription, b64url, settledDue } = await import(
  "../worker/push.js"
);

let passed = 0;
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      passed += 1;
      console.log("  ok  " + name);
    })
    .catch((error) => {
      console.error("FAIL  " + name + "\n      " + error.message);
      process.exitCode = 1;
    });
}

const fromB64url = (s) =>
  Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

async function keypair() {
  const pair = await webcrypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    true,
    ["sign", "verify"]
  );
  return {
    jwk: JSON.stringify(await webcrypto.subtle.exportKey("jwk", pair.privateKey)),
    verifyKey: pair.publicKey,
  };
}

console.log("VAPID");

await test("the token verifies against the public half, as a push service checks it", async () => {
  const { jwk, verifyKey } = await keypair();
  const now = Date.UTC(2026, 7, 28, 12, 0, 0);
  const token = await vapidToken({
    audience: "https://fcm.googleapis.com",
    subject: "mailto:superf-bot@users.noreply.github.com",
    jwk,
    now,
  });

  const [header, claims, signature] = token.split(".");
  assert.equal(token.split(".").length, 3, "a JWT has three parts");

  const ok = await webcrypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" },
    verifyKey,
    fromB64url(signature),
    new TextEncoder().encode(`${header}.${claims}`)
  );
  assert.ok(ok, "signature does not verify — every push would 401");
});

await test("the claims say what RFC 8292 requires", async () => {
  const { jwk } = await keypair();
  const now = Date.UTC(2026, 7, 28, 12, 0, 0);
  const token = await vapidToken({
    audience: "https://updates.push.services.mozilla.com",
    subject: "mailto:a@b.c",
    jwk,
    now,
  });
  const claims = JSON.parse(fromB64url(token.split(".")[1]).toString());
  // aud is the ORIGIN, never the full endpoint: one token covers every
  // subscriber on the same push service.
  assert.equal(claims.aud, "https://updates.push.services.mozilla.com");
  assert.equal(claims.sub, "mailto:a@b.c");
  assert.ok(claims.exp > now / 1000, "already expired");
  assert.ok(claims.exp <= now / 1000 + 24 * 3600, "over the 24h ceiling push services enforce");

  const header = JSON.parse(fromB64url(token.split(".")[0]).toString());
  assert.deepEqual(header, { typ: "JWT", alg: "ES256" });
});

await test("base64url carries no padding and no + or /", () => {
  // 0xFB 0xFF encodes to "+/8=" in plain base64 — every unsafe character at once.
  assert.equal(b64url(new Uint8Array([0xfb, 0xff])), "-_8");
  assert.ok(!b64url(new Uint8Array([1, 2, 3, 4, 5])).includes("="));
});

console.log("\nWhen a reminder is due");

const EVENTS = [
  { gw: 1, deadline: "2026-08-21T17:30:00Z" },
  { gw: 2, deadline: "2026-08-28T17:30:00Z" },
  { gw: 3, deadline: "2026-09-04T17:30:00Z" },
];
const at = (iso) => new Date(iso).getTime();

await test("fires inside the window", () => {
  // 2h before GW2 — inside a 3h window.
  const due = reminderDue(EVENTS, { now: at("2026-08-28T15:30:00Z"), hoursBefore: 3 });
  assert.equal(due.gw, 2);
});

await test("does not fire before the window opens", () => {
  // 5h before: too early. Firing here would tell people to set a team they
  // still have all evening to change.
  assert.equal(reminderDue(EVENTS, { now: at("2026-08-28T12:30:00Z"), hoursBefore: 3 }), null);
});

await test("never fires about a deadline that has gone", () => {
  // One minute after GW2 locks. The advice is now useless and the next
  // deadline is a week away.
  assert.equal(reminderDue(EVENTS, { now: at("2026-08-28T17:31:00Z"), hoursBefore: 3 }), null);
});

await test("a missed hour still fires while there is time left", () => {
  // The whole reason this is a window: cron slipped, and 10 minutes before the
  // deadline a reminder is still worth sending.
  const due = reminderDue(EVENTS, { now: at("2026-08-28T17:20:00Z"), hoursBefore: 3 });
  assert.equal(due.gw, 2);
});

await test("picks the nearest deadline when two are in range", () => {
  const doubled = [
    { gw: 9, deadline: "2026-12-30T18:30:00Z" },
    { gw: 10, deadline: "2026-12-30T20:00:00Z" },
  ];
  const due = reminderDue(doubled, { now: at("2026-12-30T17:00:00Z"), hoursBefore: 6 });
  assert.equal(due.gw, 9, "the one about to lock is the one worth saying");
});

await test("survives a calendar with junk in it", () => {
  assert.equal(reminderDue(undefined, { now: Date.now(), hoursBefore: 3 }), null);
  assert.equal(reminderDue([{ gw: 1, deadline: "not a date" }], { now: Date.now(), hoursBefore: 3 }), null);
});

console.log("\nRetiring subscriptions");

await test("only 404 and 410 are permanent", () => {
  assert.ok(isDeadSubscription(404), "never existed");
  assert.ok(isDeadSubscription(410), "browser dropped it");
  // A push service having a bad minute must not empty the store.
  for (const status of [201, 429, 500, 502, 503]) {
    assert.ok(!isDeadSubscription(status), `${status} must not delete a subscription`);
  }
});

console.log("\nWhat the reminder actually says");

/* sw.js is a service worker: it talks to `self`, `fetch` and
 * `registration.showNotification`. Standing those up in Node is cheaper than
 * driving a real push through a browser, and it tests the part with the
 * actual logic — the wording, the clock, and the fallback. */
const handlers = {};
const shown = [];
let pushFixture = { data: null, failFetch: false };

// Imported once: a service worker registers its listeners at evaluation time,
// so the stub it registers against has to outlive the import. Each case then
// drives the same handler with different data, which is also how the browser
// uses it.
globalThis.self = {
  addEventListener: (name, fn) => { handlers[name] = fn; },
  skipWaiting: () => {},
  location: { href: "https://noeltan.github.io/superf-fpl-dash/sw.js" },
  clients: { claim: async () => {}, matchAll: async () => [], openWindow: async () => {} },
  registration: {
    showNotification: async (title, options) => { shown.push({ title, ...options }); },
  },
};
globalThis.fetch = async () =>
  pushFixture.failFetch
    ? { ok: false, status: 503 }
    : { ok: true, status: 200, json: async () => pushFixture.data };

// The one thing sw.js remembers — the last settlement it announced — lives in
// the Cache API. A Map per cache name is all of it that the worker touches.
const cacheStores = {};
globalThis.caches = {
  open: async (name) => {
    const store = (cacheStores[name] = cacheStores[name] || new Map());
    return {
      match: async (key) => (store.has(key) ? new Response(store.get(key)) : undefined),
      put: async (key, response) => { store.set(key, await response.text()); },
    };
  },
};
const forgetSettled = () => { for (const name of Object.keys(cacheStores)) delete cacheStores[name]; };

await import("../docs/sw.js");
assert.equal(typeof handlers.push, "function", "sw.js registered no push handler");

async function runPushHandler({ data, failFetch = false, now }) {
  pushFixture = { data, failFetch };
  shown.length = 0;
  const realNow = Date.now;
  if (now) Date.now = () => now;
  try {
    const waits = [];
    await handlers.push({ waitUntil: (p) => waits.push(p) });
    await Promise.all(waits);
  } finally {
    Date.now = realNow;
  }
  return [...shown];
}

const CALENDAR = {
  events: EVENTS,
  stakes: { weekly: { stake: 10 } },
};

await test("names the gameweek, the hours left and the Malaysian lock time", async () => {
  // 2h before GW2, which locks 17:30 UTC = 01:30 Sat MYT.
  const [note] = await runPushHandler({ data: CALENDAR, now: at("2026-08-28T15:30:00Z") });
  assert.match(note.title, /GW2 deadline in 2h/);
  assert.match(note.body, /Sat 01:30 MYT/, "must speak Malaysia time, like the page");
  assert.match(note.body, /RM10/, "says what missing it costs");
});

await test("switches to minutes inside the last hour", async () => {
  const [note] = await runPushHandler({ data: CALENDAR, now: at("2026-08-28T17:05:00Z") });
  assert.match(note.title, /GW2 deadline in 25m/, "'in 0h' would be useless");
});

await test("skips a deadline that has already gone", async () => {
  // GW2 has locked; GW3 is next and is what should be named.
  const [note] = await runPushHandler({ data: CALENDAR, now: at("2026-08-28T18:00:00Z") });
  assert.match(note.title, /GW3/);
});

await test("still shows something when data.json cannot be read", async () => {
  // userVisibleOnly is a promise to the browser: wake up and you WILL show
  // something. Break it and Chrome posts its own scolding notice in our name.
  const [note] = await runPushHandler({ data: null, failFetch: true, now: Date.now() });
  assert.ok(note.title.length > 0 && note.body.length > 0);
  assert.doesNotMatch(note.title, /undefined|NaN/);
});

await test("one deadline never stacks up as several notifications", async () => {
  const [note] = await runPushHandler({ data: CALENDAR, now: at("2026-08-28T15:30:00Z") });
  assert.equal(note.tag, "superf-deadline", "a retry must replace, not pile up");
});

console.log("\nWhen a gameweek has settled");

await test("settledDue names the gameweek the summary is about, and nothing before one", () => {
  assert.equal(settledDue({ summary: null }), null);
  assert.equal(settledDue({}), null);
  assert.equal(settledDue({ summary: { gw: 0 } }), null);
  const due = settledDue({ current: { season: "2026/27" }, summary: { gw: 2, headline: "Way Shoon takes GW2" } });
  assert.deepEqual(due, { season: "2026/27", gw: 2, headline: "Way Shoon takes GW2" });
});

const SETTLED = {
  ...CALENDAR,
  current: { season: "2026/27" },
  summary: { gw: 2, headline: "Way Shoon takes GW2", monthly_settled: true },
};

await test("a new settlement is announced in the summary's own words", async () => {
  forgetSettled();
  // Tuesday morning after GW2: the next deadline is days away.
  const notes = await runPushHandler({ data: SETTLED, now: at("2026-09-01T00:00:00Z") });
  assert.equal(notes.length, 1, "no deadline is close, so only the settlement");
  const [note] = notes;
  assert.equal(note.tag, "superf-settled");
  assert.equal(note.title, "Way Shoon takes GW2");
  assert.match(note.body, /GW2 has settled, and the month with it/);
  assert.match(note.body, /send the summary/);
  assert.doesNotMatch(note.title + note.body, /\bwon\b|\bpaid\b/i, "§3.9.1");
});

await test("the same settlement is not announced twice — the next push is the deadline", async () => {
  const [note] = await runPushHandler({ data: SETTLED, now: at("2026-09-01T00:00:00Z") });
  assert.equal(note.tag, "superf-deadline");
  assert.match(note.title, /GW3 deadline/);
});

await test("a deadline inside the window rides alongside a new settlement", async () => {
  forgetSettled();
  // GW3 settles late, two hours before GW4 locks — both are worth saying.
  const tight = {
    ...SETTLED,
    events: [...EVENTS, { gw: 4, deadline: "2026-09-05T17:30:00Z" }],
    summary: { gw: 3, headline: "Sam takes GW3", monthly_settled: false },
  };
  const notes = await runPushHandler({ data: tight, now: at("2026-09-05T15:30:00Z") });
  assert.deepEqual(notes.map((n) => n.tag), ["superf-settled", "superf-deadline"]);
  assert.match(notes[0].body, /GW3 has settled\./);
  assert.match(notes[1].title, /GW4 deadline in 2h/);
});

await test("without a memory the settlement is left unsaid rather than repeated forever", async () => {
  const realCaches = globalThis.caches;
  globalThis.caches = undefined;
  try {
    const notes = await runPushHandler({ data: SETTLED, now: at("2026-09-01T00:00:00Z") });
    assert.deepEqual(notes.map((n) => n.tag), ["superf-deadline"]);
  } finally {
    globalThis.caches = realCaches;
  }
});

await test("installing notes what has already settled, so the first push is not last week's news", async () => {
  forgetSettled();
  pushFixture = { data: SETTLED, failFetch: false };
  const waits = [];
  await handlers.install({ waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  const [note] = await runPushHandler({ data: SETTLED, now: at("2026-09-01T00:00:00Z") });
  assert.equal(note.tag, "superf-deadline");
});

console.log(`\n${passed} passed`);
