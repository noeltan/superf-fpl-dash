/* Deadline reminders — the opt-in half.
 *
 * Push is per device, not per manager. Nothing here identifies anybody: the
 * store behind it holds endpoints and nothing else, so "who gets reminded" is
 * literally "whichever browsers asked to be". That is the whole privacy story
 * and it is why there is no login.
 *
 * Everything degrades. No worker configured, no push support, permission
 * denied, an iPhone that has not been added to the home screen — each is a
 * state this reports rather than an error it throws, because the dashboard has
 * to keep working identically for somebody who never turns this on.
 */

import { PROXY_BASE } from "./config.js";

const SW_PATH = "sw.js";

export function proxyConfigured() {
  return typeof PROXY_BASE === "string" && /^https?:\/\//.test(PROXY_BASE);
}

export function supported() {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window !== "undefined" &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/* iOS grants Push only to a site that has been added to the home screen, and
 * it fails *silently* in Safari otherwise — subscribe() simply never resolves
 * usefully. Detecting it up front is the difference between "here is how to
 * turn this on" and a button that does nothing. */
export function needsHomeScreenInstall() {
  if (typeof navigator === "undefined") return false;
  const iOS =
    /iP(hone|ad|od)/.test(navigator.userAgent) ||
    // iPadOS 13+ reports as a Mac; the touch points give it away.
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (!iOS) return false;
  const standalone =
    navigator.standalone === true ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  return !standalone;
}

/* The VAPID key arrives as base64url and has to reach subscribe() as bytes. */
function keyToBytes(base64url) {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function registration() {
  return navigator.serviceWorker.register(SW_PATH);
}

/* What to show on the control, without doing anything the user did not ask
 * for. Registering a service worker is fine; requesting permission is not —
 * an unprompted permission dialog is how a site gets its notifications
 * blocked forever. */
export async function currentState() {
  if (!proxyConfigured()) return "unavailable";
  if (!supported()) return "unsupported";
  if (needsHomeScreenInstall()) return "needs-install";
  if (Notification.permission === "denied") return "blocked";
  try {
    const existing = await navigator.serviceWorker.getRegistration(SW_PATH);
    const subscription = existing && (await existing.pushManager.getSubscription());
    return subscription ? "on" : "off";
  } catch (error) {
    return "off";
  }
}

export async function enable() {
  if (!proxyConfigured() || !supported()) return "unavailable";
  if (needsHomeScreenInstall()) return "needs-install";

  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "blocked" : "off";

  const keyResponse = await fetch(`${PROXY_BASE}/push/key`);
  if (!keyResponse.ok) throw new Error(`push key ${keyResponse.status}`);
  const { key } = await keyResponse.json();
  if (!key) throw new Error("worker has no VAPID key");

  const reg = await registration();
  await navigator.serviceWorker.ready;
  // An existing subscription is reused rather than replaced: re-subscribing
  // with the same key returns the same endpoint anyway, and churning it would
  // orphan the row already in the store.
  const subscription =
    (await reg.pushManager.getSubscription()) ||
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyToBytes(key),
    }));

  const saved = await fetch(`${PROXY_BASE}/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  if (!saved.ok) {
    // Do not leave the browser thinking it is subscribed when the sender has
    // never heard of it — that is a reminder that silently never arrives.
    await subscription.unsubscribe().catch(() => {});
    throw new Error(`subscribe ${saved.status}`);
  }
  return "on";
}

export async function disable() {
  const reg = await navigator.serviceWorker.getRegistration(SW_PATH);
  const subscription = reg && (await reg.pushManager.getSubscription());
  if (!subscription) return "off";
  const { endpoint } = subscription;
  await subscription.unsubscribe().catch(() => {});
  // Best effort: the local unsubscribe is what actually stops the notification
  // arriving, and a dead endpoint is retired by the sender on its next run.
  await fetch(`${PROXY_BASE}/push/unsubscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint }),
  }).catch(() => {});
  return "off";
}
