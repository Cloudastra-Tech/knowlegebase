// The service worker that makes the site installable (platform.md PL-26). It keeps
// the APP SHELL only: the page (index.html), its built scripts and styles
// (/assets/, named by content hash) and the icons, so the app opens on a bad
// connection. It never touches the API (/api/, /mcp, /scim) or anything else that
// carries answers or documents: those always go to the network, uncached.
// main.tsx registers it in production builds only.

const CACHE = "kb-shell-v2"
const SHELL = ["/", "/manifest.webmanifest", "/icons/mark-192.png", "/icons/mark-512.png"]
const NEVER = ["/api/", "/mcp", "/scim/", "/docs", "/redoc", "/openapi.json"]

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

function isShell(url) {
  return url.pathname.startsWith("/assets/") || SHELL.includes(url.pathname)
}

self.addEventListener("fetch", (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (request.method !== "GET" || url.origin !== self.location.origin) return
  if (NEVER.some((prefix) => url.pathname.startsWith(prefix))) return

  // A screen of the app (/chat, /integrations, ...): the network first, so a
  // deploy shows at once; offline, the cached page (the router shows the screen).
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/").then((page) => page || Response.error())),
    )
    return
  }
  // Built files never change under their name: from the cache, else fetched and kept.
  if (isShell(url)) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok && url.pathname.startsWith("/assets/")) {
              const copy = response.clone()
              caches.open(CACHE).then((cache) => cache.put(request, copy))
            }
            return response
          }),
      ),
    )
  }
})
