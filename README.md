# garmin-data-pipeline

Pipeline d'extraction et d'analyse des activités sportives depuis une montre Garmin.

## À quoi ça sert

Ce projet permet de récupérer automatiquement les fichiers d'activité (`.fit`) stockés sur une montre Garmin connectée en USB, de les archiver localement, et d'en extraire un résumé lisible par un humain ou exploitable par un programme.

Le format `.fit` (Flexible and Interoperable Data Transfer) est le format binaire propriétaire utilisé par Garmin pour enregistrer toutes les données captées pendant une activité : GPS, fréquence cardiaque, cadence, vitesse, altitude, etc. Ce pipeline le rend accessible sans passer par Garmin Connect.

## Prérequis

**Dépendances système :**

- `jmtpfs` — pour monter la montre en MTP (protocole USB utilisé par les montres Garmin)
- `fusermount` — inclus avec `fuse`, pour démonter proprement après l'import
- Python 3.12+

Installation sur Ubuntu/Debian :

```bash
sudo apt install jmtpfs fuse
```

**Préparer le point de montage :**

```bash
mkdir -p ~/garmin-mtp
```

## Utilisation

Brancher la montre en USB, puis lancer :

```bash
python3 import_and_summarize_garmin_fit.py
```

C'est tout. Le script gère automatiquement :

1. La création du venv et l'installation des dépendances Python au premier lancement
2. Le montage de la montre via MTP
3. La copie des nouveaux fichiers `.fit` uniquement (les fichiers déjà importés sont ignorés)
4. La génération d'un résumé par activité
5. Le démontage de la montre

### Premier lancement

Si le répertoire de données configuré n'existe pas encore, le script demande confirmation avant de le créer :

```
Le répertoire de données n'existe pas : ~/Documents/sports/donneesGarmin
Voulez-vous le créer ? [o/N]
```

## Configuration

Le fichier `config.json` (à la racine du projet) contrôle où sont stockées les données :

```json
{
  "base_dir": "~/Documents/sports/donneesGarmin"
}
```

Il suffit de modifier `base_dir` pour changer l'emplacement. Les chemins avec `~` sont supportés.

## Structure des données produites

Sous `base_dir`, trois répertoires sont créés automatiquement :

```
donneesGarmin/
├── incoming_fit/          # Fichiers .fit bruts copiés depuis la montre
├── summaries/             # Résumés extraits (un .json + un .txt par activité)
└── state/
    └── imported_files.json  # Liste des fichiers déjà importés (évite les doublons)
```

### Les fichiers de résumé

Pour chaque activité importée, deux fichiers sont générés dans `summaries/` :

**`<activite>.summary.json`** — données structurées exploitables par un script ou un outil d'analyse :

```json
{
  "file_name": "2025-03-15-10-30-00.fit",
  "sport": "running",
  "sub_sport": "generic",
  "session_start_time": "2025-03-15T10:30:00",
  "total_distance_km": 10.234,
  "total_timer_time_s": 3245.0,
  "avg_speed_km_h": 11.35,
  "max_speed_km_h": 14.2,
  "avg_heart_rate_bpm": 158,
  "max_heart_rate_bpm": 178,
  "avg_cadence": 82,
  "total_ascent_m": 145,
  "total_descent_m": 143,
  "record_count": 3245,
  "has_gps": true,
  "gps_points_preview": [...]
}
```

**`<activite>.summary.txt`** — version lisible directement dans un terminal ou un éditeur de texte.

### Ce que contiennent les données extraites

| Champ | Description |
|---|---|
| `sport` / `sub_sport` | Type d'activité enregistré par la montre (running, cycling, swimming…) |
| `session_start_time` | Heure de début de l'activité |
| `total_distance_km` | Distance totale parcourue |
| `total_timer_time_s` | Temps chronomètre (pauses exclues) |
| `total_elapsed_time_s` | Temps total écoulé (pauses incluses) |
| `avg_speed_km_h` / `max_speed_km_h` | Vitesse moyenne et maximale |
| `avg_heart_rate_bpm` / `max_heart_rate_bpm` | Fréquence cardiaque moyenne et maximale |
| `avg_cadence` / `max_cadence` | Cadence (pas/min pour la course, tours/min pour le vélo) |
| `total_ascent_m` / `total_descent_m` | Dénivelé positif et négatif |
| `record_count` | Nombre de points de mesure enregistrés |
| `has_gps` | Indique si l'activité contient des coordonnées GPS |
| `gps_points_preview` | Aperçu des 5 premiers points GPS avec timestamp, coordonnées, altitude, FC |

### Utiliser les données extraites

Les fichiers `.summary.json` peuvent être chargés directement en Python, exploités avec `jq`, ou importés dans un tableur. Exemple :

```bash
# Lister toutes les activités avec leur distance
jq -r '[.session_start_time, .sport, (.total_distance_km | tostring) + " km"] | join(" | ")' \
  ~/Documents/sports/donneesGarmin/summaries/*.summary.json
```

Les fichiers `.fit` bruts dans `incoming_fit/` sont conservés intacts pour un éventuel retraitement ou import dans d'autres outils (Strava, TrainingPeaks, etc.).
