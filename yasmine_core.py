"""Le coeur local de Yasmine.

Yasmine n'exécute jamais de commande système provenant du texte saisi. Les
actions d'ouverture sont volontairement limitées à une courte liste blanche.
"""

from __future__ import annotations

import ast
import json
import math
import os
import platform
import random
import re
import threading
import unicodedata
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import joblib


NOM_ASSISTANT = "Yasmine"
FICHIER_CERVEAU = Path(__file__).with_name("yasmine_modele.joblib")
FICHIER_MEMOIRE = Path(__file__).with_name("yasmine_memoire.json")
REPONSE_INCONNUE = "Je n'ai pas encore la compétence pour cette demande. Dites « aide » pour mes commandes locales."


def normaliser(texte: str) -> str:
    """Met un texte sous une forme facile à reconnaître."""
    texte = unicodedata.normalize("NFD", texte.casefold())
    texte = "".join(caractere for caractere in texte if unicodedata.category(caractere) != "Mn")
    texte = re.sub(r"[-_’']", " ", texte)
    return " ".join(texte.split())


def calculer(expression: str) -> float:
    """Évalue une expression arithmétique sans utiliser ``eval``.

    Seules les opérations +, -, *, /, %, ** et les parenthèses sont admises.
    """
    expression = expression.strip().replace(",", ".")
    if not expression or len(expression) > 100:
        raise ValueError("L'expression doit contenir entre 1 et 100 caractères.")

    try:
        arbre = ast.parse(expression, mode="eval")
    except SyntaxError as erreur:
        raise ValueError("Je n'ai pas reconnu ce calcul.") from erreur

    def parcourir(noeud: ast.AST) -> float:
        if isinstance(noeud, ast.Expression):
            return parcourir(noeud.body)
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, (int, float)) and not isinstance(noeud.value, bool):
            valeur = float(noeud.value)
            if not math.isfinite(valeur):
                raise ValueError("Les nombres doivent être finis.")
            return valeur
        if isinstance(noeud, ast.UnaryOp) and isinstance(noeud.op, (ast.UAdd, ast.USub)):
            valeur = parcourir(noeud.operand)
            return valeur if isinstance(noeud.op, ast.UAdd) else -valeur
        if isinstance(noeud, ast.BinOp) and isinstance(
            noeud.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
        ):
            gauche, droite = parcourir(noeud.left), parcourir(noeud.right)
            if isinstance(noeud.op, ast.Add):
                resultat = gauche + droite
            elif isinstance(noeud.op, ast.Sub):
                resultat = gauche - droite
            elif isinstance(noeud.op, ast.Mult):
                resultat = gauche * droite
            elif isinstance(noeud.op, ast.Div):
                resultat = gauche / droite
            elif isinstance(noeud.op, ast.Mod):
                resultat = gauche % droite
            else:
                if abs(droite) > 100 or (gauche == 0 and droite < 0):
                    raise ValueError("Cette puissance est hors limites.")
                resultat = gauche**droite
            if not isinstance(resultat, (int, float)) or not math.isfinite(resultat) or abs(resultat) > 1e100:
                raise ValueError("Le résultat est hors limites.")
            return resultat
        raise ValueError("Je peux uniquement calculer avec des nombres et +, -, *, /, % ou **.")

    try:
        return parcourir(arbre)
    except ZeroDivisionError as erreur:
        raise ValueError("Division par zéro impossible.") from erreur


