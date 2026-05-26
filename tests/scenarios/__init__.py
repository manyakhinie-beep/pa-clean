"""
Scenario tests — интеграционные тесты с реальными внешними зависимостями.

Эти тесты требуют:
  - Apple Silicon + mlx-lm  → тесты в test_mlx_scenarios.py
  - macOS + Calendar/Mail   → тесты в test_applescript_scenarios.py

Запуск только вручную на целевой машине (не в CI):
    uv run pytest tests/scenarios/ -v

Все тесты пропускаются автоматически если зависимость недоступна.
"""
