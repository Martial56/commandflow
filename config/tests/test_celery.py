from django.conf import settings

from config.celery import app
from noyau.tasks import verifier_celery


def test_configuration():
    assert app.conf.broker_url == settings.REDIS_URL
    assert app.conf.timezone == "Africa/Abidjan"
    assert app.conf.enable_utc is True


def test_taches_decouvertes():
    app.loader.import_default_modules()
    assert "noyau.tasks.verifier_celery" in app.tasks


def test_execution_immediate_en_test():
    assert verifier_celery.delay().get() == "ok"
