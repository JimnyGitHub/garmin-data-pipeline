"""
Tests unitaires couvrant les bugs et points de sécurité identifiés lors de
la revue de code du pipeline Garmin.

Couverture :
  Bug #1  — mount_watch() : OSError sur iterdir() géré sans crash
  Bug #2  — copy_new_files() : sauvegarde incrémentale du state
  Bug #3  — copy_new_files() : déduplication et renommage des collisions
  Bug #5  — safe_value() : types non-sérialisables (bytes, enum, listes, objets)
  Sec #6  — get_base_dir() : chemin hors du home rejeté
  Sec #7  — _check_requirements() : message clair si requirements.txt absent
"""
import enum
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import import_and_summarize_garmin_fit as pipeline


# ===========================================================================
# Bug #5 — safe_value : types non-sérialisables
# ===========================================================================

class TestSafeValue:
    def test_none_passthrough(self):
        assert pipeline.safe_value(None) is None

    def test_bool_passthrough(self):
        assert pipeline.safe_value(True) is True
        assert pipeline.safe_value(False) is False

    def test_int_passthrough(self):
        assert pipeline.safe_value(42) == 42

    def test_float_passthrough(self):
        assert pipeline.safe_value(3.14) == 3.14

    def test_str_passthrough(self):
        assert pipeline.safe_value("running") == "running"

    def test_datetime_to_iso(self):
        dt = datetime(2025, 3, 15, 10, 30, 0)
        assert pipeline.safe_value(dt) == "2025-03-15T10:30:00"

    def test_bytes_to_hex(self):
        assert pipeline.safe_value(b"\xde\xad\xbe\xef") == "deadbeef"

    def test_bytes_empty(self):
        assert pipeline.safe_value(b"") == ""

    def test_enum_to_name(self):
        class Sport(enum.Enum):
            RUNNING = 1
            CYCLING = 2

        assert pipeline.safe_value(Sport.RUNNING) == "RUNNING"
        assert pipeline.safe_value(Sport.CYCLING) == "CYCLING"

    def test_list_recurse(self):
        dt = datetime(2025, 1, 1)
        result = pipeline.safe_value([1, b"\xff", dt, "ok"])
        assert result == [1, "ff", "2025-01-01T00:00:00", "ok"]

    def test_tuple_recurse(self):
        result = pipeline.safe_value((42, b"\x00"))
        assert result == [42, "00"]

    def test_unknown_object_fallback_to_str(self):
        class Weird:
            def __str__(self):
                return "weird_object"

        assert pipeline.safe_value(Weird()) == "weird_object"

    def test_result_is_json_serializable(self):
        """Tout ce que safe_value retourne doit passer dans json.dumps."""
        class CustomObj:
            def __str__(self):
                return "custom"

        values = [
            None, True, 42, 3.14, "hello",
            datetime(2025, 1, 1),
            b"\xca\xfe",
            [1, b"\x00", datetime(2025, 1, 1)],
            CustomObj(),
        ]
        for v in values:
            result = pipeline.safe_value(v)
            # Ne doit pas lever TypeError
            json.dumps(result)


# ===========================================================================
# Bug #1 — mount_watch : OSError sur iterdir() géré
# ===========================================================================

class TestMountWatch:
    def test_oserror_on_iterdir_triggers_mount(self):
        """Si iterdir() lève OSError (point de montage cassé), on tente jmtpfs."""
        mock_dir = MagicMock()
        mock_dir.iterdir.side_effect = OSError("Permission denied")

        with patch.object(pipeline, "MOUNT_DIR", mock_dir), \
             patch.object(pipeline, "run") as mock_run:
            pipeline.mount_watch()

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "jmtpfs"

    def test_empty_dir_triggers_mount(self):
        """Répertoire vide (non monté) → jmtpfs appelé."""
        mock_dir = MagicMock()
        mock_dir.iterdir.return_value = iter([])

        with patch.object(pipeline, "MOUNT_DIR", mock_dir), \
             patch.object(pipeline, "run") as mock_run:
            pipeline.mount_watch()

        mock_run.assert_called_once()

    def test_already_mounted_no_mount_call(self):
        """Répertoire déjà peuplé → jmtpfs non appelé."""
        mock_dir = MagicMock()
        mock_dir.iterdir.return_value = iter([Path("some_file")])

        with patch.object(pipeline, "MOUNT_DIR", mock_dir), \
             patch.object(pipeline, "run") as mock_run:
            pipeline.mount_watch()

        mock_run.assert_not_called()


# ===========================================================================
# Bug #2 — copy_new_files : sauvegarde incrémentale du state
# ===========================================================================

