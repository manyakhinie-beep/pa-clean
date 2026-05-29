"""
Unit tests for ``services.lora_trainer`` — обёртка над mlx_lm.lora.

Не запускает реальное обучение (это часы и нужен MLX-runtime).  Покрывает:
  * Конфиг → CLI-аргументы — соответствие mlx_lm.lora ожидаемому формату
  * Статус-функция корректно читает наличие adapter-файлов
  * Очистка адаптера удаляет директорию
  * write_adapter_metadata пишет training_metadata.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_assistant.services import lora_trainer as tr


# ----------------------------------------------------------------------
# LoraConfig → CLI args
# ----------------------------------------------------------------------


class TestConfigCliArgs:
    def _cfg(self, tmp_path: Path) -> tr.LoraConfig:
        return tr.LoraConfig(
            model="/path/to/model",
            data_dir=tmp_path / "data",
            adapter_path=tmp_path / "adapter",
        )

    def test_includes_train_flag(self, tmp_path: Path):
        args = self._cfg(tmp_path).as_cli_args()
        assert "--train" in args

    def test_includes_model_and_data_dir(self, tmp_path: Path):
        args = self._cfg(tmp_path).as_cli_args()
        assert "--model" in args
        assert "/path/to/model" in args
        assert "--data" in args

    def test_passes_lora_layers_as_num_layers(self, tmp_path: Path):
        """mlx_lm.lora 0.30+ переименовал --lora-layers в --num-layers."""
        cfg = tr.LoraConfig(
            model="m", data_dir=tmp_path / "d", adapter_path=tmp_path / "a",
            lora_layers=12,
        )
        args = cfg.as_cli_args()
        assert "--num-layers" in args
        idx = args.index("--num-layers")
        assert args[idx + 1] == "12"

    def test_passes_adapter_path_for_output(self, tmp_path: Path):
        cfg = self._cfg(tmp_path)
        args = cfg.as_cli_args()
        assert "--adapter-path" in args
        idx = args.index("--adapter-path")
        assert args[idx + 1] == str(cfg.adapter_path)

    def test_passes_iters_batch_size_lr(self, tmp_path: Path):
        cfg = tr.LoraConfig(
            model="m", data_dir=tmp_path / "d", adapter_path=tmp_path / "a",
            iters=1000, batch_size=2, learning_rate=5e-5,
        )
        args = cfg.as_cli_args()
        assert "1000" in args
        assert "2" in args
        # learning rate в формате %g, чтобы не было лишних нулей
        assert any("5e-05" in a or "5e-5" in a or "0.00005" in a for a in args)

    def test_includes_seed_for_reproducibility(self, tmp_path: Path):
        args = self._cfg(tmp_path).as_cli_args()
        assert "--seed" in args


# ----------------------------------------------------------------------
# adapter_status
# ----------------------------------------------------------------------


class TestAdapterStatus:
    def test_missing_path_reports_not_exists(self, tmp_path: Path):
        info = tr.adapter_status(tmp_path / "nope")
        assert info["exists"] is False

    def test_existing_path_reports_files(self, tmp_path: Path):
        ap = tmp_path / "adapter"
        ap.mkdir()
        (ap / "adapters.safetensors").write_bytes(b"\x00" * 10_000)
        (ap / "adapter_config.json").write_text("{}", encoding="utf-8")
        info = tr.adapter_status(ap)
        assert info["exists"] is True
        assert info["weights_present"] is True
        assert info["config_present"] is True
        assert info["size_mb"] >= 0  # 10 KB ≈ 0.01 MB → округляется до 0

    def test_loads_training_metadata_if_present(self, tmp_path: Path):
        ap = tmp_path / "adapter"
        ap.mkdir()
        meta = {"config": {"iters": 600}, "dataset_manifest": {"total_pairs": 42}}
        (ap / "training_metadata.json").write_text(
            json.dumps(meta), encoding="utf-8",
        )
        info = tr.adapter_status(ap)
        assert info["metadata"] == meta


# ----------------------------------------------------------------------
# clear_adapter
# ----------------------------------------------------------------------


class TestClearAdapter:
    def test_removes_directory(self, tmp_path: Path):
        ap = tmp_path / "a"
        ap.mkdir()
        (ap / "stuff.bin").write_bytes(b"x")
        assert tr.clear_adapter(ap) is True
        assert not ap.exists()

    def test_returns_false_when_missing(self, tmp_path: Path):
        assert tr.clear_adapter(tmp_path / "nope") is False


# ----------------------------------------------------------------------
# write_adapter_metadata
# ----------------------------------------------------------------------


class TestWriteMetadata:
    def test_writes_training_metadata_json(self, tmp_path: Path):
        ap = tmp_path / "a"
        ap.mkdir()
        cfg = tr.LoraConfig(model="m", data_dir=tmp_path / "d", adapter_path=ap)
        tr.write_adapter_metadata(ap, cfg, manifest={"total_pairs": 100})
        out = ap / "training_metadata.json"
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["config"]["model"] == "m"
        assert loaded["config"]["iters"] == 600  # default
        assert loaded["dataset_manifest"]["total_pairs"] == 100


# ----------------------------------------------------------------------
# CLI command registration — pa lora <subcmd>
# ----------------------------------------------------------------------


def test_lora_cli_commands_registered():
    # cli.py импортирует `rich` для красивых таблиц — в некоторых
    # ограниченных окружениях (минимальные CI runners) его нет.
    # Если так — проверяем что lora-команды декларированы в файле
    # хотя бы статически.
    try:
        from personal_assistant.cli import main  # type: ignore[attr-defined]
    except ModuleNotFoundError as exc:
        if "rich" not in str(exc).lower():
            raise
        # Fallback на статическую проверку источника
        from pathlib import Path
        src = (
            Path(__file__).resolve().parents[3]
            / "src" / "personal_assistant" / "cli.py"
        ).read_text(encoding="utf-8")
        assert '@main.group("lora")' in src
        for sub in ("prepare", "train", "status", "clear"):
            assert f'@lora_group.command("{sub}")' in src, (
                f"pa lora {sub} missing in cli.py"
            )
        return

    sub = main.commands.get("lora")
    assert sub is not None, "pa lora group not registered"
    expected = {"prepare", "train", "status", "clear"}
    assert expected <= set(sub.commands.keys()), (
        f"missing subcommands: {expected - set(sub.commands.keys())}"
    )
