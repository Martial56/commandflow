// Service worker de ComandFlow (décision 8.4), généré par noyau/pwa.py.
// Fichiers statiques en cache et page de repli hors connexion. Les pages et les envois
// (formulaires) passent toujours par le réseau et ne sont jamais mis en cache.
const CACHE = "comandflow-{{ version }}";
const FICHIERS = {{ fichiers|safe }};
const REPLI = {{ repli|safe }};

self.addEventListener("install", (evenement) => {
  evenement.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll([...FICHIERS, REPLI]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (evenement) => {
  evenement.waitUntil(
    caches
      .keys()
      .then((noms) =>
        Promise.all(
          noms
            .filter((nom) => nom.startsWith("comandflow-") && nom !== CACHE)
            .map((nom) => caches.delete(nom)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (evenement) => {
  const requete = evenement.request;
  if (requete.method !== "GET") return;
  const url = new URL(requete.url);
  if (url.origin !== self.location.origin) return;
  if (requete.mode === "navigate") {
    // Page : toujours le réseau ; sans réseau, la page « hors connexion ».
    evenement.respondWith(fetch(requete).catch(() => caches.match(REPLI)));
  } else if (FICHIERS.includes(url.pathname)) {
    evenement.respondWith(caches.match(requete).then((reponse) => reponse || fetch(requete)));
  }
});
