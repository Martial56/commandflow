import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import ProgrammingError, transaction
from django.http import HttpResponse
from django.test import RequestFactory

from comptes.tests.factories import UtilisateurFactory
from entreprises.middleware import EntrepriseCouranteMiddleware
from noyau.contexte import activer_entreprise, activer_utilisateur
from noyau.models import HistoriquePieceJointe, HistoriqueValidation, PieceJointe, Validation
from noyau.services import decider_validation, demander_validation, joindre_fichier
from noyau.tests.models import ObjetTest

pytestmark = pytest.mark.django_db


@pytest.fixture
def objet(utilisateur):
    with activer_entreprise(utilisateur.entreprise), activer_utilisateur(utilisateur):
        yield ObjetTest.objects.create(nom="commande fictive")


def test_creation_et_decision_tracees(objet, utilisateur):
    responsable = UtilisateurFactory(entreprise=utilisateur.entreprise)
    validation = demander_validation(objet, Validation.Action.ANNULATION, motif="client absent")
    with activer_utilisateur(responsable):
        decider_validation(validation, accepter=False)

    creation, decision = HistoriqueValidation.objects.filter(pgh_obj=validation).order_by("pgh_id")
    assert creation.pgh_label == "insert"
    assert creation.decision == Validation.Decision.EN_ATTENTE
    assert creation.modifie_par == utilisateur
    assert decision.pgh_label == "update"
    assert decision.decision == Validation.Decision.REFUSEE
    assert decision.validee_par == responsable
    # L'auteur exact de chaque événement est dans l'instantané (`modifie_par`) ;
    # le contexte regroupe les événements d'une même requête ou tâche.
    assert decision.modifie_par == responsable
    assert creation.pgh_context_id == decision.pgh_context_id


def test_contexte_rattache_a_l_utilisateur_et_a_l_entreprise(objet, utilisateur):
    validation = demander_validation(objet, Validation.Action.URGENCE)
    contexte = HistoriqueValidation.objects.get(pgh_obj=validation).pgh_context
    assert contexte.metadata == {
        "utilisateur": utilisateur.pk,
        "entreprise": utilisateur.entreprise_id,
    }


def test_auteur_restaure_apres_un_contexte_imbrique(objet, utilisateur):
    autre = UtilisateurFactory(entreprise=utilisateur.entreprise)
    with activer_utilisateur(autre):
        pass
    validation = demander_validation(objet, Validation.Action.URGENCE)
    evenement = HistoriqueValidation.objects.get(pgh_obj=validation)
    assert evenement.pgh_context.metadata["utilisateur"] == utilisateur.pk


def test_piece_jointe_tracee(objet, tmp_path, monkeypatch):
    from django.core.files.storage import FileSystemStorage

    monkeypatch.setattr(
        PieceJointe._meta.get_field("fichier"), "storage", FileSystemStorage(location=tmp_path)
    )
    piece = joindre_fichier(
        objet, SimpleUploadedFile("bon.pdf", b"%PDF"), PieceJointe.Type.BON_COMMANDE
    )
    evenement = HistoriquePieceJointe.objects.get(pgh_obj=piece)
    assert evenement.pgh_label == "insert"
    assert evenement.nom_original == "bon.pdf"


def test_modification_en_masse_tracee(objet):
    """Les triggers PostgreSQL voient aussi `QuerySet.update()`, qui ne passe pas par save()."""
    validation = demander_validation(objet, Validation.Action.ECART, motif="avant")
    Validation.objects.filter(pk=validation.pk).update(motif="après")
    dernier = HistoriqueValidation.objects.filter(pgh_obj=validation).latest("pgh_id")
    assert dernier.pgh_label == "update"
    assert dernier.motif == "après"


def test_historique_en_ajout_seul(objet):
    validation = demander_validation(objet, Validation.Action.ECART)
    evenements = HistoriqueValidation.objects.filter(pgh_obj=validation)
    with pytest.raises(ProgrammingError), transaction.atomic():
        evenements.update(motif="falsifié")
    with pytest.raises(ProgrammingError), transaction.atomic():
        evenements.delete()


def test_requete_rattachee_a_l_url_et_a_l_utilisateur(utilisateur):
    def vue(request):
        objet = ObjetTest.objects.create(nom="via requête")
        demander_validation(objet, Validation.Action.MODIFICATION)
        return HttpResponse()

    requete = RequestFactory().post("/commandes/42/modifier/")
    requete.user = utilisateur
    EntrepriseCouranteMiddleware(vue)(requete)

    evenement = HistoriqueValidation.objects.get()
    assert evenement.pgh_context.metadata == {
        "url": "/commandes/42/modifier/",
        "utilisateur": utilisateur.pk,
        "entreprise": utilisateur.entreprise_id,
    }
