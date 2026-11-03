// Composants Alpine des écrans de terrain (maquettes 5 et 6).
document.addEventListener("alpine:init", () => {
  // Joint la position du téléphone au formulaire si elle arrive en 5 secondes ; sinon le
  // formulaire part sans elle : le GPS n'est jamais bloquant (§13).
  Alpine.data("avecPosition", () => ({
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
