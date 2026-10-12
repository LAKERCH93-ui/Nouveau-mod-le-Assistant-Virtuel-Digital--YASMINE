# Yasmine

Yasmine est un assistant personnel local : chat vocal, mémoire, rappels, apprentissage de commandes et, en option, connexion à un modèle de langage local pour les demandes complexes.

## Vision Agent OS

L'onglet **Agent** est le cockpit de Yasmine : il montre son cerveau (Ollama), sa mémoire locale, ses compétences et les connexions à préparer. Les compétences peuvent être activées ou mises en pause. Les actions sur le PC restent limitées à une liste blanche et demandent une confirmation explicite.

Les connexions Gmail, Google Calendar, Telegram/WhatsApp et autres services ne sont jamais automatiques : elles nécessitent une autorisation distincte avant toute lecture ou écriture. Aujourd'hui, la mémoire, les rappels, l'apprentissage, la voix, Ollama et les actions locales sont les briques réellement disponibles.

## Démarrer

Prérequis : Python 3.11 ou plus récent. Lors de son installation sous Windows, cochez **Add Python to PATH**. Puis, depuis ce dossier :

```powershell
py -m pip install -r requirements.txt
py yasmine_train.py
py yasmine_server.py
```

Ouvrez ensuite `http://127.0.0.1:8765` dans un navigateur récent et autorisez le microphone lorsque le navigateur le demande. Le premier lancement demande de créer un mot de passe local. Après une heure sans interaction, Yasmine se verrouille et le redemande.

La mémoire est créée dans `yasmine_memoire.json`, uniquement sur votre machine. Le serveur écoute seulement sur `127.0.0.1`. Pour utiliser l'ancien chat terminal à voix haute (facultatif) :

```powershell
py -m pip install pyttsx3
py yasmine_chat.py --voix
```

## Exemples

- `Yasmine, bonjour`
- `je m'appelle Alex`
- `note appeler Léa demain`
- `mes notes` puis `supprime note 1`
- `calcule (12 + 8) / 2`
- `minuteur 5 minutes`
- `rappelle-moi dans 30 minutes de sortir le pain`
- `mes rappels`
- `quelle heure est-il ?`
- `briefing`
- `statut`
- `ouvre calculatrice` (ou Google, YouTube, GitHub)

Les ouvertures sont limitées à cette liste blanche ; Yasmine n'exécute jamais du texte comme une commande système.

## Apprendre depuis l'interface

L'onglet **Apprendre** affiche toutes les phrases de `intentions.json`, classées par intention. Ajoutez-y une nouvelle intention, une phrase et une réponse : l'interface met à jour ce fichier et réentraîne immédiatement le classifieur.

Cet apprentissage sert à reconnaître vos commandes personnelles. Il ne transforme pas le petit classifieur en modèle généraliste. Pour des raisonnements en maths, informatique, analyse ou conversation ouverte, activez dans l'onglet **Intelligence** un modèle Ollama déjà installé sur votre PC et indiquez son nom. Aucun modèle ni donnée ne quitte votre ordinateur.

## Limites vocales

La reconnaissance de parole transcrit vos mots et peut répondre dès la fin de votre phrase. Ce n'est pas une reconnaissance biométrique de locuteur : le mot de passe reste l'authentification d'accès. Une véritable identification vocale demanderait un modèle spécialisé, l'enrôlement de votre voix et un choix de confidentialité explicite.

## Vérifier

```powershell
py -m unittest -v
```
