/* CORS proxy for the FPL API (spec §4).
 *
 * The FPL API sends no CORS headers, so the browser cannot call it directly.
 * That single missing header is the only thing GitHub Pages cannot do for this
 * project — so rather than a scheduler, a storage bucket and a stored live.json,
 * this defeats the header and the page polls FPL itself.
 *
 * Deploy:
 *   cd worker && npx wrangler deploy
 * then paste the resulting URL into docs/config.js as PROXY_BASE.
 *
 * Free tier is 100k requests/day. Eight viewers polling once a minute inside
 * match windows is a few thousand a week, and the 30s edge cache collapses
 * concurrent viewers into roughly two origin hits a minute.
 */

import { isDeadSubscription, reminderDue, sendPush } from "./push.js";

const ORIGIN = "https://fantasy.premierleague.com";
const CACHE_SECONDS = 30;

/* --- deadline reminders (opt-in, per device) --------------------------------
 *
 * The one thing this league actually needs telling: the deadline is close, set
 * your team. Everything else on the site is a number you go and look at; this
 * is the number that costs you RM10 if nobody taps you on the shoulder.
 *
 * Subscriptions live in KV because they have to outlive the request that made
 * them and there is nowhere else — Pages is static. They are per browser, not
 * per manager: nothing here knows or stores who anybody is, which is why the
 * store needs no auth beyond the caps below.
 */
const MAX_SUBSCRIPTIONS = 200;
const REMIND_HOURS_BEFORE = 3;
const SUB_PREFIX = "sub:";

// A push endpoint is a capability URL — whoever holds it can push. Accepting
// arbitrary hosts would make this an open relay pointed at somebody else's
// server, so only the real push services are stored.
const PUSH_HOSTS = [
  /\.googleapis\.com$/,          // Chrome, Edge, Brave (FCM)
  /\.mozilla\.com$/,             // Firefox (autopush)
  /\.windows\.com$/,             // legacy Edge (WNS)
  /\.apple\.com$/,               // Safari / iOS
];

function subscriptionKey(endpoint) {
  // The endpoint is the identity. Hashing keeps KV keys bounded and avoids
  // putting a capability URL in a key name that gets listed.
  let hash = 5381;
  for (let i = 0; i < endpoint.length; i++) hash = ((hash * 33) ^ endpoint.charCodeAt(i)) >>> 0;
  return SUB_PREFIX + hash.toString(36) + "-" + endpoint.length.toString(36);
}

function isAllowedPushEndpoint(endpoint) {
  let url;
  try {
    url = new URL(endpoint);
  } catch {
    return false;
  }
  return url.protocol === "https:" && PUSH_HOSTS.some((re) => re.test(url.hostname));
}

// Only the endpoints the dashboard actually needs. An open proxy is somebody
// else's bandwidth problem eventually.
const ALLOWED = [
  /^\/api\/bootstrap-static\/$/,
  /^\/api\/fixtures\/(\?event=\d+)?$/,
  /^\/api\/event\/\d+\/live\/$/,
  /^\/api\/entry\/\d+\/event\/\d+\/picks\/$/,
  /^\/api\/leagues-classic\/\d+\/standings\/$/,
];

function corsHeaders(extra = {}) {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    ...extra,
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: corsHeaders({ "Content-Type": "application/json" }),
  });
}

/* The push routes. Separate from the proxy above them: that one is a cache in
 * front of somebody else's API, this one is the only state this Worker owns. */
