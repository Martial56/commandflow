// Composants Alpine des écrans de terrain (maquettes 5 et 6).

// Application installable (décision 8.4) : service worker servi à la racine (noyau/pwa.py).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js"));
}

document.addEventListener("alpine:init", () => {
  // Joint la position du téléphone au formulaire si elle arrive en 5 secondes ; sinon le
  // formulaire part sans elle : le GPS n'est jamais bloquant (§13).
  const avecPosition = () => ({
    envoi: false,
    envoyer(formulaire) {
      if (this.envoi) return;
      this.envoi = true;
      const partir = () => formulaire.submit();
      if (!navigator.geolocation) return partir();
      navigator.geolocation.getCurrentPosition(
        (position) => {
          formulaire.elements.latitude.value = position.coords.latitude.toFixed(6);
          formulaire.elements.longitude.value = position.coords.longitude.toFixed(6);
          partir();
        },
        partir,
        { enableHighAccuracy: true, timeout: 5000, maximumAge: 60000 },
      );
    },
  });
  Alpine.data("avecPosition", avecPosition);

  // Preuve de livraison (maquette 6) : résultat choisi, position jointe à l'envoi.
  Alpine.data("livraison", (resultat) => ({ ...avecPosition(), resultat }));

  // Signature du réceptionnaire à l'écran (signature_pad) : le PNG est recopié dans le
  // champ caché `signature` à chaque trait.
  Alpine.data("signature", () => ({
    ouverte: false,
    signee: false,
    pad: null,
    ouvrir() {
      this.ouverte = true;
      // La toile n'a sa taille qu'une fois affichée : attendre l'image suivante.
      this.$nextTick(() =>
        requestAnimationFrame(() => {
          if (this.pad) return;
          const toile = this.$refs.toile;
          const ratio = Math.max(window.devicePixelRatio || 1, 1);
          toile.width = toile.offsetWidth * ratio;
          toile.height = toile.offsetHeight * ratio;
          toile.getContext("2d").scale(ratio, ratio);
          this.pad = new SignaturePad(toile);
          this.pad.addEventListener("endStroke", () => {
            this.$refs.valeur.value = this.pad.toDataURL("image/png");
            this.signee = true;
          });
        }),
      );
    },
    effacer() {
      if (this.pad) this.pad.clear();
      this.$refs.valeur.value = "";
      this.signee = false;
    },
  }));

  // « En ligne » / « Hors connexion » dans l'en-tête de la tournée.
  Alpine.data("connexion", () => ({
    enLigne: navigator.onLine,
    init() {
      window.addEventListener("online", () => (this.enLigne = true));
      window.addEventListener("offline", () => (this.enLigne = false));
    },
  }));
});
