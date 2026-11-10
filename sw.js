// Conto di Casa — service worker: funziona offline, tiene freschi i prezzi e ricorda le scadenze.
const CACHE = "contodicasa-v8";
const CORE = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icon.svg",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-maskable-512.png",
  "./apple-touch-icon.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== "contodicasa-state").map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const networkFirst = (req, key) =>
  fetch(req)
    .then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(key || req, copy)); } return res; })
    .catch(() => caches.match(key || req));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Pagina e prezzi dei carburanti: prima la rete, poi la copia salvata.
  if (req.mode === "navigate") { e.respondWith(networkFirst(req, "./index.html")); return; }
  if (url.origin === location.origin && url.pathname.includes("/data/")) { e.respondWith(networkFirst(req)); return; }
  // Prezzi dei carburanti: prima la rete, poi l'ultima copia (così funzionano anche offline).
  if (url.hostname.endsWith("supabase.co") && url.pathname.startsWith("/rest/v1/carburanti")) { e.respondWith(networkFirst(req)); return; }
  // Sincronizzazione e accesso: sempre in rete, mai in cache.
  if (url.hostname.endsWith("supabase.co")) return;
  // File dell'app, font e librerie: prima la copia salvata, poi la rete.
  if (url.origin === location.origin || /fonts\.(googleapis|gstatic)\.com$|cdn\.jsdelivr\.net$/.test(url.hostname)) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok || res.type === "opaque") { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }))
    );
  }
});

// Promemoria anche ad app chiusa (dove il telefono lo permette).
async function checkScadenze() {
  const c = await caches.open("contodicasa-state");
  const res = await c.match("./__scadenze.json");
  if (!res) return;
  const { notify, list } = await res.json();
  if (!notify || !Array.isArray(list)) return;
  const sentRes = await c.match("./__notificati.json");
  const sent = sentRes ? await sentRes.json() : [];
  const lim = Date.now() + 3 * 864e5;
  for (const s of list) {
    if (sent.includes(s.key) || new Date(s.date + "T09:00").getTime() > lim) continue;
    await self.registration.showNotification(s.amount > 0 ? `${s.title}: ${String(s.amount.toFixed(2)).replace(".", ",")} €` : s.title, {
      body: `In scadenza il ${new Date(s.date + "T12:00").toLocaleDateString("it-IT", { day: "numeric", month: "long" })}`,
      icon: "icon-192.png", badge: "icon-192.png", tag: s.key
    });
    sent.push(s.key);
  }
  await c.put("./__notificati.json", new Response(JSON.stringify(sent.slice(-200))));
}
self.addEventListener("periodicsync", (e) => { if (e.tag === "scadenze") e.waitUntil(checkScadenze()); });

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const w = wins.find((x) => x.url.includes("/conto-di-casa/") || x.url.endsWith("/index.html"));
    return w ? w.focus() : self.clients.openWindow("./index.html#panoramica");
  }));
});
