// Composants Alpine des écrans de bureau.
document.addEventListener("alpine:init", () => {
  // Enregistrer un paiement (maquette 16) : le montant est réparti sur les factures
  // ouvertes, la plus ancienne échéance d'abord ; chaque part reste modifiable. Le
  // serveur revérifie tout (paiements.services).
  Alpine.data("paiement", () => ({
    montant: "",
    message: "",
    entier(valeur) {
      return parseInt(String(valeur).replace(/\D/g, ""), 10) || 0;
    },
    champs() {
      return [...this.$root.querySelectorAll("input[data-solde]")];
    },
    repartir() {
      let reste = this.entier(this.montant);
      for (const champ of this.champs()) {
        const part = Math.min(reste, Number(champ.dataset.solde));
        champ.value = part ? String(part) : "";
        reste -= part;
      }
      this.calculer();
    },
    calculer() {
      const montant = this.entier(this.montant);
      const champs = this.champs();
      const affecte = champs.reduce((total, champ) => total + this.entier(champ.value), 0);
      if (!montant) {
        this.message = "";
        return;
      }
      if (affecte > montant) {
        this.message = "Le montant affecté dépasse le paiement.";
        return;
      }
      const partielles = champs
        .filter((c) => this.entier(c.value) > 0 && this.entier(c.value) < Number(c.dataset.solde))
        .map((c) => c.dataset.numero);
      const suite = partielles.length
        ? ` · ${partielles.join(", ")} ${partielles.length > 1 ? "resteront partiellement payées" : "restera partiellement payée"}`
        : "";
      this.message =
        affecte === montant
          ? `Montant entièrement affecté${suite}`
          : `${(montant - affecte).toLocaleString("fr-FR")} FCFA resteront en avance du client${suite}`;
    },
  }));
});
