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

const ORIGIN = "https://fantasy.premierleague.com";
const CACHE_SECONDS = 30;

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
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Max-Age": "86400",
    ...extra,
  };
}

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders() });
    }

    const url = new URL(request.url);
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
};
