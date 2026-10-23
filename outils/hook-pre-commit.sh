#!/bin/sh
# Hook git « pre-commit » de ComandFlow : lance pre-commit dans le conteneur web.
# Sous Windows, le contrôle des applications bloque les outils que pre-commit installe
# sur le poste ; dans Docker, les hooks sont ceux de la CI.
# Installation (une fois, depuis la racine du dépôt) :
#     cp outils/hook-pre-commit.sh .git/hooks/pre-commit
# Docker doit être démarré : docker compose up -d
exec docker compose exec -T web pre-commit run --hook-stage pre-commit
