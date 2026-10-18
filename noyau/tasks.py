from celery import shared_task


@shared_task
def verifier_celery():
    """Tâche de contrôle : confirme qu'un worker traite bien les tâches."""
    return "ok"
