"""Vérifie que PostgreSQL 16 et Redis répondent (services Docker Compose)."""

import pytest
import redis
from django.conf import settings
from django.db import connection


@pytest.mark.django_db
def test_postgresql_16_repond():
    with connection.cursor() as curseur:
        curseur.execute("SHOW server_version")
        version = curseur.fetchone()[0]
    assert version.startswith("16.")


def test_redis_repond():
    client = redis.Redis.from_url(settings.REDIS_URL)
    assert client.ping() is True
