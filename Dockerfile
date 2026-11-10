FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

# git : nécessaire à pre-commit, lancé dans le conteneur (outils/hook-pre-commit.sh).
# Le dépôt monté appartient à l'utilisateur du poste : git doit l'accepter.
# Pango, HarfBuzz et une police : nécessaires à WeasyPrint (exports PDF).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory /app

# dev (par défaut) ou prod
ARG REQUIREMENTS=dev
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/${REQUIREMENTS}.txt

# Image de développement seulement : Chromium et ses bibliothèques pour la recette
# dans un vrai navigateur (pytest -m recette). Deux couches : les bibliothèques système
# (longues à installer) restent en cache si le téléchargement du navigateur échoue.
RUN if [ "$REQUIREMENTS" = "dev" ]; then \
        playwright install-deps chromium && rm -rf /var/lib/apt/lists/*; \
    fi
# Les tests tournent sans fenêtre : la version réduite (headless shell) suffit. Délai et
# nouveaux essais pour les connexions lentes.
RUN if [ "$REQUIREMENTS" = "dev" ]; then \
        export PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000; \
        for essai in 1 2 3; do playwright install --only-shell chromium && exit 0; sleep 15; done; \
        exit 1; \
    fi

COPY . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
