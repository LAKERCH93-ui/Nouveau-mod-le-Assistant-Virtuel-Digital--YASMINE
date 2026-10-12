"""Point d'entrée terminal de Yasmine."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from yasmine_core import FICHIER_MEMOIRE, Yasmine


class Voix:
    """Synthèse vocale facultative : aucun paquet n'est requis pour le chat texte."""

    def __init__(self, active: bool) -> None:
        self.engine = None
        if not active:
            return
        try:
            import pyttsx3  # type: ignore[import-not-found]

            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 180)
        except ImportError:
            print("[Voix indisponible : installez pyttsx3 avec `pip install pyttsx3`.]")

    def dire(self, texte: str) -> None:
        if self.engine is not None:
            threading.Thread(target=self._parler, args=(texte,), daemon=True).start()

    def _parler(self, texte: str) -> None:
        assert self.engine is not None
        self.engine.say(texte)
        self.engine.runAndWait()


def main() -> None:
    analyseur = argparse.ArgumentParser(description="Yasmine, assistant personnel local.")
    analyseur.add_argument("--voix", action="store_true", help="lit les réponses à voix haute (pyttsx3 requis)")
    analyseur.add_argument("--memoire", type=Path, default=FICHIER_MEMOIRE, help="emplacement de la mémoire JSON")
    arguments = analyseur.parse_args()

    voix = Voix(arguments.voix)

    def signaler(alerte: str) -> None:
        print(f"\nYasmine > {alerte}")
        voix.dire(alerte)

    yasmine = Yasmine(chemin_memoire=arguments.memoire, notifier=signaler)
    print("=== YASMINE · ASSISTANT PERSONNEL ===")
    print("Tapez « aide » pour les commandes ou « quitter » pour arrêter.\n")

    while True:
        try:
            message = input("Vous > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nYasmine > À bientôt.")
            break

        if message.casefold() in {"quitter", "quit", "exit", "au revoir"}:
            print("Yasmine > À bientôt.")
            break

        reponse = yasmine.repondre(message)
        print(f"Yasmine > {reponse}\n")
        voix.dire(reponse)


if __name__ == "__main__":
    main()
