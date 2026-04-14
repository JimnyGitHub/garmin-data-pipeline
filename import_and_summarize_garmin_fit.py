#!/usr/bin/env python3
"""
Bootstrap : s'assure que le script tourne dans le venv du projet.
Si ce n'est pas le cas, crée le venv, installe les dépendances, puis se
ré-exécute automatiquement avec le bon interpréteur.
"""
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
VENV_DIR = SCRIPT_DIR / ".venv"
REQUIREMENTS = SCRIPT_DIR / "requirements.txt"


def _bootstrap_venv() -> None:
    venv_python = VENV_DIR / "bin" / "python"
    if Path(sys.prefix) == VENV_DIR:
        return  # déjà dans le bon venv

    if not venv_python.exists():
        print("Création du venv...")
        import subprocess
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        print("Installation des dépendances...")
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)],
            check=True,
        )
        print("Dépendances installées.\n")

    os.execv(str(venv_python), [str(venv_python)] + sys.argv)


_bootstrap_venv()

# À partir d'ici on est forcément dans le venv avec fitdecode disponible.
import json
import shutil
import subprocess
from datetime import datetime
from typing import Any

import fitdecode


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_FILE = SCRIPT_DIR / "config.json"
DEFAULT_BASE_DIR = Path.home() / "Documents" / "sports" / "donneesGarmin"

MOUNT_DIR = Path.home() / "garmin-mtp"
SOURCE_DIR = MOUNT_DIR / "Internal Storage" / "GARMIN" / "Activity"


def load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(config: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def get_base_dir() -> Path:
    config = load_config()
    raw = config.get("base_dir")
    return Path(raw).expanduser() if raw else DEFAULT_BASE_DIR


def ensure_base_dir(base_dir: Path) -> None:
    if base_dir.exists():
        return

    print(f"\nLe répertoire de données n'existe pas : {base_dir}")
    response = input("Voulez-vous le créer ? [o/N] ").strip().lower()
    if response in ("o", "oui", "y", "yes"):
        base_dir.mkdir(parents=True, exist_ok=True)
        config = load_config()
        config["base_dir"] = str(base_dir)
        save_config(config)
        print(f"Répertoire créé : {base_dir}\n")
    else:
        print("Abandon.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Commande échouée: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def ensure_dirs(base_dir: Path) -> tuple[Path, Path, Path]:
    incoming_dir = base_dir / "incoming_fit"
    summary_dir = base_dir / "summaries"
    state_file = base_dir / "state" / "imported_files.json"

    MOUNT_DIR.mkdir(parents=True, exist_ok=True)
    incoming_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    return incoming_dir, summary_dir, state_file


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"imported": []}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"imported": []}


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def mount_watch() -> None:
    if not any(MOUNT_DIR.iterdir()):
        run(["jmtpfs", str(MOUNT_DIR)])


def unmount_watch() -> None:
    subprocess.run(["fusermount", "-u", str(MOUNT_DIR)], text=True, capture_output=True)


def copy_new_files(incoming_dir: Path, state_file: Path) -> list[Path]:
    state = load_state(state_file)
    imported = set(state.get("imported", []))
    copied: list[Path] = []

    if not SOURCE_DIR.exists():
        raise RuntimeError(f"Dossier source introuvable: {SOURCE_DIR}")

    fit_files = sorted(SOURCE_DIR.glob("*.fit")) + sorted(SOURCE_DIR.glob("*.FIT"))

    for fit_file in fit_files:
        key = f"{fit_file.name}|{fit_file.stat().st_size}"
        if key in imported:
            continue

        dest_file = incoming_dir / fit_file.name
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            i = 2
            while (incoming_dir / f"{stem}_{i}{suffix}").exists():
                i += 1
            dest_file = incoming_dir / f"{stem}_{i}{suffix}"

        shutil.copy2(fit_file, dest_file)
        imported.add(key)
        copied.append(dest_file)

    state["imported"] = sorted(imported)
    save_state(state_file, state)
    return copied


# ---------------------------------------------------------------------------
# FIT parsing
# ---------------------------------------------------------------------------

def safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def fit_record_to_dict(frame: fitdecode.records.FitDataMessage) -> dict[str, Any]:
    return {field.name: safe_value(field.value) for field in frame.fields}


