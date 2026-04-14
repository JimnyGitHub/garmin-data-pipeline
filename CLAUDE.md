# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Lancer le script

Le script se lance directement avec l'interpréteur système — il gère lui-même le venv :

```bash
python3 import_and_summarize_garmin_fit.py
```

Au premier lancement, le script crée automatiquement `.venv/`, installe les dépendances depuis `requirements.txt`, puis se ré-exécute dans le venv via `os.execv`. Les lancements suivants sont directs.

## Architecture

Le script `import_and_summarize_garmin_fit.py` est le seul fichier actif. Il fait trois choses en séquence :

1. **Bootstrap venv** — bloc en tête de fichier, avant tout import tiers. Vérifie `sys.prefix == VENV_DIR`, sinon crée le venv et se ré-exécute.
2. **Import MTP** — monte la montre via `jmtpfs`, copie les fichiers `.fit` nouveaux dans `incoming_fit/`, met à jour `state/imported_files.json` (clé `nom|taille` pour la déduplication).
3. **Résumé FIT** — parse chaque fichier copié avec `fitdecode`, extrait le message `session` (métriques agrégées) et les premiers enregistrements `record` (aperçu GPS), écrit un `.summary.json` et un `.summary.txt` dans `summaries/`.

## Configuration

`config.json` (racine du dépôt) contrôle le répertoire de données :

```json
{ "base_dir": "/home/seb/Documents/sports/donneesGarmin" }
```

Sous `base_dir` sont créés automatiquement : `incoming_fit/`, `summaries/`, `state/`.

Si `base_dir` n'existe pas au lancement, le script demande confirmation avant de le créer.

## Dépendances système requises

- `jmtpfs` — pour monter la montre Garmin en MTP (`fusermount` pour le démontage)
- Python 3.12+


