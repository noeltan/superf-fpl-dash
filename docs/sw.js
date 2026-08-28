/* Service worker — deadline reminders only.
 *
 * This does NOT cache the site. A dashboard whose whole point is that the
 * numbers are current is the last thing that should be served from a stale
 * cache, and §9.5's stale banner can only work if the page it is on came from
 * the network. The only job here is to be awake when a push arrives.
 *
 * The push carries no payload (see worker/push.js for why). So this fetches
 * data.json — the same file every number on the site comes from — and writes
 * the reminder out of the real calendar. That means the message can never
 * disagree with the page: there is one deadline list, not two.
 */

const DATA_URL = "data.json";
const TAG = "superf-deadline";

/* Take over as soon as installed, rather than waiting for every tab to close.
 * Nobody is going to close all their tabs to make notifications start working. */
self.addEventListener("install", () => self.skipWaiting());
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

async function buildNotification() {
  const now = Date.now();
  // Cache-busted per minute, same as the page does: a reminder built from a
  // cached calendar could name a deadline that has already moved.
  const response = await fetch(`${DATA_URL}?t=${Math.floor(now / 60000)}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`data.json ${response.status}`);
  const data = await response.json();

  const next = (data.events || [])
    .filter((e) => new Date(e.deadline).getTime() > now)
    .sort((a, b) => new Date(a.deadline) - new Date(b.deadline))[0];
  if (!next) throw new Error("no upcoming deadline");

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
  };
}

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let notification;
      try {
        notification = await buildNotification();
      } catch (error) {
        // userVisibleOnly is a promise to the browser: wake up and you WILL
        // show something. Break it and Chrome posts "This site has been
        // updated in the background" in our name. A vague reminder that is
        // still true beats that, and beats silence.
        notification = {
          title: "FPL deadline coming up",
          body: "Open SuperF to check the countdown and set your team.",
        };
      }
      await self.registration.showNotification(notification.title, {
        body: notification.body,
        // One deadline, one notification: a re-send replaces rather than
        // stacks, so a retry cannot leave three of these on the lock screen.
        tag: TAG,
        renotify: false,
        icon: "icon-192.png",
        badge: "icon-192.png",
        data: { url: "./" },
      });
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
