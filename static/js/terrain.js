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
    async envoyer(formulaire) {
      if (this.envoi) return;
      this.envoi = true;
      // Une photo en cours de réduction part une fois prête (composant « photo »).
      await (formulaire.photoPrete || Promise.resolve());
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

  // Photo du bordereau réduite sur le téléphone avant l'envoi (lot A4) : environ 1600 px
  // de côté en JPEG, quelques centaines de Ko au lieu de plusieurs Mo, pour économiser le
  // forfait du chauffeur et le stockage. Le serveur revérifie tout ; si la réduction échoue
  // ou n'apporte rien, la photo d'origine part telle quelle.
  const COTE_MAX = 1600;
  const QUALITE = 0.7;
  const SEUIL = 500 * 1024;

  const reduire = async (fichier) => {
    if (!fichier.type.startsWith("image/") || !window.createImageBitmap) return fichier;
    const image = await createImageBitmap(fichier, { imageOrientation: "from-image" });
    const echelle = Math.min(1, COTE_MAX / Math.max(image.width, image.height));
    if (echelle === 1 && fichier.size <= SEUIL) return fichier;
    const toile = document.createElement("canvas");
    toile.width = Math.round(image.width * echelle);
    toile.height = Math.round(image.height * echelle);
    toile.getContext("2d").drawImage(image, 0, 0, toile.width, toile.height);
    image.close();
    const blob = await new Promise((resolu) => toile.toBlob(resolu, "image/jpeg", QUALITE));
    if (!blob || blob.size >= fichier.size) return fichier;
    const nom = fichier.name.replace(/\.[^.]+$/, "") + ".jpg";
    return new File([blob], nom, { type: "image/jpeg", lastModified: Date.now() });
  };

  const taille = (octets) =>
    octets < 1024 * 1024
      ? `${Math.max(1, Math.round(octets / 1024))} Ko`
      : `${(octets / 1024 / 1024).toFixed(1).replace(".", ",")} Mo`;

  Alpine.data("photo", () => ({
    nom: "",
    detail: "",
    choisir(evenement) {
      const champ = evenement.target;
      const fichier = champ.files[0];
      this.nom = fichier ? fichier.name : "";
      this.detail = fichier ? taille(fichier.size) : "";
      if (!fichier) return;
      const formulaire = champ.form;
      formulaire.photoPrete = reduire(fichier)
        .catch(() => fichier)
        .then((resultat) => {
          if (resultat !== fichier) {
            const transfert = new DataTransfer();
            transfert.items.add(resultat);
            champ.files = transfert.files;
          }
          this.nom = resultat.name;
          this.detail = taille(resultat.size);
        });
    },
  }));

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
