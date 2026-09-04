/* Service worker — reminders only.
 *
 * This does NOT cache the site. A dashboard whose whole point is that the
 * numbers are current is the last thing that should be served from a stale
 * cache, and §9.5's stale banner can only work if the page it is on came from
 * the network. The only job here is to be awake when a push arrives.
 *
 * The push carries no payload (see worker/push.js for why). So this fetches
 * data.json — the same file every number on the site comes from — and writes
 * the notification out of the real book. That means the message can never
 * disagree with the page: there is one deadline list, one summary, not two.
 *
 * Two things are worth waking anybody for, and the push cannot say which it
 * is, so both are worked out here from the book:
 *
 *   - a deadline is close: name it, the hours left, and what missing it costs;
 *   - a gameweek has settled since this device last heard: name who took it,
 *     so whoever holds the phone knows the summary is ready to send.
 *
 * "Since this device last heard" needs one number remembered — the last
 * settled gameweek announced — and the Cache API is the only storage a
 * service worker has that survives it being killed. It holds that one entry
 * and nothing of the site.
 */

const DATA_URL = "data.json";
const DEADLINE_TAG = "superf-deadline";
const SETTLED_TAG = "superf-settled";

// The one thing remembered. A cache name and a key inside it; the value is
// `season:gw` of the last settlement shown, so next August's GW1 is new.
const STATE_CACHE = "superf-notify-state";
const STATE_KEY = "./superf-notify-state/settled";

// The same window the Worker fires on. It cannot be asked — the push is
// bodyless — so it is repeated here. Only used to decide whether a push that
// arrived on a new settlement should *also* mention a deadline that is close.
const REMIND_HOURS_BEFORE = 3;

/* Take over as soon as installed, rather than waiting for every tab to close.
 * Nobody is going to close all their tabs to make notifications start working.
 * And note what has already settled: everything in the book at install is old
 * news, so the first push after subscribing does not announce last week. */
self.addEventListener("install", (event) => {
  self.skipWaiting();
  if (event && event.waitUntil) {
    event.waitUntil(
      fetchBook(Date.now())
        .then((data) => rememberSettled(settledKey(data)))
        .catch(() => {})
    );
  }
});
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

const MYT_OFFSET_MS = 8 * 3600e3;

function myt(iso) {
  return new Date(new Date(iso).getTime() + MYT_OFFSET_MS);
}

/* Malaysia time, matching the page: it is the only clock this league thinks in. */
function whenText(iso) {
  const d = myt(iso);
  const day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()];
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day} ${hh}:${mm}`;
}

function hoursUntil(iso, now) {
  return (new Date(iso).getTime() - now) / 3600e3;
}

async function fetchBook(now) {
  // Cache-busted per minute, same as the page does: a reminder built from a
  // cached calendar could name a deadline that has already moved.
  const response = await fetch(`${DATA_URL}?t=${Math.floor(now / 60000)}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`data.json ${response.status}`);
  return response.json();
}

function nextDeadline(data, now) {
  return (data.events || [])
    .filter((e) => new Date(e.deadline).getTime() > now)
    .sort((a, b) => new Date(a.deadline) - new Date(b.deadline))[0];
}

function deadlineNotification(next, data, now) {
  const hours = hoursUntil(next.deadline, now);
  const rounded = hours >= 1 ? `${Math.round(hours)}h` : `${Math.max(1, Math.round(hours * 60))}m`;
  const stake =
    data.stakes && data.stakes.weekly
      ? `RM${data.stakes.weekly.stake}`
      : "your stake";

  return {
    title: `GW${next.gw} deadline in ${rounded}`,
    body:
      `Locks ${whenText(next.deadline)} MYT. Set your team — miss it and FPL rolls ` +
      `last week's squad over, and you still pay ${stake}.`,
    tag: DEADLINE_TAG,
  };
}

/* `season:gw` of the book's summary, or null while nothing has settled. */
function settledKey(data) {
  const summary = data && data.summary;
  if (!summary || !Number.isInteger(summary.gw) || summary.gw < 1) return null;
  const season = (data.current && data.current.season) || "season";
  return `${season}:${summary.gw}`;
}

/* The words are the summary's own — the headline the emitter composed — so
 * this cannot say "won" when the book says "is owed". */
function settledNotification(data) {
  const summary = data.summary;
  const what = summary.monthly_settled
    ? `GW${summary.gw} has settled, and the month with it.`
    : `GW${summary.gw} has settled.`;
  return {
    title: summary.headline || `GW${summary.gw} settled`,
    body: `${what} Open SuperF to send the summary to the group.`,
    tag: SETTLED_TAG,
  };
}

/* What this device last announced. `{ ok: false }` when the storage is not
 * there at all, which is different from "nothing stored": without a memory
 * every push would re-announce the same gameweek, so the settlement is simply
 * not mentioned and the push falls through to the deadline. */
async function knownSettled() {
  try {
    const cache = await caches.open(STATE_CACHE);
    const hit = await cache.match(STATE_KEY);
    return { ok: true, key: hit ? await hit.text() : null };
  } catch (error) {
    return { ok: false, key: null };
  }
}

async function rememberSettled(key) {
  if (!key) return;
  try {
    const cache = await caches.open(STATE_CACHE);
    await cache.put(STATE_KEY, new Response(key));
  } catch (error) {
    // Nothing to do: the next push will say it again, which is the cheaper
    // mistake.
  }
}

async function buildNotifications(now) {
  const data = await fetchBook(now);
  const notes = [];

  const key = settledKey(data);
  if (key) {
    const known = await knownSettled();
    if (known.ok && known.key !== key) {
      notes.push(settledNotification(data));
      await rememberSettled(key);
    }
  }

  const next = nextDeadline(data, now);
  // A push that was not about a settlement is about the deadline, whatever
  // the clock says: the Worker decided it was due. One that was about a
  // settlement also mentions the deadline only if it is genuinely close.
  if (next && (!notes.length || hoursUntil(next.deadline, now) <= REMIND_HOURS_BEFORE)) {
    notes.push(deadlineNotification(next, data, now));
  }
  if (!notes.length) throw new Error("nothing to say");
  return notes;
}

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let notifications;
      try {
        notifications = await buildNotifications(Date.now());
      } catch (error) {
        // userVisibleOnly is a promise to the browser: wake up and you WILL
        // show something. Break it and Chrome posts "This site has been
        // updated in the background" in our name. A vague reminder that is
        // still true beats that, and beats silence.
        notifications = [{
          title: "FPL deadline coming up",
          body: "Open SuperF to check the countdown and set your team.",
          tag: DEADLINE_TAG,
        }];
      }
      for (const notification of notifications) {
        await self.registration.showNotification(notification.title, {
          body: notification.body,
          // One deadline, one notification; one settlement, one notification.
          // A re-send replaces rather than stacks, so a retry cannot leave
          // three of these on the lock screen.
          tag: notification.tag,
          renotify: false,
          icon: "icon-192.png",
          badge: "icon-192.png",
          data: { url: "./" },
        });
      }
    })()
  );
});

/* Tapping it should land on the dashboard — focusing a tab that already has it
 * open rather than piling up new ones. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const target = new URL(
        (event.notification.data && event.notification.data.url) || "./",
        self.location.href
      ).href;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if (client.url.startsWith(target.replace(/[^/]*$/, "")) && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })()
  );
});