def first_non_null(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def summarize_fit_file(fit_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "file_name": fit_path.name,
        "file_path": str(fit_path),
        "sport": None,
        "sub_sport": None,
        "session_start_time": None,
        "total_timer_time_s": None,
        "total_elapsed_time_s": None,
        "total_distance_m": None,
        "total_distance_km": None,
        "avg_speed_m_s": None,
        "avg_speed_km_h": None,
        "max_speed_m_s": None,
        "max_speed_km_h": None,
        "avg_heart_rate_bpm": None,
        "max_heart_rate_bpm": None,
        "avg_cadence": None,
        "max_cadence": None,
        "total_ascent_m": None,
        "total_descent_m": None,
        "record_count": 0,
        "has_gps": False,
        "gps_points_preview": [],
    }

    session_message: dict[str, Any] | None = None
    activity_message: dict[str, Any] | None = None
    records_preview: list[dict[str, Any]] = []

    with fitdecode.FitReader(str(fit_path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            if frame.name == "session":
                session_message = fit_record_to_dict(frame)
            elif frame.name == "activity":
                activity_message = fit_record_to_dict(frame)
            elif frame.name == "record":
                summary["record_count"] += 1
                record_data = fit_record_to_dict(frame)

                lat = record_data.get("position_lat")
                lon = record_data.get("position_long")
                if lat is not None and lon is not None:
                    summary["has_gps"] = True

                if len(records_preview) < 5:
                    records_preview.append({
                        "timestamp": record_data.get("timestamp"),
                        "position_lat": lat,
                        "position_long": lon,
                        "altitude": record_data.get("altitude"),
                        "distance": record_data.get("distance"),
                        "heart_rate": record_data.get("heart_rate"),
                        "cadence": record_data.get("cadence"),
                        "speed": record_data.get("speed"),
                    })

    summary["gps_points_preview"] = records_preview

    if session_message:
        summary["sport"] = session_message.get("sport")
        summary["sub_sport"] = session_message.get("sub_sport")
        summary["session_start_time"] = first_non_null(
            session_message.get("start_time"),
            session_message.get("timestamp"),
        )
        summary["total_timer_time_s"] = session_message.get("total_timer_time")
        summary["total_elapsed_time_s"] = session_message.get("total_elapsed_time")
        summary["total_distance_m"] = session_message.get("total_distance")
        summary["avg_speed_m_s"] = session_message.get("avg_speed")
        summary["max_speed_m_s"] = session_message.get("max_speed")
        summary["avg_heart_rate_bpm"] = session_message.get("avg_heart_rate")
        summary["max_heart_rate_bpm"] = session_message.get("max_heart_rate")
        summary["avg_cadence"] = session_message.get("avg_cadence")
        summary["max_cadence"] = session_message.get("max_cadence")
        summary["total_ascent_m"] = session_message.get("total_ascent")
        summary["total_descent_m"] = session_message.get("total_descent")

    if activity_message and not summary["session_start_time"]:
        summary["session_start_time"] = activity_message.get("timestamp")

    if summary["total_distance_m"] is not None:
        summary["total_distance_km"] = round(summary["total_distance_m"] / 1000, 3)
    if summary["avg_speed_m_s"] is not None:
        summary["avg_speed_km_h"] = round(summary["avg_speed_m_s"] * 3.6, 2)
    if summary["max_speed_m_s"] is not None:
        summary["max_speed_km_h"] = round(summary["max_speed_m_s"] * 3.6, 2)

    return summary


def write_summary_files(fit_path: Path, summary: dict[str, Any], summary_dir: Path) -> None:
    base_name = fit_path.stem
    json_path = summary_dir / f"{base_name}.summary.json"
    txt_path = summary_dir / f"{base_name}.summary.txt"

    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    txt_lines = [
        f"Fichier            : {summary['file_name']}",
        f"Sport              : {summary['sport']}",
        f"Sous-sport         : {summary['sub_sport']}",
        f"Début              : {summary['session_start_time']}",
        f"Distance (km)      : {summary['total_distance_km']}",
        f"Temps chrono (s)   : {summary['total_timer_time_s']}",
        f"Temps écoulé (s)   : {summary['total_elapsed_time_s']}",
        f"FC moyenne         : {summary['avg_heart_rate_bpm']}",
        f"FC max             : {summary['max_heart_rate_bpm']}",
        f"Cadence moyenne    : {summary['avg_cadence']}",
        f"Cadence max        : {summary['max_cadence']}",
        f"Vitesse moy km/h   : {summary['avg_speed_km_h']}",
        f"Vitesse max km/h   : {summary['max_speed_km_h']}",
        f"D+ (m)             : {summary['total_ascent_m']}",
        f"D- (m)             : {summary['total_descent_m']}",
        f"Nb records         : {summary['record_count']}",
        f"GPS présent        : {summary['has_gps']}",
        "",
        "Aperçu de quelques points :",
    ]
    for point in summary["gps_points_preview"]:
        txt_lines.append(
            f"- ts={point.get('timestamp')} dist={point.get('distance')} "
            f"hr={point.get('heart_rate')} cad={point.get('cadence')} "
            f"alt={point.get('altitude')} lat={point.get('position_lat')} "
            f"lon={point.get('position_long')} speed={point.get('speed')}"
        )

    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    base_dir = get_base_dir()
    ensure_base_dir(base_dir)

    incoming_dir, summary_dir, state_file = ensure_dirs(base_dir)

    try:
        print("Montage de la montre...")
        mount_watch()

        print(f"Lecture de : {SOURCE_DIR}")
        copied = copy_new_files(incoming_dir, state_file)

        if not copied:
            print("Aucun nouveau fichier .fit à copier.")
            return

        print("Nouveaux fichiers copiés :")
        for fit_file in copied:
            print(f"  - {fit_file}")

        print("\nGénération des résumés...")
        for fit_file in copied:
            try:
                summary = summarize_fit_file(fit_file)
                write_summary_files(fit_file, summary, summary_dir)
                print(f"  Résumé généré : {fit_file.name}")
            except Exception as exc:
                print(f"  Erreur sur {fit_file.name}: {exc}")

    finally:
        print("Démontage...")
        unmount_watch()


if __name__ == "__main__":
    main()