async function handlePush(request, env, path) {
  if (!env.SUBSCRIPTIONS) {
    return json({ error: "push is not configured on this worker" }, 503);
  }

  // The page needs the VAPID public key to subscribe at all, and it is public
  // by construction — serving it here means it is never pasted into config.js
  // and can never drift from the private half the sender signs with.
  if (path === "/push/key" && request.method === "GET") {
    return env.VAPID_PUBLIC_KEY
      ? json({ key: env.VAPID_PUBLIC_KEY, hours_before: REMIND_HOURS_BEFORE })
      : json({ error: "no VAPID key configured" }, 503);
  }

  if (path === "/push/subscribe" && request.method === "POST") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "invalid JSON" }, 400);
    }
    const endpoint = body && body.endpoint;
    if (typeof endpoint !== "string" || !isAllowedPushEndpoint(endpoint)) {
      return json({ error: "not a recognised push endpoint" }, 400);
    }
    const key = subscriptionKey(endpoint);
    // Re-subscribing is normal — browsers rotate endpoints — so an existing
    // key is an update, not an error, and only a genuinely new one counts
    // against the cap.
    const existing = await env.SUBSCRIPTIONS.get(key);
    if (!existing) {
      const { keys } = await env.SUBSCRIPTIONS.list({ prefix: SUB_PREFIX, limit: MAX_SUBSCRIPTIONS + 1 });
      if (keys.length >= MAX_SUBSCRIPTIONS) {
        return json({ error: "subscription list is full" }, 507);
      }
    }
    await env.SUBSCRIPTIONS.put(key, JSON.stringify({ endpoint, added: new Date().toISOString() }));
    return json({ ok: true });
  }

  if (path === "/push/unsubscribe" && request.method === "POST") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "invalid JSON" }, 400);
    }
    if (typeof (body && body.endpoint) !== "string") {
      return json({ error: "endpoint required" }, 400);
    }
    await env.SUBSCRIPTIONS.delete(subscriptionKey(body.endpoint));
    return json({ ok: true });
  }

  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    if (url.pathname.startsWith("/push/")) {
      return handlePush(request, env, url.pathname);
    }

    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders() });
    }

    const path = url.pathname + url.search;
    if (!ALLOWED.some((pattern) => pattern.test(path))) {
      return new Response("Not proxied", { status: 404, headers: corsHeaders() });
    }

    const target = ORIGIN + path;
    const cache = caches.default;
    const cacheKey = new Request(target, { method: "GET" });

    let response = await cache.match(cacheKey);
    if (!response) {
      const upstream = await fetch(target, {
        headers: { "User-Agent": "superf-fpl-dash/1.0", Accept: "application/json" },
        cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true },
      });
      response = new Response(upstream.body, upstream);
      response.headers.set("Cache-Control", `public, max-age=${CACHE_SECONDS}`);
      if (upstream.ok) {
        await cache.put(cacheKey, response.clone());
      }
    }

    const out = new Response(response.body, response);
    for (const [key, value] of Object.entries(corsHeaders())) {
      out.headers.set(key, value);
    }
    return out;
  },

  /* Hourly. Reads the same published calendar the page reads, decides whether
   * a deadline is close enough to be worth waking anybody for, and sends one
   * bodyless push per subscribed device.
   *
   * Fires on a WINDOW, guarded by a KV flag, rather than at an exact hour —
   * cron is a suggestion and a missed slot must not mean a missed deadline.
   * The flag carries the gameweek, so it also survives a redeploy.
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(notifyDeadline(env, Date.now()));
  },
};

export async function notifyDeadline(env, now) {
  if (!env.SUBSCRIPTIONS || !env.VAPID_PUBLIC_KEY || !env.VAPID_PRIVATE_JWK) return;
  if (!env.DATA_URL) return;

  const response = await fetch(`${env.DATA_URL}?t=${Math.floor(now / 60000)}`);
  if (!response.ok) return;
  const data = await response.json();

  const due = reminderDue(data.events, { now, hoursBefore: REMIND_HOURS_BEFORE });
  if (!due) return;

  // One reminder per gameweek, ever. The season is in the key so a rebuilt
  // calendar in a later season cannot inherit last season's "already sent".
  const season = (data.current && data.current.season) || "season";
  const flag = `sent:${season}:gw${due.gw}`;
  if (await env.SUBSCRIPTIONS.get(flag)) return;

  const { keys } = await env.SUBSCRIPTIONS.list({ prefix: SUB_PREFIX });
  if (!keys.length) return;

  const subject = env.VAPID_SUBJECT || "mailto:superf-bot@users.noreply.github.com";
  let sent = 0;
  for (const { name } of keys) {
    const stored = await env.SUBSCRIPTIONS.get(name);
    if (!stored) continue;
    let subscription;
    try {
      subscription = JSON.parse(stored);
    } catch {
      await env.SUBSCRIPTIONS.delete(name);
      continue;
    }
    try {
      const status = await sendPush({
        subscription,
        publicKey: env.VAPID_PUBLIC_KEY,
        subject,
        jwk: env.VAPID_PRIVATE_JWK,
        now,
      });
      // A browser that has dropped its subscription says so once and then
      // says it forever. Retiring it here is the only garbage collection
      // this store gets.
      if (isDeadSubscription(status)) await env.SUBSCRIPTIONS.delete(name);
      else if (status < 300) sent += 1;
    } catch {
      // A single push service having a bad minute must not stop the rest.
    }
  }

  // Written only after a real send. A run that reached nobody leaves the flag
  // clear so the next hour tries again — there is still time before the
  // deadline, which is the whole point of the window.
  if (sent > 0) {
    const ttl = Math.max(
      60,
      Math.floor((new Date(due.deadline).getTime() - now) / 1000) + 86400
    );
    await env.SUBSCRIPTIONS.put(flag, String(now), { expirationTtl: ttl });
  }
}
