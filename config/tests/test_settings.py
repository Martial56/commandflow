"""Vérifie les réglages imposés par CLAUDE.md."""

import importlib

from django.conf import settings


def test_fuseau_et_langue():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "Africa/Abidjan"
    assert settings.LANGUAGE_CODE == "fr"


def test_cle_primaire_bigautofield():
    assert settings.DEFAULT_AUTO_FIELD == "django.db.models.BigAutoField"


def test_base_postgresql():
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


def test_prod_sans_debug(monkeypatch):
    monkeypatch.setenv("DJANGO_DEBUG", "True")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "comandflow.example")
    prod = importlib.import_module("config.settings.prod")
    prod = importlib.reload(prod)
    assert prod.DEBUG is False
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
