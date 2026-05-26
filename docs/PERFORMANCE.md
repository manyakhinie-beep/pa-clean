# PERFORMANCE — pa-clean

Performance baseline для §7 acceptance. Замеры через `scripts/benchmark.py`.

## Как замерить

```bash
cd /path/to/pa-clean
PA_MLX_MODEL_PATH=/Users/<you>/models/<model-dir> \
  uv run python scripts/benchmark.py > docs/PERFORMANCE.md.new
# затем скопируйте интересный блок ниже под «Baseline»
```

Бенчмарк состоит из двух блоков:

1. **Vault merge** — 1000 синтетических писем × 50 тредов, dedup + thread grouping + vault write в temp-директорию. Никаких внешних зависимостей не требует.
2. **MLX inference** — cold load + cold inference + 3-замерочный steady-state. Требует `PA_MLX_MODEL_PATH` на реальную MLX-модель.

## Baseline (sandbox reference)

Linux sandbox без MLX, Python 3.10 — служит нижней границей для сравнения. Mac
M1/M2 на тех же 1000 сообщений будет значительно быстрее.

```
## Vault merge (sync/dedup/threads)
- synthetic corpus: 1000 messages × 50 threads
- dedup: 13ms (300 unique)
- thread grouping: 1ms (50 threads)
- vault write: 57ms (300 files)
- total merge pipeline: 71ms
```

## Baseline (Mac — заполнить после прогона)

```
## Vault merge
- ...

## MLX inference
- model_path: ...
- cold load time: ...s
- cold inference (64 tok max): ...s — '...'
- steady-state inference (32 tok, median of 3): ...s
```

## Что считать регрессией

| Метрика | Нормальный диапазон (Mac M1/M2) | Регрессия |
|---|---|---|
| Vault merge total (1000 msgs) | < 200ms | > 500ms |
| MLX cold load | 5–30s (зависит от размера модели) | > 60s |
| MLX cold inference 64 tok | 1–5s | > 15s |
| MLX steady-state 32 tok | 0.5–3s | > 8s |

При регрессии — проверьте swap usage, нет ли ghost-процессов, и что
`settings.vault_path` указывает на пустой/мелкий vault, а не на полный продакшен.
