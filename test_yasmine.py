import tempfile
import unittest
from pathlib import Path

from yasmine_core import Yasmine, calculer


class CalculTests(unittest.TestCase):
    def test_calcul_accepte_les_operations_simples(self):
        self.assertEqual(calculer("(12 + 8) / 2"), 10)

    def test_calcul_refuse_du_code(self):
        with self.assertRaises(ValueError):
            calculer("__import__('os').system('whoami')")


class MemoireTests(unittest.TestCase):
    def test_prenom_et_note_sont_persistes(self):
        with tempfile.TemporaryDirectory() as dossier:
            memoire = Path(dossier) / "memoire.json"
            yasmine = Yasmine(chemin_memoire=memoire, chemin_cerveau=Path(dossier) / "absent.joblib")
            self.assertIn("Ada", yasmine.repondre("Je m'appelle Ada"))
            self.assertIn("C'est noté", yasmine.repondre("note appeler Louis"))

            redemarree = Yasmine(chemin_memoire=memoire, chemin_cerveau=Path(dossier) / "absent.joblib")
            self.assertEqual(redemarree.prenom_utilisateur, "Ada")
            self.assertIn("appeler Louis", redemarree.repondre("mes notes"))

    def test_rappel_est_persiste(self):
        with tempfile.TemporaryDirectory() as dossier:
            memoire = Path(dossier) / "memoire.json"
            yasmine = Yasmine(chemin_memoire=memoire, chemin_cerveau=Path(dossier) / "absent.joblib")
            reponse = yasmine.repondre("Rappelle-moi dans 5 minutes de boire de l'eau")
            self.assertIn("Rappel enregistré", reponse)
            self.assertEqual(yasmine.memoire["taches"][0]["texte"], "boire de l'eau")


if __name__ == "__main__":
    unittest.main()
