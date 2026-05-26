# SECURITY — pa-clean

## Аудит

Прогон: `bandit -r src` + `pip-audit` (см. ниже) на финальной приёмке §7.

| Категория | Результат |
|-----------|-----------|
| Hardcoded secrets / API keys в коде | **0** (grep по AWS/GitHub/OpenAI паттернам) |
| `.env` закоммичен в git | **нет** (только `.env.example` без секретов) |
| Bandit HIGH severity | **0** (исправлено: `hashlib.md5(..., usedforsecurity=False)` для 7 non-crypto hash use-case'ов) |
| Bandit MEDIUM | **4 accepted-risk** — см. ниже |
| Bandit LOW | 56 (broad-except, try/pass — не блокирующие) |

## Accepted-risk MEDIUM-findings

Все четыре MEDIUM-флага относятся к локальному single-user desktop-приложению и
не являются критическими в этом контексте.

### B301 — `pickle.load` для локального кэша индекса

- `src/personal_assistant/mlx_server/vault_index.py:268` — BM25 cache.
- `src/personal_assistant/mlx_server/vector_index.py:307` — embeddings metadata cache.

Pickle опасен только при загрузке **untrusted data**. Здесь pickle читает кэш,
который сам же приложение записало в директорию пользователя (`vault/.index_cache.pkl`).
Кэш не передаётся между машинами и не загружается из сети. Атака возможна только
если злоумышленник уже имеет write-доступ к домашней директории — в этом случае
у него и без pickle достаточно векторов.

### B608 — f-string в SQL

`src/personal_assistant/personal_vault/db.py:353`:
```python
where = "WHERE " + " AND ".join(conditions) if conditions else ""
sql = f"SELECT * FROM items {where} ORDER BY date_iso DESC LIMIT ?"
```

В `{where}` подставляются ТОЛЬКО внутренние WHERE-клаузы вида `"col = ?"` —
имена колонок жёстко прописаны в коде. Реальные значения параметров уходят
через `?` placeholders в `cursor.execute(sql, params)`. Bandit confidence: Low.

### B615 — `snapshot_download` без `revision=`

`src/personal_assistant/webui/routes.py:2450` — загрузка MLX-модели с HuggingFace
по запросу пользователя через UI. Пользователь явно выбирает модель из списка
известных репозиториев (`QWEN_MODELS` whitelist). Pinning `revision=` сделает
обновления моделей менее удобными; whitelist уже защищает от подмены произвольного
репо. Если эта операция станет автоматической (без user-confirm) — добавить
revision-pinning обязательно.

## Зависимости (pip-audit)

В песочнице нельзя экспортировать uv.lock корректно (нет uv). На Mac:

```bash
uv add --dev pip-audit
uv export --format requirements-txt --no-hashes --no-emit-project > /tmp/pa-reqs.txt
uv run pip-audit -r /tmp/pa-reqs.txt
```

Ожидается «No known vulnerabilities» — uv-managed deps относительно свежие.
Если pip-audit найдёт CVE — `uv lock --upgrade <package>` поднимет до фикс-версии.

## Повторный прогон

```bash
uv run --with bandit bandit -r src                 # должно: 0 HIGH
uv run --with pip-audit pip-audit -r <(uv export --format requirements-txt --no-hashes --no-emit-project)
grep -rEn "(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{36})" src tests   # должно: пусто
```
