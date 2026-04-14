import os

# Désactive le bootstrap venv avant tout import du module principal,
# afin que les tests puissent importer le module sans déclencher os.execv.
os.environ["GARMIN_SKIP_BOOTSTRAP"] = "1"
