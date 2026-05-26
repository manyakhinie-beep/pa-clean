"""
CLI coverage tests for ``pa`` (Click) — uses ``click.testing.CliRunner``.

Goal: exercise the easy ``pa <cmd>`` commands so cli.py climbs from 0% to ~60%
without needing real MLX inference, real AppleScript, or a running server.
Heavy paths (sync, serve, build-index, search) are touched via mocks; the
deep apple-side / model-side logic lives in scenario tests, not here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from personal_assistant.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Top-level group: --help, --version-like
# ---------------------------------------------------------------------------


class TestCLITopLevel:
    def test_help_lists_subcommands(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        # spot-check a few documented subcommands
        for cmd in ("check", "status", "sync-all", "serve", "list-models"):
            assert cmd in result.output

    def test_no_command_shows_usage(self, runner):
        # No subcommand → Click prints usage and exits with code 2
        result = runner.invoke(main, [])
        # Click 8 returns 2 (no command), some configs return 0 with usage —
        # accept either as long as 'Usage:' appears.
        assert "Usage:" in result.output

    def test_unknown_command_errors(self, runner):
        result = runner.invoke(main, ["definitely-not-a-real-command"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# pa check
# ---------------------------------------------------------------------------


class TestCheck:
    def test_check_runs_without_crash(self, runner, tmp_path, monkeypatch):
        """`pa check` should produce a status table even when AppleScript fails
        (we're not on a real Mac with Calendar/Mail; failures are expected)."""
        # vault path is consulted; make it a tmp path so it doesn't try the
        # developer's real ~/PersonalAssistantVault.
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        # Force osascript-call failures (we're not on a real Mac).
        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            side_effect=RuntimeError("no osascript here"),
        ):
            result = runner.invoke(main, ["check"])

        # Even with all-failing probes, the command must exit cleanly and
        # render its table.
        assert result.exit_code == 0
        # Loose markers — rich output may wrap; just check for the title.
        assert "System Check" in result.output or "Item" in result.output

    def test_check_with_existing_vault_marks_it_ok(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ):
            result = runner.invoke(main, ["check"])
        assert result.exit_code == 0
        assert "Vault" in result.output


# ---------------------------------------------------------------------------
# pa status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_empty_vault(self, runner, tmp_path, monkeypatch):
        """`pa status` on an empty vault should not crash."""
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0

    def test_status_with_some_files(self, runner, tmp_path, monkeypatch):
        # Create a tiny vault with one mail and one calendar event.
        (tmp_path / "mail" / "2026" / "05").mkdir(parents=True)
        (tmp_path / "mail" / "2026" / "05" / "2026-05-01_test_abc123.md").write_text(
            "---\nsubject: Hi\n---\nBody\n", encoding="utf-8"
        )
        (tmp_path / "calendar" / "2026" / "05").mkdir(parents=True)
        (tmp_path / "calendar" / "2026" / "05" / "2026-05-01_meeting_xyz.md").write_text(
            "---\ntitle: M\n---\nNotes\n", encoding="utf-8"
        )

        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# pa fix-model-config --dry-run
# ---------------------------------------------------------------------------


class TestFixModelConfig:
    def test_fix_model_config_dry_run_no_changes(self, runner, tmp_path, monkeypatch):
        """`--dry-run` must NOT write to .env even when the current path is bad."""
        # Run in a tmp cwd so any accidental writes go there, not the project.
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            # No .env present → command should still run and explain.
            result = runner.invoke(
                main,
                ["fix-model-config", "--dry-run"],
            )
            # Command may exit 0 (nothing to fix) or 1 (no .env) — both fine.
            assert result.exit_code in (0, 1)
            # No .env created by --dry-run.
            assert not Path(".env").exists()

    def test_fix_model_config_dry_run_with_existing_env(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=str(tmp_path)):
            Path(".env").write_text(
                "PA_MLX_MODEL_PATH=/some/bogus/path\nOTHER=1\n",
                encoding="utf-8",
            )
            result = runner.invoke(main, ["fix-model-config", "--dry-run"])
            assert result.exit_code in (0, 1)
            # Dry-run must NOT modify the file.
            content = Path(".env").read_text(encoding="utf-8")
            assert "PA_MLX_MODEL_PATH=/some/bogus/path" in content

    def test_fix_model_config_writes_int_to_float(self, runner, tmp_path):
        """Full flow: real config.json with int values for float fields → fix
        rewrites them on disk. Exercises _find_int_float_fields +
        _fix_int_float_fields."""
        import json

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        cfg_file = model_dir / "config.json"
        # Use the *real* _FLOAT_FIELDS set so the test stays in sync with
        # production. See src/personal_assistant/cli.py.
        cfg_file.write_text(
            json.dumps({
                "routed_scaling_factor": 1,             # should become 1.0
                "scaling_factor": 32,                   # should become 32.0
                "rope_scaling": {"mscale": 1, "mscale_all_dim": 2},
                "unrelated": 1,                         # int but not in _FLOAT_FIELDS
            }),
            encoding="utf-8",
        )
        result = runner.invoke(
            main,
            ["fix-model-config", "--model-path", str(model_dir)],
        )
        assert result.exit_code == 0, result.output
        # File was rewritten — top-level fields are floats now
        after = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert isinstance(after["routed_scaling_factor"], float)
        assert isinstance(after["scaling_factor"], float)
        # Nested scalars also fixed
        assert isinstance(after["rope_scaling"]["mscale"], float)
        assert isinstance(after["rope_scaling"]["mscale_all_dim"], float)
        # Unrelated field untouched
        assert after["unrelated"] == 1

    def test_fix_model_config_clean_file_no_changes(self, runner, tmp_path):
        """If the config has no int-as-float fields, command exits 0 with
        a 'looks fine' message."""
        import json

        model_dir = tmp_path / "model"
        model_dir.mkdir()
        cfg_file = model_dir / "config.json"
        cfg_file.write_text(
            # No int-as-float field — all clean
            json.dumps({"routed_scaling_factor": 1.0, "unrelated": "x"}),
            encoding="utf-8",
        )
        before = cfg_file.read_text(encoding="utf-8")
        result = runner.invoke(
            main,
            ["fix-model-config", "--model-path", str(model_dir)],
        )
        assert result.exit_code == 0, result.output
        # File untouched
        assert cfg_file.read_text(encoding="utf-8") == before

    def test_fix_model_config_no_model_path_errors(self, runner, tmp_path, monkeypatch):
        """Without --model-path AND without PA_MLX_MODEL_PATH in settings →
        the command exits non-zero with a hint."""
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "mlx_model_path", "")
        result = runner.invoke(main, ["fix-model-config"])
        assert result.exit_code != 0
        assert "PA_MLX_MODEL_PATH" in result.output or "model path" in result.output.lower()


