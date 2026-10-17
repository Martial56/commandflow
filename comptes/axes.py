def identifiant_connexion(request, credentials):
    """Identifiant utilisé par django-axes pour compter les échecs par compte.

    allauth transmet `phone` ou `email` ; l'admin Django transmet `username`.
    """
    credentials = credentials or {}
    for cle in ("phone", "email", "username"):
        if credentials.get(cle):
            return str(credentials[cle]).replace(" ", "").lower()
    donnees = getattr(request, "POST", {})
    valeur = donnees.get("login") or donnees.get("username") or ""
    return valeur.replace(" ", "").lower()
