
import json
from pathlib import Path

import joblib

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Tous les chemins sont relatifs à ce script, pas au dossier depuis lequel il est lancé.
RACINE = Path(__file__).parent

def entrainer(
    chemin_donnees: Path = RACINE / "intentions.json",
    chemin_modele: Path = RACINE / "yasmine_modele.joblib",
) -> dict[str, int]:
    """Reconstruit le classifieur d'intentions après un apprentissage manuel."""
    with chemin_donnees.open("r", encoding="utf-8") as fichier:
        donnees = json.load(fichier)

    phrases: list[str] = []
    intentions: list[str] = []
    for intention, contenu in donnees.items():
        for phrase in contenu["exemples"]:
            phrases.append(phrase)
            intentions.append(intention)

    if len(set(intentions)) < 2:
        raise ValueError("Il faut au moins deux intentions pour entraîner Yasmine.")

    vectoriseur = TfidfVectorizer(lowercase=True, ngram_range=(1, 2))
    X = vectoriseur.fit_transform(phrases)
    modele = LogisticRegression(max_iter=1000, random_state=42)
    modele.fit(X, intentions)

    joblib.dump(
        {
            "vectoriseur": vectoriseur,
            "modele": modele,
            "seuil_confiance": 0.25,
            "donnees": donnees,
        },
        chemin_modele,
    )
    return {"phrases": len(phrases), "intentions": len(set(intentions))}


def main() -> None:
    resultat = entrainer()
    print("Yasmine a été entraînée sur", resultat["phrases"], "phrases.")
    print("Modèle sauvegardé dans yasmine_modele.joblib")


if __name__ == "__main__":
    main()
