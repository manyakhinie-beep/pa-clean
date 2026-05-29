"""
lora_trainer.py — subprocess-обёртка над ``mlx_lm.lora`` для запуска
fine-tune-а из CLI / WebUI.

Зачем тонкая обёртка, а не прямой вызов API:
  * mlx_lm.lora — это **CLI tool**, его официальный путь — запуск как
    подпроцесс.  Прямой импорт ``from mlx_lm.lora import ...`` ломается
    при минорных обновлениях библиотеки.
  * subprocess изолирует MLX-инициализацию от FastAPI-процесса —
    нам не надо париться про двойную загрузку модели в память.
  * Прогресс и ошибки видны построчно (stream stdout/stderr) — можно
    показывать пилотам прогресс в WebUI «epoch 3/10, loss=0.41».

Что НЕ делает этот модуль:
  * Не управляет жизненным циклом адаптера в MLX-engine — это работа
    ``engine.reload(adapter_path=...)``, см. Phase 2.
  * Не делает eval с метриками BLEU/ROUGE — для деловой переписки
    они малопоказательны, надёжнее ручной check на 5-10 примерах.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger


# Дефолты подобраны под GigaChat3.1-10B-A1.8B-MLX-4bit на 32 GB машине.
# Источники: рекомендации MLX team + наш бенчмарк.  Можно переопределять
# из CLI / config.
@dataclass
class LoraConfig:
    """Параметры запуска ``mlx_lm.lora``."""
    model: str           # путь или HF-id базовой модели
    data_dir: Path       # директория с train.jsonl + valid.jsonl
    adapter_path: Path   # куда писать adapter weights + config

    # Гиперпараметры
    iters: int = 600           # количество итераций (обычно 200-1000)
    batch_size: int = 1        # на 32GB MoE-моделях >1 редко влезает
    lora_layers: int = 8       # сколько последних слоёв адаптировать
    learning_rate: float = 1e-5
    grad_accum_steps: int = 1
    val_batches: int = 25
    steps_per_eval: int = 50
    save_every: int = 100      # checkpoints каждые N итераций
    seed: int = 42

    # Безопасные пределы
    max_seq_length: int = 2048  # ограничение длины sequence в датасете

    def as_cli_args(self) -> list[str]:
        """Собрать аргументы командной строки для ``mlx_lm.lora``.

        Ключи имени соответствуют ``mlx_lm.lora --help`` (mlx-lm 0.30+).
        """
        return [
            "--model", str(self.model),
            "--data", str(self.data_dir),
            "--train",
            "--iters", str(self.iters),
            "--batch-size", str(self.batch_size),
            "--num-layers", str(self.lora_layers),
            "--learning-rate", f"{self.learning_rate:g}",
            "--grad-checkpoint",  # экономия RAM на длинных sequence
            "--val-batches", str(self.val_batches),
            "--steps-per-eval", str(self.steps_per_eval),
            "--save-every", str(self.save_every),
            "--adapter-path", str(self.adapter_path),
            "--max-seq-length", str(self.max_seq_length),
            "--seed", str(self.seed),
        ]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _resolve_mlx_lora_cmd() -> list[str]:
    """Найти как звать mlx_lm.lora — через ``python -m`` или прямой CLI.

    Установка через uv обычно даёт обе формы; предпочитаем ``python -m``,
    он гарантировано использует Python из текущего venv.
    """
    import sys
    return [sys.executable, "-m", "mlx_lm.lora"]


def run_training(
    cfg: LoraConfig,
    *,
    log_path: Optional[Path] = None,
) -> Iterator[str]:
    """Запустить обучение, стримить stdout/stderr построчно.

    Использование:

        for line in run_training(cfg, log_path=Path("data/lora/last_run.log")):
            print(line)
            # или: emit Server-Sent Event клиенту

    Возвращает строки лога; пишет их в файл если ``log_path``.  Поднимает
    ``subprocess.CalledProcessError`` если mlx_lm.lora упал ненулевым
    кодом возврата (модель не найдена, OOM, и т. п.).
    """
    cfg.adapter_path.mkdir(parents=True, exist_ok=True)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("w", encoding="utf-8")
    else:
        log_fh = None

    cmd = _resolve_mlx_lora_cmd() + cfg.as_cli_args()
    pretty = " ".join(shlex.quote(c) for c in cmd)
    logger.info(f"[lora_trainer] $ {pretty}")
    if log_fh:
        log_fh.write(f"$ {pretty}\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if log_fh:
                log_fh.write(line + "\n")
                log_fh.flush()
            yield line

        rc = proc.wait()
        if rc != 0:
            msg = f"mlx_lm.lora exited with code {rc}"
            logger.error(f"[lora_trainer] {msg}")
            if log_fh:
                log_fh.write(f"\n# {msg}\n")
            raise subprocess.CalledProcessError(rc, cmd)
    finally:
        if log_fh:
            log_fh.close()


# ---------------------------------------------------------------------------
# Adapter management
# ---------------------------------------------------------------------------


def write_adapter_metadata(adapter_path: Path, cfg: LoraConfig, manifest: dict) -> None:
    """Положить рядом с весами адаптера ``training_metadata.json``.

    Без этого через месяц непонятно «какой адаптер обучали с какими
    параметрами на каких данных».  Пишем всё в одно место.
    """
    meta = {
        "config": {
            **asdict(cfg),
            "data_dir": str(cfg.data_dir),
            "adapter_path": str(cfg.adapter_path),
            "model": str(cfg.model),
        },
        "dataset_manifest": manifest,
    }
    out = adapter_path / "training_metadata.json"
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[lora_trainer] wrote {out}")


def adapter_status(adapter_path: Path) -> dict:
    """Краткий отчёт об адаптере для CLI ``pa lora status``."""
    if not adapter_path.exists():
        return {"exists": False, "path": str(adapter_path)}

    # mlx_lm.lora пишет ``adapters.safetensors`` + ``adapter_config.json``
    weights = adapter_path / "adapters.safetensors"
    config = adapter_path / "adapter_config.json"
    meta = adapter_path / "training_metadata.json"

    return {
        "exists": True,
        "path": str(adapter_path),
        "weights_present": weights.exists(),
        "config_present": config.exists(),
        "size_mb": (
            round(weights.stat().st_size / 1024 / 1024, 1)
            if weights.exists() else 0
        ),
        "metadata": (
            json.loads(meta.read_text(encoding="utf-8"))
            if meta.exists() else None
        ),
    }


def clear_adapter(adapter_path: Path) -> bool:
    """Удалить директорию адаптера.  Возвращает True если что-то удалили."""
    if not adapter_path.exists():
        return False
    shutil.rmtree(adapter_path)
    logger.info(f"[lora_trainer] removed {adapter_path}")
    return True
