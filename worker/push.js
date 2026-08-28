/* Web Push sending, VAPID-signed, without payload encryption.
 *
 * A push message can carry an encrypted payload (RFC 8291: ECDH + HKDF +
 * aes128gcm), and that is most of the code in a push library. We do not need
 * it. The only thing the league is told is "the deadline is close", and the
 * page that would say it is already published: `docs/sw.js` fetches data.json
 * when it wakes and writes the notification from the same calendar every other
 * number on the site comes from.
 *
 * So this sends a *bodyless* push — a bare "wake up" — and the service worker
 * does the rest. What is left is VAPID (RFC 8292): sign a JWT with the P-256
 * key, name yourself in it, and let the push service decide you are allowed.
 * That is ~40 lines of WebCrypto instead of ~200 of key derivation, and it
 * means a subscription's payload can never leak from anywhere: there isn't one.
 *
 * The trade is that the service worker must be able to fetch data.json to know
 * what to say. It is a static file on the same origin, so that is a cheap
 * request — and if it fails, sw.js still shows a deliberately vague reminder
 * rather than nothing, because a push that shows no notification is a promise
 * broken to the browser (`userVisibleOnly`) and Chrome will scold the user
 * with "This site has been updated in the background" on our behalf.
 */

const JWT_LIFETIME_SECONDS = 12 * 3600;
const PUSH_TTL_SECONDS = 3 * 3600;

/* base64url, no padding — every field in a JWT and in VAPID uses it. */
export function b64url(bytes) {
  let binary = "";
  const view = new Uint8Array(bytes);
  for (const byte of view) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function utf8(text) {
  return new TextEncoder().encode(text);
}

/* The private key travels as a JWK string in a Worker secret, because that is
 * the one form WebCrypto imports without us hand-parsing ASN.1. */
async function importSigningKey(jwk) {
  return crypto.subtle.importKey(
    "jwk",
    typeof jwk === "string" ? JSON.parse(jwk) : jwk,
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["sign"]
  );
}

/* RFC 8292 §2 — the token the push service checks before it accepts a push.
 *
 * `aud` is the ORIGIN of the endpoint, not the endpoint: one token is valid for
 * every subscription on the same push service, which is why this is computed
 * per host rather than per subscriber. */
export async function vapidToken({ audience, subject, jwk, now = Date.now() }) {
  const header = b64url(utf8(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const claims = b64url(
    utf8(
      JSON.stringify({
        aud: audience,
        exp: Math.floor(now / 1000) + JWT_LIFETIME_SECONDS,
        sub: subject,
      })
    )
  );
  const signingInput = `${header}.${claims}`;
  const key = await importSigningKey(jwk);
  // WebCrypto returns the raw r||s pair ES256 wants — no DER unwrapping.
  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    key,
    utf8(signingInput)
  );
  return `${signingInput}.${b64url(signature)}`;
}

/* One bodyless push. Returns the HTTP status so the caller can retire a
 * subscription the push service has given up on. */
export async function sendPush({ subscription, publicKey, subject, jwk, now = Date.now() }) {
  const endpoint = new URL(subscription.endpoint);
  const token = await vapidToken({ audience: endpoint.origin, subject, jwk, now });
  const response = await fetch(subscription.endpoint, {
    method: "POST",
    headers: {
      Authorization: `vapid t=${token}, k=${publicKey}`,
      TTL: String(PUSH_TTL_SECONDS),
      // No body, so say so explicitly. Some push services are particular.
      "Content-Length": "0",
    },
  });
  return response.status;
}

/* 404 means the subscription never existed; 410 Gone means the browser has
 * dropped it (uninstalled, permission revoked, profile wiped). Both are
 * permanent, and keeping them means pushing into the void every deadline for
 * the rest of the season. */
export function isDeadSubscription(status) {
  return status === 404 || status === 410;
}

/* Which gameweek, if any, is due a reminder right now.
 *
 * Deliberately a WINDOW and not a moment. The cron fires hourly and Cloudflare
 * makes no promise about the minute; a check for "is it exactly T-3h" would
 * miss and never fire again. Anything inside the window fires, and the KV flag
 * upstream is what stops it firing twice — the same shape as the data job's
 * retries, and for the same reason: the schedule is a suggestion, the guard is
 * the thing that makes it correct.
 */
export function reminderDue(events, { now, hoursBefore }) {
  const windowEnd = now + hoursBefore * 3600e3;
  let due = null;
  for (const event of events || []) {
    const deadline = new Date(event.deadline).getTime();
    if (Number.isNaN(deadline)) continue;
    if (deadline <= now) continue; // gone — never remind about the past
    if (deadline > windowEnd) continue; // not yet inside the window
    if (due === null || deadline < new Date(due.deadline).getTime()) due = event;
  }
  return due;
}