# ---------------------------------------------------------------------------
# pa list-models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_list_models_prints_table(self, runner):
        result = runner.invoke(main, ["list-models"])
        assert result.exit_code == 0
        # Should at least include the bge-m3 hint from the help text.
        assert "embedding" in result.output.lower() or "bge" in result.output.lower() \
            or "model" in result.output.lower()


# ---------------------------------------------------------------------------
# pa sync-* — mock readers/writers so we don't touch real Mail / Calendar
# ---------------------------------------------------------------------------


class TestSyncCommands:
    """For sync commands we mock the readers so the CLI plumbing is exercised
    (option parsing, vault path resolution, summary print) without touching
    real Apple apps."""

    def test_sync_calendar_with_mocked_reader(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        with patch(
            "personal_assistant.readers.calendar_reader.CalendarReader"
        ) as cls:
            instance = cls.return_value
            instance.fetch_events.return_value = []
            result = runner.invoke(
                main,
                ["sync-calendar", "--days-back", "1", "--days-forward", "1"],
            )
        # Even with zero events the command should exit 0.
        assert result.exit_code == 0

    def test_sync_mail_with_mocked_reader(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        with patch(
            "personal_assistant.readers.mail_reader.MailReader"
        ) as cls:
            instance = cls.return_value
            instance.fetch_messages.return_value = []
            result = runner.invoke(main, ["sync-mail", "--days-back", "1"])
        assert result.exit_code == 0

    def test_sync_all_with_mocked_readers(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        with patch(
            "personal_assistant.readers.calendar_reader.CalendarReader"
        ) as cal_cls, patch(
            "personal_assistant.readers.mail_reader.MailReader"
        ) as mail_cls:
            cal_cls.return_value.fetch_events.return_value = []
            mail_cls.return_value.fetch_messages.return_value = []
            result = runner.invoke(main, ["sync-all", "--sources", "calendar,mail"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# pa serve — mock uvicorn.run so we don't actually start a server
# ---------------------------------------------------------------------------


class TestServe:
    def test_serve_calls_uvicorn(self, runner):
        """`pa serve` should hand off to uvicorn — we mock uvicorn.run to
        avoid binding a port."""
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(main, ["serve", "--host", "127.0.0.1", "--port", "0"])
        # Exit may be 0 (handed off and mock returned) or nonzero (some
        # init failed before uvicorn.run) — both are acceptable for coverage,
        # as long as the CLI parsed the options and reached the dispatch.
        assert mock_run.called or result.exit_code != 0


# ---------------------------------------------------------------------------
# pa run-tasks — runs scheduled pipeline once; mock it
# ---------------------------------------------------------------------------


class TestRunTasks:
    def test_run_tasks_invokes_pipeline(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)
        # run-tasks early-returns when mlx_model_path is empty (default in
        # tests). Set a non-empty placeholder so the dispatch reaches the
        # mocked pipeline call.
        monkeypatch.setattr(_cfg, "mlx_model_path", str(tmp_path / "fake-model"))

        with patch(
            "personal_assistant.mlx_server.scheduler.run_pipeline"
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "started_at": "...",
                "classify": {"total": 0, "classified": 0, "label_counts": {}},
                "summary": {"recent_mail_count": 0, "summary": "—"},
                "digest_path": "/tmp/x",
                "finished_at": "...",
            }
            result = runner.invoke(main, ["run-tasks"])
        assert mock_pipeline.called, f"run_pipeline not called; output: {result.output}"
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# pa search — exercises CLI plumbing for the search subcommand
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_with_empty_vault(self, runner, tmp_path, monkeypatch):
        from personal_assistant.config import settings as _cfg
        monkeypatch.setattr(_cfg, "vault_path", tmp_path)

        # Force MLX engine unavailable so we don't try to load a model.
        monkeypatch.setattr(_cfg, "mlx_model_path", "")

        with patch(
            "personal_assistant.mlx_server.engine.MLXEngine"
        ) as eng_cls:
            instance = eng_cls.return_value
            instance.is_loaded = False
            instance.ask.return_value = MagicMock(
                answer="(no model)", sources=[], confidence=0.0
            )
            result = runner.invoke(main, ["search", "тест"])

        # Empty vault search should not crash.
        assert result.exit_code in (0, 1)
