/* Generate the VAPID keypair the reminder sender signs with.
 *
 *   cd worker && node genkeys.mjs
 *
 * Prints two values and stores nothing. The private half never leaves your
 * machine except into `wrangler secret put`, which is why this is a script you
 * run rather than a key checked into the repo.
 *
 * VAPID is only about identity: it lets the push service — Google's, Mozilla's,
 * Apple's — see that the pushes claiming to be from this dashboard really are.
 * It is not encryption, and there is nothing to encrypt here, because the push
 * carries no payload (see push.js).
 */

import { webcrypto } from "node:crypto";

const b64url = (buf) =>
  Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const { publicKey, privateKey } = await webcrypto.subtle.generateKey(
  { name: "ECDSA", namedCurve: "P-256" },
  true,
  ["sign", "verify"]
);

// The public key travels to the browser as the 65-byte uncompressed point.
const raw = await webcrypto.subtle.exportKey("raw", publicKey);
// The private key travels to the Worker as a JWK, the one form WebCrypto
// imports without hand-parsing ASN.1.
const jwk = await webcrypto.subtle.exportKey("jwk", privateKey);

console.log("\nVAPID_PUBLIC_KEY  (npx wrangler secret put VAPID_PUBLIC_KEY)\n");
console.log(b64url(raw));
console.log("\nVAPID_PRIVATE_JWK (npx wrangler secret put VAPID_PRIVATE_JWK)\n");
console.log(JSON.stringify(jwk));
console.log(
  "\nKeep the private half out of the repo. Rotating it invalidates every\n" +
    "existing subscription — everyone would have to tap Remind me again.\n"
);