class Yasmine:
    """Assistant conversationnel local avec mémoire, utilitaires et personnalité."""

    def __init__(
        self,
        chemin_memoire: Path | str = FICHIER_MEMOIRE,
        chemin_cerveau: Path | str = FICHIER_CERVEAU,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        self.chemin_memoire = Path(chemin_memoire)
        self.memoire = self._charger_memoire()
        self.cerveau = self._charger_cerveau(Path(chemin_cerveau))
        self.minuteurs: list[dict[str, Any]] = []
        self._alertes: list[str] = []
        self._notifier = notifier
        self._verrou_minuteurs = threading.Lock()

    def _charger_memoire(self) -> dict[str, Any]:
        base = {"utilisateur": {"prenom": ""}, "notes": [], "taches": []}
        if not self.chemin_memoire.exists():
            return base
        try:
            with self.chemin_memoire.open("r", encoding="utf-8") as fichier:
                chargee = json.load(fichier)
            if isinstance(chargee, dict):
                base.update(chargee)
            if not isinstance(base.get("utilisateur"), dict):
                base["utilisateur"] = {"prenom": ""}
            if not isinstance(base.get("notes"), list):
                base["notes"] = []
            if not isinstance(base.get("taches"), list):
                base["taches"] = []
            return base
        except (OSError, json.JSONDecodeError):
            return base

    def _sauvegarder_memoire(self) -> None:
        self.chemin_memoire.parent.mkdir(parents=True, exist_ok=True)
        with self.chemin_memoire.open("w", encoding="utf-8") as fichier:
            json.dump(self.memoire, fichier, ensure_ascii=False, indent=2)

    @staticmethod
    def _charger_cerveau(chemin: Path) -> dict[str, Any] | None:
        try:
            return joblib.load(chemin) if chemin.exists() else None
        except (OSError, ValueError, KeyError):
            return None

    @property
    def prenom_utilisateur(self) -> str:
        return str(self.memoire["utilisateur"].get("prenom", "")).strip()

    def _salutation(self) -> str:
        heure = datetime.now().hour
        moment = "Bonsoir" if heure >= 18 else "Bonjour" if heure >= 6 else "Bonsoir"
        nom = f", {self.prenom_utilisateur}" if self.prenom_utilisateur else ""
        return f"{moment}{nom}. Systèmes prêts. Que puis-je faire pour vous ?"

    def _heure(self) -> str:
        maintenant = datetime.now()
        return f"Il est {maintenant:%H:%M}, le {maintenant:%d/%m/%Y}."

    def _statut(self) -> str:
        return (
            "Diagnostic local : opérationnel. "
            f"Système : {platform.system()} {platform.release()} · Python : {platform.python_version()}."
        )

    def _briefing(self) -> str:
        nom = self.prenom_utilisateur or ""
        introduction = f"Bonjour {nom}. " if nom else "Bonjour. "
        notes = len(self.memoire["notes"])
        return introduction + self._heure() + f" Vous avez {notes} note{'s' if notes != 1 else ''} enregistrée{'s' if notes != 1 else ''}."

    def _ajouter_note(self, texte: str) -> str:
        texte = texte.strip(" .")
        if not texte:
            return "Dites-moi le contenu de la note après « note »."
        self.memoire["notes"].append(
            {"texte": texte, "cree_le": datetime.now().isoformat(timespec="seconds")}
        )
        self._sauvegarder_memoire()
        return f"C'est noté : « {texte} »."

    def _lister_notes(self) -> str:
        notes = self.memoire["notes"]
        if not notes:
            return "Aucune note enregistrée pour l'instant."
        lignes = [f"{index}. {note.get('texte', '')}" for index, note in enumerate(notes, start=1)]
        return "Voici vos notes :\n" + "\n".join(lignes)

    def _supprimer_note(self, numero: str) -> str:
        index = int(numero) - 1
        notes = self.memoire["notes"]
        if not 0 <= index < len(notes):
            return "Je ne trouve pas cette note. Utilisez « mes notes » pour voir les numéros."
        note = notes.pop(index)
        self._sauvegarder_memoire()
        return f"Note supprimée : « {note.get('texte', '')} »."

    def _programmer_minuteur(self, nombre: str, unite: str) -> str:
        duree = int(nombre) * (60 if normaliser(unite).startswith("minute") else 1)
        if not 1 <= duree <= 86_400:
            return "Choisissez une durée entre 1 seconde et 24 heures."
        echeance = datetime.now().timestamp() + duree
        minuteur = {"echeance": echeance, "duree": duree}
        with self._verrou_minuteurs:
            self.minuteurs.append(minuteur)
        alarme = threading.Timer(duree, self._declencher_minuteur, args=(minuteur,))
        alarme.daemon = True
        alarme.start()
        libelle = f"{nombre} {'minute' if nombre == '1' and duree == 60 else 'minutes' if duree != int(nombre) else 'seconde' if nombre == '1' else 'secondes'}"
        return f"Minuteur lancé pour {libelle}."

    def _creer_rappel(self, nombre: str, unite: str, texte: str) -> str:
        multiplicateurs = {"minute": 60, "heure": 3600, "jour": 86_400}
        unite_normalisee = normaliser(unite).rstrip("s")
        duree = int(nombre) * multiplicateurs[unite_normalisee]
        if not texte.strip() or not 1 <= duree <= 31_536_000:
            return "Donnez-moi une tâche et une échéance comprise entre une minute et un an."
        echeance = datetime.now().timestamp() + duree
        tache = {
            "id": uuid.uuid4().hex,
            "texte": texte.strip(" ."),
            "echeance": echeance,
            "rappel_envoye": False,
            "cree_le": datetime.now().isoformat(timespec="seconds"),
        }
        self.memoire["taches"].append(tache)
        self._sauvegarder_memoire()
        libelle = f"{nombre} {unite_normalisee}{'' if nombre == '1' else 's'}"
        return f"Rappel enregistré dans {libelle} : « {tache['texte']} »."

    def _lister_taches(self) -> str:
        taches = [tache for tache in self.memoire["taches"] if not tache.get("rappel_envoye")]
        if not taches:
            return "Aucun rappel à venir."
        lignes = []
        for index, tache in enumerate(taches, start=1):
            date = datetime.fromtimestamp(float(tache["echeance"])).strftime("%d/%m à %H:%M")
            lignes.append(f"{index}. {tache.get('texte', '')} — {date}")
        return "Rappels à venir :\n" + "\n".join(lignes)

    def _declencher_minuteur(self, minuteur: dict[str, Any]) -> None:
        message = "⏱ Minuteur terminé."
        with self._verrou_minuteurs:
            if minuteur not in self.minuteurs:
                return
            self.minuteurs.remove(minuteur)
            if self._notifier is None:
                self._alertes.append(message)
        if self._notifier is not None:
            self._notifier(message)

    def alertes_pretes(self) -> list[str]:
        """Retourne les minuteurs et rappels arrivés à échéance."""
        with self._verrou_minuteurs:
            alertes, self._alertes = self._alertes, []
        maintenant = datetime.now().timestamp()
        rappels = []
        for tache in self.memoire["taches"]:
            if not tache.get("rappel_envoye") and float(tache.get("echeance", 0)) <= maintenant:
                tache["rappel_envoye"] = True
                rappels.append(f"⏰ Rappel : {tache.get('texte', 'une tâche est arrivée à échéance')}.")
        if rappels:
            self._sauvegarder_memoire()
        return alertes + rappels

    def recharger_cerveau(self) -> None:
        """Recharge le modèle après un entraînement effectué depuis l'interface."""
        self.cerveau = self._charger_cerveau(FICHIER_CERVEAU)
        return alertes

    @staticmethod
    def _ouvrir(cible: str) -> str | None:
        cibles_web = {
            "google": "https://www.google.com",
            "youtube": "https://www.youtube.com",
            "github": "https://github.com",
        }
        if cible in cibles_web:
            webbrowser.open(cibles_web[cible])
            return f"J'ouvre {cible}."
        if cible in {"calculatrice", "calc"} and os.name == "nt":
            os.startfile("calc.exe")  # type: ignore[attr-defined]
            return "J'ouvre la calculatrice."
        return None

    def _reponse_modele(self, message: str) -> str | None:
        if not self.cerveau:
            return None
        try:
            vectoriseur = self.cerveau["vectoriseur"]
            modele = self.cerveau["modele"]
            seuil = self.cerveau["seuil_confiance"]
            donnees = self.cerveau["donnees"]
            probabilites = modele.predict_proba(vectoriseur.transform([message]))[0]
            index = probabilites.argmax()
            if probabilites[index] < seuil:
                return None
            intention = modele.classes_[index]
            return random.choice(donnees[intention]["reponses"])
        except (KeyError, AttributeError, IndexError, TypeError):
            return None

    def repondre(self, message: str) -> str:
        """Produit une réponse sans exécuter d'action arbitraire."""
        original = message.strip()
        texte = normaliser(original)
        if not texte:
            return "Je vous écoute."

        texte = re.sub(r"[?!.,;:]+$", "", texte).strip()

        # Le mot d'activation reste facultatif dans le terminal.
        texte = re.sub(rf"^{NOM_ASSISTANT.casefold()}[,: ]+", "", texte).strip()

        if texte in {"bonjour", "salut", "bonsoir", "coucou", "hello", "hey"}:
            return self._salutation()
        if texte in {"aide", "commandes", "que peux tu faire"}:
            return (
                "Je peux donner l'heure, faire un calcul, mémoriser des notes, lancer un minuteur, "
                "faire un briefing, créer des rappels et ouvrir Google, YouTube, GitHub ou la calculatrice.\n"
                "Depuis l'interface, je peux aussi proposer d'ouvrir Visual Studio Code, le Bloc-notes, l'Explorateur, Chrome ou Edge après votre confirmation.\n"
                "Exemples : « note appeler Léa », « mes notes », « calcule (12 + 8) / 2 », "
                "« rappelle-moi dans 30 minutes de sortir le pain », « briefing »."
            )
        if texte in {"heure", "quelle heure est il", "donne moi l heure", "date", "quelle est la date"}:
            return self._heure()
        if texte in {"statut", "diagnostic", "etat du systeme", "etat systeme"}:
            return self._statut()
        if texte in {"briefing", "rapport", "rapport du matin"}:
            return self._briefing()
        if texte in {"comment je m appelle", "quel est mon prenom", "tu connais mon nom"}:
            return f"Vous vous appelez {self.prenom_utilisateur}." if self.prenom_utilisateur else "Je ne connais pas encore votre prénom."

        correspondance = re.match(r"(?:je m['’ ]appelle|mon prénom est)\s+([a-zà-ÿ' -]{2,40})$", original, re.IGNORECASE)
        if correspondance:
            prenom = correspondance.group(1).strip().title()
            self.memoire["utilisateur"]["prenom"] = prenom
            self._sauvegarder_memoire()
            return f"Ravi de vous connaître, {prenom}. Je m'en souviendrai."

        correspondance = re.match(r"(?:note|retiens|souviens[- ]toi(?: que)?)\s*[:,-]?\s+(.+)$", original, re.IGNORECASE)
        if correspondance:
            return self._ajouter_note(correspondance.group(1))
        if texte in {"mes notes", "liste mes notes", "montre mes notes", "notes"}:
            return self._lister_notes()
        correspondance = re.match(r"(?:supprime|efface) (?:la )?note (\d+)$", texte)
        if correspondance:
            return self._supprimer_note(correspondance.group(1))

        correspondance = re.match(r"(?:minuteur|lance (?:un )?minuteur)\s+(\d+)\s*(seconde|secondes|minute|minutes)$", texte)
        if correspondance:
            return self._programmer_minuteur(*correspondance.groups())

        correspondance = re.match(
            r"rappelle(?:[- ]moi)?\s+dans\s+(\d+)\s+(minute|minutes|heure|heures|jour|jours)\s+(?:de\s+)?(.+)$",
            original,
            re.IGNORECASE,
        )
        if correspondance:
            return self._creer_rappel(*correspondance.groups())
        if texte in {"mes rappels", "rappels", "mes taches", "mes taches a faire"}:
            return self._lister_taches()

        correspondance = re.match(r"(?:calcule|combien font?|resultat de)\s+(.+)$", original, re.IGNORECASE)
        if correspondance:
            try:
                resultat = calculer(correspondance.group(1))
                return f"Résultat : {resultat:g}."
            except ValueError as erreur:
                return str(erreur)

        correspondance = re.match(r"ouvre\s+(google|youtube|github|calculatrice|calc)$", texte)
        if correspondance:
            return self._ouvrir(correspondance.group(1)) or "Je ne peux pas ouvrir cela."

        reponse = self._reponse_modele(original)
        if reponse:
            return reponse
        return REPONSE_INCONNUE
