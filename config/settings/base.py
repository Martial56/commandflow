"""Réglages communs à tous les environnements."""

from datetime import timedelta
from pathlib import Path

import environ
import pghistory
from django.db import models

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Bibliothèques
    "rules.apps.AutodiscoverRulesConfig",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "axes",
    "pgtrigger",
    "pghistory",
    "django_tailwind_cli",
    # ComandFlow
    "noyau",
    "entreprises",
    "comptes",
]

AUTH_USER_MODEL = "comptes.Utilisateur"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "entreprises.middleware.EntrepriseCouranteMiddleware",
    "comptes.middleware.DoubleAuthentificationObligatoireMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # En dernier : transforme un blocage en réponse « compte bloqué ».
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "comptes.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Base de données : PostgreSQL 16, URL fournie par l'environnement.
DATABASES = {"default": env.db("DATABASE_URL")}

# Redis : file Celery et cache.
REDIS_URL = env("REDIS_URL")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentification : django-axes (blocage) en premier, puis permissions objet
# (django-rules), admin Django (identifiant) et allauth (téléphone ou e-mail).
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "rules.permissions.ObjectPermissionBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "accueil"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"

# django-allauth : connexion par téléphone ou e-mail + mot de passe, sans inscription
# publique (adaptateur), sans vérification par e-mail ni SMS au MVP.
ACCOUNT_ADAPTER = "comptes.adaptateurs.AdaptateurCompte"
ACCOUNT_LOGIN_METHODS = {"phone", "email"}
ACCOUNT_SIGNUP_FIELDS = ["phone*", "email", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_PHONE_VERIFICATION_ENABLED = False
ACCOUNT_SESSION_REMEMBER = False
# Le blocage après échecs est assuré par django-axes seul (règle unique de 15 minutes).
ACCOUNT_RATE_LIMITS = {"login_failed": None}
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "ComandFlow"

# django-axes : compte bloqué 15 minutes après 5 échecs (maquette 07).
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_USERNAME_CALLABLE = "comptes.axes.identifiant_connexion"
AXES_RESET_ON_SUCCESS = True
# Blocage par compte uniquement, pas par adresse IP : le personnel d'une entreprise
# partage souvent la même connexion, un blocage par IP couperait toute l'équipe.
SILENCED_SYSTEM_CHECKS = ["axes.W006"]

# django-pghistory : historique par triggers PostgreSQL (insertions et modifications).
# Tables d'historique en ajout seul : un événement ne peut être ni modifié ni supprimé.
PGHISTORY_APPEND_ONLY = True
PGHISTORY_CONTEXT_FIELD = pghistory.ContextForeignKey(on_delete=models.PROTECT)
PGHISTORY_OBJ_FIELD = pghistory.ObjForeignKey(on_delete=models.PROTECT)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Langue et fuseau horaire (Côte d'Ivoire).
LANGUAGE_CODE = "fr"
TIME_ZONE = "Africa/Abidjan"
USE_I18N = True
USE_TZ = True

# Celery : tâches de fond (Redis) et tâches planifiées (Celery Beat), à l'heure d'Abidjan.
CELERY_BROKER_URL = REDIS_URL
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_TIME_LIMIT = 10 * 60
CELERY_BEAT_SCHEDULE = {}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Tailwind CSS (exécutable autonome, sans Node). Source : assets/css/source.css ;
# fichier généré : static/css/comandflow.css (non versionné).
TAILWIND_CLI_VERSION = "4.1.3"
TAILWIND_CLI_SRC_CSS = "assets/css/source.css"
TAILWIND_CLI_DIST_CSS = "css/comandflow.css"

# Stockage. « prive » : pièces jointes, bordereaux, photos, signatures. Jamais servi
# directement : accès uniquement par lien signé (noyau.fichiers). En production, ce
# stockage passera sur un service S3 (Cloudflare R2 ou Backblaze B2).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "prive": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": env("DOSSIER_FICHIERS_PRIVES", default=BASE_DIR / "fichiers_prives")
        },
    },
}

# Durée de validité d'un lien signé vers un fichier privé, en secondes.
DUREE_LIEN_FICHIER = env.int("DUREE_LIEN_FICHIER", default=300)
