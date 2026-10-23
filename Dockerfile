FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# git : nécessaire à pre-commit, lancé dans le conteneur (outils/hook-pre-commit.sh).
# Le dépôt monté appartient à l'utilisateur du poste : git doit l'accepter.
RUN apt-get update     && apt-get install -y --no-install-recommends git     && rm -rf /var/lib/apt/lists/*     && git config --global --add safe.directory /app

# dev (par défaut) ou prod
ARG REQUIREMENTS=dev
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/${REQUIREMENTS}.txt

COPY . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
