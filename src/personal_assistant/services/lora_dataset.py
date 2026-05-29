"""
lora_dataset.py — генерация обучающего набора для ``mlx_lm.lora`` из vault-а.

Идея.  Vault уже содержит сотни/тысячи твоих реальных переписок: входящее
письмо → твой реальный ответ.  Это **золотой стандарт** того, как ты сам
пишешь — и именно такой паттерн стоит передать модели через LoRA-fine-tune,
чтобы драфты ассистента звучали как ты, а не как обобщённый деловой стиль
GigaChat3.1 «из коробки».

Извлекаемая пара (per task=draft):
  ┌─────────────────────────────────────────────────────────────────┐
  │ messages: [                                                     │
  │   {"role": "system",    "content": <DEFAULT_DRAFT_SYSTEM>},     │
  │   {"role": "user",      "content": "Письмо от <X>:\\n<тело>"},  │
  │   {"role": "assistant", "content": <твой ответ>},               │
  │ ]                                                               │
  └─────────────────────────────────────────────────────────────────┘

Это формат ``mlx_lm.lora --data <dir>`` принимает напрямую (``train.jsonl``
+ ``valid.jsonl``).

Фильтры:
  * Только письма с настоящим ответом (≥ ``min_reply_chars``).
  * Только пары где входящее ≠ твоё (excludes self-emails).
  * Только пары внутри одного thread_id и непосредственно следующие
    друг за другом (по дате).
  * Body чистится от quoted-history (»» цитат) перед записью.

API: ``build_pairs(vault_root, user_email) -> list[Pair]`` — чистая
функция; ``write_dataset(pairs, out_dir, split=0.8)`` — пишет JSONL.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from personal_assistant.mlx_server.vault_index import VaultDoc


# Минимальная длина твоего ответа — иначе это «Спасибо!», авто-ответ,
# подпись.  Не учит модель ничему полезному.
MIN_REPLY_CHARS = 80

# Максимум на входящее письмо — обрезаем чтобы не раздувать контекст
# во время обучения.  4 KB ≈ 1000-1500 токенов.
MAX_INCOMING_CHARS = 4_000

# Максимум на твой ответ — обрезаем сверхдлинные ответы, иначе loss
# доминирован одним примером.
MAX_REPLY_CHARS = 3_000


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Pair:
    """Одна пара (входящее → твой ответ) для обучения."""
    thread_id: str
    incoming_from: str
    incoming_subject: str
    incoming_body: str
    reply_body: str
    reply_date: str  # ISO

    def as_chat_messages(self, system_prompt: str) -> dict:
        """Сериализовать в формате ``messages`` для mlx_lm.lora."""
        user_content = (
            f"Письмо от {self.incoming_from}:\n\n"
            f"Тема: {self.incoming_subject}\n\n"
            f"{self.incoming_body}"
        )
        return {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": self.reply_body},
            ]
        }


# ---------------------------------------------------------------------------
# Body cleanup
# ---------------------------------------------------------------------------

_QUOTE_LINE_RE = re.compile(r"^(?:>+|\|).*$", re.MULTILINE)
_FORWARDED_RE = re.compile(
    r"^[-–—]{2,}\s*(?:Original Message|Forwarded message|Пересланное|"
    r"От кого:|From:|От:|Sent:|Отправлено:).*",
    re.MULTILINE | re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"\n--\s*\n.*$",  # стандартный sigdash "-- \n"
    re.DOTALL,
)


def _clean_body(text: str) -> str:
    """Убрать quoted-history и signature.  Сохраняет твою свежую мысль."""
    if not text:
        return ""
    # Срезаем всё начиная с «Original Message» / «От кого:» — это начало
    # цитаты предыдущего письма.
    m = _FORWARDED_RE.search(text)
    if m:
        text = text[: m.start()]
    # Убираем линии-цитаты (»» и | префиксы).
    text = _QUOTE_LINE_RE.sub("", text)
    # Срезаем подпись через sigdash.
    text = _SIGNATURE_RE.sub("", text)
    # Множественные пустые строки → одна.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Vault traversal
# ---------------------------------------------------------------------------


def _walk_mail(vault_root: Path) -> Iterable[VaultDoc]:
    """Yield все mail-доки из vault."""
    from personal_assistant.mlx_server.vault_index import _parse_frontmatter

    mail_dir = vault_root / "mail"
    if not mail_dir.exists():
        return
    for md in mail_dir.rglob("*.md"):
        try:
            raw = md.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(raw)
            yield VaultDoc(
                path=md,
                section="mail",
                frontmatter=fm,
                content=body,
                raw=raw,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[lora_dataset] skip {md.name}: {exc}")
            continue


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Pair extraction
# ---------------------------------------------------------------------------


def build_pairs(
    vault_root: Path,
    user_email: str,
    *,
    min_reply_chars: int = MIN_REPLY_CHARS,
    max_examples: Optional[int] = None,
) -> list[Pair]:
    """Извлечь пары (входящее → твой ответ) из vault.

    Возвращает отсортированный по дате список — стабильный порядок
    нужен для воспроизводимого train/valid split.
    """
    if not user_email or "@" not in user_email:
        raise ValueError(
            "user_email is required for pair extraction — "
            "set PA_USER_EMAIL in .env or pass explicitly"
        )

    user_email_lc = user_email.strip().lower()

    # ── 1. Сгруппировать письма по thread_id ────────────────────────────
    threads: dict[str, list[tuple[datetime, VaultDoc]]] = {}
    skipped_no_thread = 0
    skipped_no_date = 0
    for doc in _walk_mail(vault_root):
        thread_id = str(doc.frontmatter.get("thread_id") or "").strip()
        if not thread_id:
            skipped_no_thread += 1
            continue
        dt = _parse_date(doc.date)
        if dt is None:
            skipped_no_date += 1
            continue
        threads.setdefault(thread_id, []).append((dt, doc))

    logger.info(
        f"[lora_dataset] threads={len(threads)} "
        f"(skipped no-thread-id={skipped_no_thread}, no-date={skipped_no_date})"
    )

    # ── 2. Внутри каждого thread найти пары входящее → твой ответ ──────
    pairs: list[Pair] = []
    for thread_id, items in threads.items():
        items.sort(key=lambda x: x[0])
        for i in range(1, len(items)):
            cur_dt, cur_doc = items[i]
            sender = (cur_doc.sender_email or "").lower()
            if sender != user_email_lc:
                continue  # не твой ответ, пропускаем

            prev_dt, prev_doc = items[i - 1]
            prev_sender = (prev_doc.sender_email or "").lower()
            if not prev_sender or prev_sender == user_email_lc:
                continue  # сам себе писал — не учим

            reply = _clean_body(cur_doc.content)
            if len(reply) < min_reply_chars:
                continue  # «Спасибо!» / sig-only — не учим

            incoming = _clean_body(prev_doc.content)
            if not incoming:
                continue

            pairs.append(
                Pair(
                    thread_id=thread_id,
                    incoming_from=str(
                        prev_doc.frontmatter.get("sender_name")
                        or prev_doc.frontmatter.get("from")
                        or prev_sender
                    ),
                    incoming_subject=str(
                        prev_doc.frontmatter.get("subject") or "(без темы)"
                    ),
                    incoming_body=incoming[:MAX_INCOMING_CHARS],
                    reply_body=reply[:MAX_REPLY_CHARS],
                    reply_date=cur_dt.isoformat(),
                )
            )

    # ── 3. Стабильная сортировка + cap ──────────────────────────────────
    pairs.sort(key=lambda p: p.reply_date)
    if max_examples is not None and len(pairs) > max_examples:
        # Берём САМЫЕ СВЕЖИЕ — твой стиль свежее, обучение полезнее.
        pairs = pairs[-max_examples:]

    logger.info(f"[lora_dataset] extracted {len(pairs)} pairs")
    return pairs


# ---------------------------------------------------------------------------
# Dataset writer
# ---------------------------------------------------------------------------


def write_dataset(
    pairs: list[Pair],
    out_dir: Path,
    *,
    system_prompt: str,
    train_split: float = 0.8,
    seed: int = 42,
) -> dict:
    """Записать train/valid JSONL + manifest.

    Стратегия split-а: фиксированный seed → одна и та же выборка от
    запуска к запуску.  Это критично для последовательных экспериментов
    с iters/layers — иначе изменения сравниваешь с шумом.
    """
    if not pairs:
        raise ValueError("no pairs to write — vault empty or user_email wrong?")
    if not (0.5 <= train_split <= 0.95):
        raise ValueError("train_split must be in [0.5, 0.95]")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Shuffle с фиксированным seed для воспроизводимости.
    indexed = list(enumerate(pairs))
    rng = random.Random(seed)
    rng.shuffle(indexed)
    n_train = int(len(indexed) * train_split)
    train_idx = sorted(i for i, _ in indexed[:n_train])
    valid_idx = sorted(i for i, _ in indexed[n_train:])

    def _write(path: Path, indices: list[int]) -> None:
        with path.open("w", encoding="utf-8") as f:
            for i in indices:
                obj = pairs[i].as_chat_messages(system_prompt)
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    train_path = out_dir / "train.jsonl"
    valid_path = out_dir / "valid.jsonl"
    _write(train_path, train_idx)
    _write(valid_path, valid_idx)

    manifest = {
        "version": 1,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "total_pairs": len(pairs),
        "train_count": len(train_idx),
        "valid_count": len(valid_idx),
        "train_split": train_split,
        "seed": seed,
        "system_prompt_preview": system_prompt[:200],
        "earliest_reply": pairs[0].reply_date if pairs else "",
        "latest_reply": pairs[-1].reply_date if pairs else "",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        f"[lora_dataset] wrote {len(train_idx)} train + {len(valid_idx)} valid "
        f"to {out_dir}"
    )
    return manifest
