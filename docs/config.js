/* Client configuration.
 *
 * The FPL API sends no CORS headers, so the browser cannot call it directly
 * (spec §4). Live scores are polled through a small proxy that adds the one
 * missing header. Deploy the Cloudflare Worker in /worker (about fifteen lines,
 * free tier, no billing account) and paste its URL here.
 *
 * Leave it empty and the page still works completely: the countdown, the
 * settled ledger, the prediction card and both tabs all come from the JSON
 * files. Only the in-match live layer stays hidden.
 */
export const PROXY_BASE = "https://superf-fpl-proxy.workers.dev";