class TestCopyNewFilesIncrementalState:
    def _make_source(self, tmp_path: Path, names: list[str]) -> Path:
        source = tmp_path / "source"
        source.mkdir()
        for name in names:
            (source / name).write_bytes(b"FIT" + name.encode() + b"\x00" * 50)
        return source

    def test_state_saved_after_each_file(self, tmp_path):
        """Après copie de chaque fichier, le state est mis à jour immédiatement."""
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = self._make_source(tmp_path, ["a.fit", "b.fit", "c.fit"])

        save_calls = []
        original_save = pipeline.save_state

        def tracking_save(sf, state):
            save_calls.append(len(state["imported"]))
            original_save(sf, state)

        with patch.object(pipeline, "SOURCE_DIR", source), \
             patch.object(pipeline, "save_state", side_effect=tracking_save):
            pipeline.copy_new_files(incoming, state_file)

        # save_state doit avoir été appelé 3 fois (une fois par fichier)
        assert save_calls == [1, 2, 3]

    def test_state_preserved_after_interruption(self, tmp_path):
        """Si la copie est interrompue au 2e fichier, le 1er est déjà dans le state."""
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = self._make_source(tmp_path, ["first.fit", "second.fit"])

        call_count = 0

        def failing_copy(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Disque plein simulé")
            Path(dst).write_bytes(Path(src).read_bytes())

        with patch.object(pipeline, "SOURCE_DIR", source), \
             patch("shutil.copy2", side_effect=failing_copy):
            with pytest.raises(OSError, match="Disque plein simulé"):
                pipeline.copy_new_files(incoming, state_file)

        # Le 1er fichier doit être sauvegardé dans le state malgré l'interruption
        state = json.loads(state_file.read_text())
        imported_keys = state["imported"]
        assert len(imported_keys) == 1
        assert any("first.fit" in k for k in imported_keys)
        assert not any("second.fit" in k for k in imported_keys)


# ===========================================================================
# Bug #3 — copy_new_files : déduplication et renommage
# ===========================================================================

class TestCopyNewFilesDeduplication:
    def _make_source(self, tmp_path: Path, name: str, content: bytes = b"\x00" * 100) -> Path:
        source = tmp_path / "source"
        source.mkdir(exist_ok=True)
        (source / name).write_bytes(content)
        return source

    def test_already_imported_file_is_skipped(self, tmp_path):
        """Un fichier déjà présent dans le state n'est pas recopié."""
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = self._make_source(tmp_path, "run.fit")

        with patch.object(pipeline, "SOURCE_DIR", source):
            copied1 = pipeline.copy_new_files(incoming, state_file)
            copied2 = pipeline.copy_new_files(incoming, state_file)

        assert len(copied1) == 1
        assert len(copied2) == 0

    def test_collision_in_dest_renamed(self, tmp_path):
        """Si le fichier de destination existe déjà, il est renommé _2, _3, etc."""
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        # Pré-remplir incoming avec un fichier du même nom
        (incoming / "run.fit").write_bytes(b"ancien")

        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = self._make_source(tmp_path, "run.fit", b"\x00" * 200)

        with patch.object(pipeline, "SOURCE_DIR", source):
            copied = pipeline.copy_new_files(incoming, state_file)

        assert len(copied) == 1
        assert copied[0].name == "run_2.fit"
        assert (incoming / "run.fit").exists()   # l'original est intact
        assert (incoming / "run_2.fit").exists()

    def test_multiple_collisions_increment_suffix(self, tmp_path):
        """Si _2 existe aussi, on passe à _3."""
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        (incoming / "run.fit").write_bytes(b"v1")
        (incoming / "run_2.fit").write_bytes(b"v2")

        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = self._make_source(tmp_path, "run.fit", b"\x00" * 300)

        with patch.object(pipeline, "SOURCE_DIR", source):
            copied = pipeline.copy_new_files(incoming, state_file)

        assert copied[0].name == "run_3.fit"

    def test_different_size_same_name_is_imported(self, tmp_path):
        """
        Fichier corrigé (même nom, taille différente) doit pouvoir être importé
        car la clé nom|taille est différente.
        """
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        state_file = tmp_path / "state" / "imported.json"
        state_file.parent.mkdir()
        source = tmp_path / "source"
        source.mkdir()

        # 1er import : fichier de 50 octets
        (source / "activity.fit").write_bytes(b"\x00" * 50)
        with patch.object(pipeline, "SOURCE_DIR", source):
            copied1 = pipeline.copy_new_files(incoming, state_file)
        assert len(copied1) == 1

        # La montre remplace le fichier par une version plus complète (200 octets)
        (source / "activity.fit").write_bytes(b"\x00" * 200)
        with patch.object(pipeline, "SOURCE_DIR", source):
            copied2 = pipeline.copy_new_files(incoming, state_file)

        assert len(copied2) == 1
        assert copied2[0].name == "activity_2.fit"


# ===========================================================================
# Sécurité #6 — get_base_dir : validation du chemin
# ===========================================================================

class TestGetBaseDirSecurity:
    def test_path_outside_home_raises(self, tmp_path, monkeypatch):
        """Un base_dir hors du répertoire personnel doit lever ValueError."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"base_dir": "/etc/garmin_data"}))
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        with pytest.raises(ValueError, match="répertoire personnel"):
            pipeline.get_base_dir()

    def test_path_traversal_attempt_raises(self, tmp_path, monkeypatch):
        """Une tentative de path traversal doit être rejetée."""
        home = Path.home()
        traversal = str(home / "sports" / ".." / ".." / "etc" / "passwd_dir")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"base_dir": traversal}))
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        with pytest.raises(ValueError, match="répertoire personnel"):
            pipeline.get_base_dir()

    def test_valid_home_subpath_accepted(self, tmp_path, monkeypatch):
        """Un chemin dans le home est accepté."""
        valid = str(Path.home() / "Documents" / "sports" / "garmin")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"base_dir": valid}))
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        result = pipeline.get_base_dir()
        assert result == Path(valid).resolve()

    def test_tilde_expansion_within_home(self, tmp_path, monkeypatch):
        """Un chemin avec ~ doit être accepté si dans le home."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"base_dir": "~/Documents/sports"}))
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        result = pipeline.get_base_dir()
        assert result == (Path.home() / "Documents" / "sports").resolve()

    def test_no_config_returns_default(self, tmp_path, monkeypatch):
        """Sans config.json, on retourne DEFAULT_BASE_DIR."""
        monkeypatch.setattr(pipeline, "CONFIG_FILE", tmp_path / "inexistant.json")
        assert pipeline.get_base_dir() == pipeline.DEFAULT_BASE_DIR


# ===========================================================================
# Sécurité #7 — _check_requirements : message clair si fichier absent
# ===========================================================================

class TestCheckRequirements:
    def test_missing_requirements_exits_with_code_1(self, tmp_path, monkeypatch, capsys):
        """requirements.txt absent → sys.exit(1) avec message explicite."""
        monkeypatch.setattr(pipeline, "REQUIREMENTS", tmp_path / "nonexistent.txt")

        with pytest.raises(SystemExit) as exc_info:
            pipeline._check_requirements()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "requirements.txt" in captured.err

    def test_existing_requirements_does_not_exit(self, tmp_path, monkeypatch):
        """requirements.txt présent → pas de sys.exit."""
        req = tmp_path / "requirements.txt"
        req.write_text("fitdecode\n")
        monkeypatch.setattr(pipeline, "REQUIREMENTS", req)

        # Ne doit pas lever SystemExit
        pipeline._check_requirements()


# ===========================================================================
# ensure_base_dir : comportement interactif
# ===========================================================================

class TestEnsureBaseDir:
    def test_existing_dir_no_prompt(self, tmp_path):
        """Si le répertoire existe déjà, aucune interaction utilisateur."""
        with patch("builtins.input") as mock_input:
            pipeline.ensure_base_dir(tmp_path)
        mock_input.assert_not_called()

    def test_confirmed_creates_directory(self, tmp_path, monkeypatch):
        """Répondre 'o' crée le répertoire et met à jour config.json."""
        target = tmp_path / "nouveau"
        config_file = tmp_path / "config.json"
        config_file.write_text("{}")
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        with patch("builtins.input", return_value="o"):
            pipeline.ensure_base_dir(target)

        assert target.exists()
        saved = json.loads(config_file.read_text())
        assert "base_dir" in saved

    def test_refused_exits_without_creating(self, tmp_path):
        """Répondre 'n' appelle sys.exit(0) sans créer le répertoire."""
        target = tmp_path / "nouveau"

        with patch("builtins.input", return_value="n"), \
             pytest.raises(SystemExit) as exc_info:
            pipeline.ensure_base_dir(target)

        assert exc_info.value.code == 0
        assert not target.exists()

    @pytest.mark.parametrize("answer", ["o", "oui", "y", "yes", "O", "OUI"])
    def test_all_positive_answers_create_dir(self, tmp_path, monkeypatch, answer):
        """Toutes les variantes de 'oui' sont acceptées."""
        target = tmp_path / f"dir_{answer}"
        config_file = tmp_path / "config.json"
        config_file.write_text("{}")
        monkeypatch.setattr(pipeline, "CONFIG_FILE", config_file)

        with patch("builtins.input", return_value=answer):
            pipeline.ensure_base_dir(target)

        assert target.exists()
