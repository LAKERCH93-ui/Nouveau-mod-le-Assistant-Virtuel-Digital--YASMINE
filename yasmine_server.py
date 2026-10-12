"""Serveur local de Yasmine : chat, apprentissage, rappels et verrouillage.

Le serveur écoute uniquement sur 127.0.0.1. Il n'expose pas l'assistant sur le
réseau et ne contacte un modèle de langage local que si l'utilisateur l'active.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from yasmine_core import REPONSE_INCONNUE, Yasmine, normaliser
from yasmine_train import RACINE, entrainer


RACINE_WEB = RACINE / "yasmine-interface" / "dist"
FICHIER_SECURITE = RACINE / "yasmine_securite.json"
FICHIER_PARAMETRES = RACINE / "yasmine_parametres.json"
FICHIER_AGENT = RACINE / "yasmine_agent.json"
DUREE_SESSION = 60 * 60
MAX_CORPS = 32_000


def est_demande_raisonnement(message: str) -> bool:
    """Détecte les demandes qui gagnent à être confiées au modèle local.

    Le classifieur entraîné sert aux courtes commandes répétitives. Une vraie
    question, une demande détaillée ou un sujet scolaire/technique doit aller
    vers Ollama, même si le classifieur a reconnu des mots similaires.
    """
    texte = normaliser(message).rstrip(" ?!.,;:")
    commandes_directes = {
        "heure", "quelle heure est il", "donne moi l heure", "date", "quelle est la date",
        "statut", "diagnostic", "etat du systeme", "etat systeme", "briefing", "rapport", "rapport du matin",
        "mes notes", "liste mes notes", "montre mes notes", "notes", "mes rappels", "rappels", "mes taches", "mes taches a faire",
        "aide", "commandes", "que peux tu faire",
    }
    if texte in commandes_directes or texte.startswith(("note ", "retiens ", "souviens toi ", "rappelle", "minuteur", "calcule ")):
        return False
    mots = set(texte.split())
    indicateurs = {
        "explique", "resous", "resoudre", "analyse", "programme", "programmer", "code", "coder", "algorithme",
        "math", "mathematique", "equation", "derivee", "integrale", "informatique", "python", "projet",
        "pourquoi", "comment", "quel", "quelle", "quels", "quelles", "combien", "conseille", "conseil",
        "probleme", "problemes", "strategie", "plan", "compare", "choisir", "avis",
    }
    return "?" in message or len(texte.split()) >= 8 or bool(mots & indicateurs)


def est_reponse_generique(reponse: str) -> bool:
    """Évite qu'une réponse d'entraînement trop vague bloque Ollama."""
    texte = normaliser(reponse)
    return any(
        formule in texte
        for formule in (
            "de quoi s agit il",
            "de quel projet s agit il",
            "je peux essayer de t aider",
        )
    )


def lire_json(chemin: Path, defaut: dict[str, Any]) -> dict[str, Any]:
    try:
        with chemin.open("r", encoding="utf-8") as fichier:
            valeur = json.load(fichier)
        return valeur if isinstance(valeur, dict) else defaut.copy()
    except (OSError, json.JSONDecodeError):
        return defaut.copy()


def ecrire_json(chemin: Path, valeur: dict[str, Any]) -> None:
    temporaire = chemin.with_suffix(".tmp")
    with temporaire.open("w", encoding="utf-8") as fichier:
        json.dump(valeur, fichier, ensure_ascii=False, indent=2)
    temporaire.replace(chemin)


class CoffreMotDePasse:
    """Stocke uniquement un dérivé PBKDF2 salé, jamais le mot de passe."""

    iterations = 310_000

    def __init__(self, chemin: Path = FICHIER_SECURITE) -> None:
        self.chemin = chemin
        self.donnees = lire_json(chemin, {})

    @property
    def configure(self) -> bool:
        return bool(self.donnees.get("sel") and self.donnees.get("empreinte"))

    def definir(self, mot_de_passe: str) -> None:
        if len(mot_de_passe) < 8:
            raise ValueError("Choisissez un mot de passe d'au moins 8 caractères.")
        sel = secrets.token_bytes(16)
        empreinte = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), sel, self.iterations)
        self.donnees = {
            "sel": base64.b64encode(sel).decode("ascii"),
            "empreinte": base64.b64encode(empreinte).decode("ascii"),
            "iterations": self.iterations,
        }
        ecrire_json(self.chemin, self.donnees)

    def verifier(self, mot_de_passe: str) -> bool:
        if not self.configure:
            return False
        try:
            sel = base64.b64decode(self.donnees["sel"])
            attendu = base64.b64decode(self.donnees["empreinte"])
            iterations = int(self.donnees.get("iterations", self.iterations))
        except (KeyError, TypeError, ValueError):
            return False
        obtenu = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), sel, iterations)
        return hmac.compare_digest(obtenu, attendu)


class Parametres:
    def __init__(self, chemin: Path = FICHIER_PARAMETRES) -> None:
        self.chemin = chemin
        self.donnees = lire_json(
            chemin,
            {"llm_active": False, "ollama_url": "http://127.0.0.1:11434", "ollama_modele": ""},
        )

    def public(self) -> dict[str, Any]:
        return {
            "llm_active": bool(self.donnees.get("llm_active", False)),
            "ollama_url": str(self.donnees.get("ollama_url", "http://127.0.0.1:11434")),
            "ollama_modele": str(self.donnees.get("ollama_modele", "")),
        }

    def mettre_a_jour(self, valeurs: dict[str, Any]) -> dict[str, Any]:
        url = str(valeurs.get("ollama_url", self.donnees.get("ollama_url", ""))).strip().rstrip("/")
        morceau = urlparse(url)
        if morceau.scheme not in {"http", "https"} or morceau.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Par sécurité, seul un serveur de modèle local est accepté.")
        modele = str(valeurs.get("ollama_modele", self.donnees.get("ollama_modele", ""))).strip()
        if len(modele) > 100:
            raise ValueError("Le nom du modèle est trop long.")
        active = bool(valeurs.get("llm_active", False))
        if active and not modele:
            raise ValueError("Indiquez le nom du modèle local à utiliser.")
        self.donnees = {"llm_active": active, "ollama_url": url, "ollama_modele": modele}
        ecrire_json(self.chemin, self.donnees)
        return self.public()


class RegistreAgent:
    """Configuration persistante du cockpit et des compétences locales."""

    catalogue = {
        "raisonnement": {
            "nom": "Raisonnement",
            "description": "Analyse, explications, programmation et discussion avec le modèle local.",
            "actif": True,
        },
        "memoire": {
            "nom": "Mémoire persistante",
            "description": "Notes, préférences et rappels sauvegardés sur ce PC.",
            "actif": True,
        },
        "planification": {
            "nom": "Planification",
            "description": "Rappels, briefing et organisation des prochaines actions.",
            "actif": True,
        },
        "actions_pc": {
            "nom": "Actions sur le PC",
            "description": "Ouverture d'applications autorisées après votre confirmation.",
            "actif": True,
        },
        "apprentissage": {
            "nom": "Apprentissage",
            "description": "Phrases et réponses personnalisées entraînées depuis l'interface.",
            "actif": True,
        },
    }

    connecteurs = (
        {"id": "email", "nom": "Messagerie", "service": "Gmail", "detail": "Lecture et brouillons après une connexion explicite."},
        {"id": "agenda", "nom": "Agenda", "service": "Google Calendar", "detail": "Briefings et création d'événements après autorisation."},
        {"id": "navigateur", "nom": "Navigateur", "service": "Windows", "detail": "Actions locales limitées, toujours confirmées."},
        {"id": "messagerie", "nom": "Canal de discussion", "service": "Telegram / WhatsApp", "detail": "À relier par une clé ou un compte dédié."},
    )

    def __init__(self, chemin: Path = FICHIER_AGENT) -> None:
        self.chemin = chemin
        self.donnees = lire_json(chemin, {"skills": {}})
        if not isinstance(self.donnees.get("skills"), dict):
            self.donnees["skills"] = {}

    def actif(self, identifiant: str) -> bool:
        configuration = self.catalogue.get(identifiant)
        if configuration is None:
            return False
        return bool(self.donnees["skills"].get(identifiant, configuration["actif"]))

    def modifier(self, identifiant: str, actif: bool) -> None:
        if identifiant not in self.catalogue or not isinstance(actif, bool):
            raise ValueError("Compétence inconnue ou réglage invalide.")
        self.donnees["skills"][identifiant] = actif
        ecrire_json(self.chemin, self.donnees)

    def public(self, assistant: Yasmine, parametres: Parametres) -> dict[str, Any]:
        memoire = assistant.memoire
        competences = [
            {
                "id": identifiant,
                "nom": configuration["nom"],
                "description": configuration["description"],
                "actif": self.actif(identifiant),
            }
            for identifiant, configuration in self.catalogue.items()
        ]
        llm = parametres.public()
        return {
            "nom": "Yasmine",
            "cerveau": {
                "etat": "Ollama local actif" if llm["llm_active"] else "Mode commandes locales",
                "modele": llm["ollama_modele"] or "Aucun modèle choisi",
            },
            "memoire": {
                "prenom": str(memoire.get("utilisateur", {}).get("prenom", "")),
                "notes": len(memoire.get("notes", [])),
                "rappels": len(memoire.get("taches", [])),
            },
            "competences": competences,
            "connecteurs": [{**connecteur, "etat": "À connecter"} for connecteur in self.connecteurs],
        }


class ModeleLocal:
    instruction = (
        "Tu es Yasmine, un assistant personnel francophone fiable et clair. "
        "Tu aides à raisonner, programmer, organiser et expliquer pas à pas. "
        "Si la question est suffisamment précise, réponds directement et utilement : ne réponds pas seulement « De quoi s'agit-il ? ». "
        "S'il manque une information indispensable, pose une question de précision concrète. "
        "N'affirme jamais avoir effectué une action réelle si tu ne l'as pas fait. "
        "Pour les décisions risquées, demande confirmation. Réponds naturellement mais de façon concise."
    )

    def __init__(self, parametres: Parametres) -> None:
        self.parametres = parametres

    def repondre(self, historique: list[dict[str, str]], message: str) -> str:
        reglages = self.parametres.public()
        if not reglages["llm_active"]:
            return REPONSE_INCONNUE
        messages = [{"role": "system", "content": self.instruction}, *historique[-12:], {"role": "user", "content": message}]
        corps = json.dumps(
            {"model": reglages["ollama_modele"], "messages": messages, "stream": False},
            ensure_ascii=False,
        ).encode("utf-8")
        requete = urllib.request.Request(
            reglages["ollama_url"] + "/api/chat",
            data=corps,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(requete, timeout=90) as reponse:
                contenu = json.loads(reponse.read().decode("utf-8"))
            texte = str(contenu.get("message", {}).get("content", "")).strip()
            return texte or "Le modèle local n'a pas produit de réponse."
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return "Le modèle local n'est pas joignable. Vérifiez Ollama, son modèle et les paramètres de Yasmine."


class ActionsOrdinateur:
    """Actions Windows limitées à une liste blanche et toujours confirmées.

    Un modèle de langage ne reçoit jamais de droit d'exécuter une commande. Il
    peut seulement faire proposer une application connue à l'utilisateur.
    """

    delai_confirmation = 90
    applications = {
        "vscode": {"nom": "Visual Studio Code", "alias": ("visual studio code", "vs code", "vscode")},
        "calculatrice": {"nom": "la calculatrice", "alias": ("calculatrice", "calc")},
        "bloc_notes": {"nom": "le Bloc-notes", "alias": ("bloc notes", "bloc-notes", "notepad")},
        "explorateur": {"nom": "l'Explorateur de fichiers", "alias": ("explorateur de fichiers", "explorateur", "mes fichiers")},
        "chrome": {"nom": "Google Chrome", "alias": ("google chrome", "chrome")},
        "edge": {"nom": "Microsoft Edge", "alias": ("microsoft edge", "edge")},
    }

    @staticmethod
    def _est_demande_ouverture(texte: str) -> bool:
        return any(mot in texte.split() for mot in ("ouvre", "ouvrir", "lance", "lancer", "demarre"))

    def identifier(self, message: str) -> str | None:
        """Retourne seulement une application explicitement demandée."""
        texte = normaliser(message)
        if not self._est_demande_ouverture(texte):
            return None
        for cle, application in self.applications.items():
            if any(alias in texte for alias in application["alias"]):
                return cle
        return None

    def proposition(self, cle: str) -> dict[str, str]:
        application = self.applications[cle]
        return {"id": secrets.token_urlsafe(18), "type": "ouvrir_application", "cle": cle, "nom": application["nom"]}

    @staticmethod
    def _candidats_vscode() -> list[str]:
        candidats = [shutil.which("code")]
        for variable, segments in (
            ("LOCALAPPDATA", ("Programs", "Microsoft VS Code", "Code.exe")),
            ("ProgramFiles", ("Microsoft VS Code", "Code.exe")),
            ("ProgramFiles(x86)", ("Microsoft VS Code", "Code.exe")),
        ):
            base = os.environ.get(variable)
            if base:
                candidats.append(str(Path(base, *segments)))
        return [candidat for candidat in candidats if candidat]

    @staticmethod
    def _candidats_navigateur(navigateur: str) -> list[str]:
        dossier = ("Google", "Chrome", "Application", "chrome.exe") if navigateur == "chrome" else ("Microsoft", "Edge", "Application", "msedge.exe")
        executable = "chrome" if navigateur == "chrome" else "msedge"
        candidats = [shutil.which(executable)]
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidats.append(str(Path(base, *dossier)))
        return [candidat for candidat in candidats if candidat]

    @staticmethod
    def _lancer_executable(candidats: list[str]) -> bool:
        for candidat in candidats:
            if shutil.which(candidat) or Path(candidat).is_file():
                subprocess.Popen([candidat], close_fds=True)
                return True
        return False

    def lancer(self, cle: str) -> str:
        if os.name != "nt":
            raise ValueError("Cette fonction est disponible uniquement sous Windows.")
        if cle == "vscode":
            if not self._lancer_executable(self._candidats_vscode()):
                raise ValueError("Visual Studio Code est introuvable. Installez-le ou ajoutez la commande « code » au PATH.")
        elif cle in {"chrome", "edge"}:
            if not self._lancer_executable(self._candidats_navigateur(cle)):
                raise ValueError(f"{self.applications[cle]['nom']} est introuvable sur ce PC.")
        elif cle == "calculatrice":
            subprocess.Popen(["calc.exe"], close_fds=True)
        elif cle == "bloc_notes":
            subprocess.Popen(["notepad.exe"], close_fds=True)
        elif cle == "explorateur":
            subprocess.Popen(["explorer.exe"], close_fds=True)
        else:
            raise ValueError("Cette application n'est pas autorisée.")
        return f"J'ouvre {self.applications[cle]['nom']}."


@dataclass
class Session:
    expire_a: float
    historique: list[dict[str, str]] = field(default_factory=list)
    action_en_attente: dict[str, Any] | None = None


class EtatYasmine:
    def __init__(self) -> None:
        self.assistant = Yasmine()
        self.securite = CoffreMotDePasse()
        self.parametres = Parametres()
        self.agent = RegistreAgent()
        self.modele = ModeleLocal(self.parametres)
        self.actions = ActionsOrdinateur()
        self.sessions: dict[str, Session] = {}
        self.verrou = threading.Lock()

    def ouvrir_session(self) -> str:
        jeton = secrets.token_urlsafe(32)
        with self.verrou:
            self.sessions[jeton] = Session(time.time() + DUREE_SESSION)
        return jeton

    def session(self, jeton: str | None, actualiser: bool = False) -> Session | None:
        if not jeton:
            return None
        with self.verrou:
            session = self.sessions.get(jeton)
            if not session or session.expire_a <= time.time():
                self.sessions.pop(jeton, None)
                return None
            if actualiser:
                session.expire_a = time.time() + DUREE_SESSION
            return session

    def apprendre(self, intention: str, exemple: str, reponse: str) -> dict[str, int]:
        intention = normaliser(intention).replace(" ", "_")
        if not intention or len(intention) > 40 or not all(caractere.isalnum() or caractere == "_" for caractere in intention):
            raise ValueError("Le nom de l'intention doit contenir des lettres, chiffres ou tirets bas.")
        exemple, reponse = exemple.strip(), reponse.strip()
        if not exemple or len(exemple) > 500:
            raise ValueError("Ajoutez une phrase d'entraînement de 1 à 500 caractères.")
        chemin = RACINE / "intentions.json"
        donnees = lire_json(chemin, {})
        contenu = donnees.setdefault(intention, {"exemples": [], "reponses": []})
        if exemple not in contenu["exemples"]:
            contenu["exemples"].append(exemple)
        if reponse and reponse not in contenu["reponses"]:
            contenu["reponses"].append(reponse)
        if not contenu["reponses"]:
            contenu["reponses"].append("Je vous ai compris.")
        ecrire_json(chemin, donnees)
        resultat = entrainer(chemin)
        self.assistant.recharger_cerveau()
        return resultat


ETAT = EtatYasmine()


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "YasmineLocal/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, statut: HTTPStatus, donnees: dict[str, Any]) -> None:
        brut = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(brut)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(brut)

    def _corps(self) -> dict[str, Any] | None:
        try:
            taille = int(self.headers.get("Content-Length", "0"))
            if not 0 < taille <= MAX_CORPS:
                raise ValueError
            valeur = json.loads(self.rfile.read(taille).decode("utf-8"))
            return valeur if isinstance(valeur, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"erreur": "Requête invalide."})
            return None

    def _session(self, actualiser: bool) -> Session | None:
        session = ETAT.session(self.headers.get("X-Yasmine-Token"), actualiser=actualiser)
        if session is None:
            self._json(HTTPStatus.UNAUTHORIZED, {"erreur": "Session verrouillée."})
        return session

    def do_GET(self) -> None:
        chemin = urlparse(self.path).path
        if chemin == "/api/security/status":
            self._json(HTTPStatus.OK, {"mot_de_passe_configure": ETAT.securite.configure, "inactivite_minutes": 60})
        elif chemin == "/api/notifications":
            if self._session(actualiser=False):
                self._json(HTTPStatus.OK, {"alertes": ETAT.assistant.alertes_pretes()})
        elif chemin == "/api/training":
            if self._session(actualiser=True):
                self._json(HTTPStatus.OK, {"intentions": lire_json(RACINE / "intentions.json", {})})
        elif chemin == "/api/settings":
            if self._session(actualiser=True):
                self._json(HTTPStatus.OK, ETAT.parametres.public())
        elif chemin == "/api/status":
            if self._session(actualiser=True):
                self._json(HTTPStatus.OK, {"nom": "Yasmine", "modele_local": ETAT.parametres.public()["llm_active"]})
        elif chemin == "/api/agent":
            if self._session(actualiser=True):
                self._json(HTTPStatus.OK, ETAT.agent.public(ETAT.assistant, ETAT.parametres))
        else:
            self._fichier(chemin)

    def do_POST(self) -> None:
        chemin = urlparse(self.path).path
        corps = self._corps()
        if corps is None:
            return
        if chemin == "/api/security/setup":
            if ETAT.securite.configure:
                self._json(HTTPStatus.CONFLICT, {"erreur": "Un mot de passe est déjà configuré."})
                return
            try:
                ETAT.securite.definir(str(corps.get("mot_de_passe", "")))
                self._json(HTTPStatus.CREATED, {"jeton": ETAT.ouvrir_session()})
            except ValueError as erreur:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": str(erreur)})
            return
        if chemin == "/api/security/unlock":
            if ETAT.securite.verifier(str(corps.get("mot_de_passe", ""))):
                self._json(HTTPStatus.OK, {"jeton": ETAT.ouvrir_session()})
            else:
                self._json(HTTPStatus.UNAUTHORIZED, {"erreur": "Mot de passe incorrect."})
            return

        session = self._session(actualiser=True)
        if session is None:
            return
        if chemin == "/api/chat":
            message = str(corps.get("message", "")).strip()
            if not message or len(message) > 4_000:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": "Le message doit contenir entre 1 et 4 000 caractères."})
                return
            cle_application = ETAT.actions.identifier(message)
            action: dict[str, str] | None = None
            if cle_application and not ETAT.agent.actif("actions_pc"):
                reponse = "La compétence Actions sur le PC est désactivée dans le cockpit Agent."
            elif cle_application:
                action = ETAT.actions.proposition(cle_application)
                action["expire_a"] = str(time.time() + ETAT.actions.delai_confirmation)
                session.action_en_attente = action
                reponse = f"Je peux ouvrir {action['nom']}. Confirmez avec le bouton ci-dessous."
            else:
                reponse = ETAT.assistant.repondre(message)
                demande_complexe = est_demande_raisonnement(message)
                if (reponse == REPONSE_INCONNUE and ETAT.agent.actif("raisonnement")) or (
                    ETAT.parametres.public()["llm_active"]
                    and ETAT.agent.actif("raisonnement")
                    and (demande_complexe or est_reponse_generique(reponse))
                ):
                    reponse = ETAT.modele.repondre(session.historique, message)
            session.historique.extend([{"role": "user", "content": message}, {"role": "assistant", "content": reponse}])
            session.historique = session.historique[-12:]
            self._json(HTTPStatus.OK, {"reponse": reponse, "action": action, "alertes": ETAT.assistant.alertes_pretes()})
        elif chemin == "/api/actions/confirmer":
            action = session.action_en_attente
            identifiant = str(corps.get("id", ""))
            session.action_en_attente = None
            if not action or not hmac.compare_digest(str(action.get("id", "")), identifiant):
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": "Cette confirmation n'est plus valable. Demandez à nouveau l'ouverture."})
                return
            if float(action.get("expire_a", 0)) < time.time():
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": "La confirmation a expiré. Demandez à nouveau l'ouverture."})
                return
            try:
                message = ETAT.actions.lancer(str(action["cle"]))
                self._json(HTTPStatus.OK, {"message": message})
            except (ValueError, OSError) as erreur:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": str(erreur)})
        elif chemin == "/api/training":
            if not ETAT.agent.actif("apprentissage"):
                self._json(HTTPStatus.FORBIDDEN, {"erreur": "La compétence Apprentissage est en pause dans le cockpit Agent."})
                return
            try:
                resultat = ETAT.apprendre(
                    str(corps.get("intention", "")),
                    str(corps.get("exemple", "")),
                    str(corps.get("reponse", "")),
                )
                self._json(HTTPStatus.OK, {"message": "Yasmine a été réentraînée.", **resultat})
            except (ValueError, KeyError) as erreur:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": str(erreur)})
        elif chemin == "/api/settings":
            try:
                self._json(HTTPStatus.OK, ETAT.parametres.mettre_a_jour(corps))
            except ValueError as erreur:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": str(erreur)})
        elif chemin == "/api/skills":
            try:
                ETAT.agent.modifier(str(corps.get("id", "")), corps.get("actif"))
                self._json(HTTPStatus.OK, ETAT.agent.public(ETAT.assistant, ETAT.parametres))
            except ValueError as erreur:
                self._json(HTTPStatus.BAD_REQUEST, {"erreur": str(erreur)})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"erreur": "Point d'accès inconnu."})

    def _fichier(self, chemin: str) -> None:
        relatif = "index.html" if chemin in {"", "/"} else unquote(chemin).lstrip("/")
        cible = (RACINE_WEB / relatif).resolve()
        if RACINE_WEB not in cible.parents and cible != RACINE_WEB or not cible.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        contenu = cible.read_bytes()
        mime, _ = mimetypes.guess_type(cible.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mime or "application/octet-stream") + ("; charset=utf-8" if cible.suffix in {".html", ".css", ".js"} else ""))
        self.send_header("Content-Length", str(len(contenu)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(contenu)


def main() -> None:
    analyseur = argparse.ArgumentParser(description="Serveur web local de Yasmine")
    analyseur.add_argument("--port", type=int, default=8765)
    arguments = analyseur.parse_args()
    if not RACINE_WEB.is_dir():
        raise SystemExit("Interface web introuvable : yasmine-interface/dist")
    with ThreadingHTTPServer(("127.0.0.1", arguments.port), Gestionnaire) as serveur:
        print(f"Yasmine est prête sur http://127.0.0.1:{arguments.port}")
        print("Le serveur est local. Utilisez Ctrl+C pour l'arrêter.")
        try:
            serveur.serve_forever()
        except KeyboardInterrupt:
            print("\nServeur Yasmine arrêté.")


if __name__ == "__main__":
    main()
