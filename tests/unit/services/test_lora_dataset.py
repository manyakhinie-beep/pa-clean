"""
Unit tests for ``services.lora_dataset`` — извлечение обучающих пар
из vault для mlx_lm.lora.

Покрытие:
  * Пара (входящее → твой ответ) попадает в выборку
  * Self-emails (sender == user) исключаются
  * Слишком короткие ответы фильтруются
  * Quoted-history вырезается из тела
  * train/valid split воспроизводим при том же seed
  * manifest содержит правильные счётчики
  * Кап max_examples берёт самые свежие
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from personal_assistant.services import lora_dataset as ds


# ----------------------------------------------------------------------
# Helpers — генерация mail-md файлов в tmp vault
# ----------------------------------------------------------------------


def _mail_md(
    tmp: Path,
    *,
    thread_id: str,
    sender_email: str,
    date: str,
    body: str,
    subject: str = "test",
    sender_name: str = "",
) -> Path:
    """Записать письмо в vault/mail/YYYY/MM/<id>.md и вернуть путь."""
    dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
    sub_dir = tmp / "mail" / f"{dt.year:04d}" / f"{dt.month:02d}"
    sub_dir.mkdir(parents=True, exist_ok=True)
    md = sub_dir / f"{thread_id}_{int(dt.timestamp())}.md"
    md.write_text(
        f"""---
thread_id: {thread_id}
sender_email: {sender_email}
sender_name: "{sender_name}"
from: {sender_email}
subject: "{subject}"
date: {date}
---
{body}
""",
        encoding="utf-8",
    )
    return md


# ----------------------------------------------------------------------
# Body cleanup
# ----------------------------------------------------------------------


class TestCleanBody:
    def test_removes_quoted_lines(self):
        text = "Hi, see below.\n\n> Original from X\n> 2nd quoted line"
        assert ds._clean_body(text) == "Hi, see below."

    def test_cuts_off_forwarded_block(self):
        text = (
            "Спасибо! Готов встретиться завтра.\n\n"
            "--------- Original Message ---------\n"
            "От: bob@x.ru\n"
            "Когда удобно?"
        )
        cleaned = ds._clean_body(text)
        assert "Спасибо!" in cleaned
        assert "Original Message" not in cleaned
        assert "Когда удобно?" not in cleaned

    def test_strips_signature(self):
        text = "Мой ответ.\n\n-- \nИгорь\n+7 999 111-22-33"
        assert ds._clean_body(text) == "Мой ответ."

    def test_collapses_blank_lines(self):
        text = "A\n\n\n\n\nB"
        assert "\n\n\n" not in ds._clean_body(text)


# ----------------------------------------------------------------------
# build_pairs
# ----------------------------------------------------------------------


class TestBuildPairs:
    def test_extracts_reply_pair(self, tmp_path: Path):
        user = "igor@example.com"
        _mail_md(tmp_path, thread_id="T1", sender_email="bob@x.ru",
                 date="2026-05-20T10:00:00",
                 body="Прошу прислать договор до пятницы." * 3)
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T15:00:00",
                 body="Высылаю договор. Подпишу до четверга, если будут правки — пиши." * 2)

        pairs = ds.build_pairs(tmp_path, user_email=user)
        assert len(pairs) == 1
        assert pairs[0].thread_id == "T1"
        assert "договор" in pairs[0].incoming_body.lower()
        assert "Высылаю" in pairs[0].reply_body

    def test_excludes_self_to_self(self, tmp_path: Path):
        """Если оба письма от user-а — это не пара (сам себе писал)."""
        user = "igor@example.com"
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T10:00:00",
                 body="Заметка для себя про задачу " * 5)
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T11:00:00",
                 body="Ещё одна заметка про задачу " * 5)
        assert ds.build_pairs(tmp_path, user_email=user) == []

    def test_excludes_short_replies(self, tmp_path: Path):
        user = "igor@example.com"
        _mail_md(tmp_path, thread_id="T1", sender_email="bob@x.ru",
                 date="2026-05-20T10:00:00",
                 body="Прошу прислать договор. " * 5)
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T11:00:00",
                 body="Ок.")  # слишком короткий
        assert ds.build_pairs(tmp_path, user_email=user, min_reply_chars=80) == []

    def test_pair_only_from_immediate_predecessor(self, tmp_path: Path):
        """Если ты ответил на письмо bob-а, потом сам себе reply,
        потом снова bob — только bob→ты считаем парой; ты→ты не считаем."""
        user = "igor@example.com"
        _mail_md(tmp_path, thread_id="T1", sender_email="bob@x.ru",
                 date="2026-05-20T10:00:00", body="Письмо от боба " * 10)
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T11:00:00",
                 body="Хороший ответ от user-а " * 10)
        # Дальше user сам себе:
        _mail_md(tmp_path, thread_id="T1", sender_email=user,
                 date="2026-05-20T12:00:00",
                 body="Заметка следом " * 10)
        pairs = ds.build_pairs(tmp_path, user_email=user)
        assert len(pairs) == 1
        assert "от боба" in pairs[0].incoming_body.lower()

    def test_skips_messages_without_thread_id(self, tmp_path: Path):
        """Если у письма нет thread_id — игнорируем (нечем сгруппировать)."""
        user = "igor@example.com"
        # thread_id пустой
        md = tmp_path / "mail" / "2026" / "05" / "lone.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(
            "---\nthread_id: \nsender_email: bob@x.ru\nfrom: bob@x.ru\n"
            f"date: 2026-05-20T10:00:00\n---\nХорошее письмо " * 1,
            encoding="utf-8",
        )
        assert ds.build_pairs(tmp_path, user_email=user) == []

    def test_max_examples_caps_to_most_recent(self, tmp_path: Path):
        """Если max_examples=2, оставляем 2 самых свежих ответа."""
        user = "igor@example.com"
        # 4 пары T1..T4, ответы 1.06, 2.06, 3.06, 4.06
        for i, d in enumerate(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"], 1):
            _mail_md(tmp_path, thread_id=f"T{i}", sender_email="bob@x.ru",
                     date=f"{d}T10:00:00", body=f"Письмо {i} " * 10)
            _mail_md(tmp_path, thread_id=f"T{i}", sender_email=user,
                     date=f"{d}T11:00:00", body=f"Ответ {i} полный текст " * 10)

        pairs = ds.build_pairs(tmp_path, user_email=user, max_examples=2)
        assert len(pairs) == 2
        # Сохранили самые свежие (3 и 4)
        assert "Ответ 3" in pairs[0].reply_body
        assert "Ответ 4" in pairs[1].reply_body

    def test_requires_user_email(self, tmp_path: Path):
        with pytest.raises(ValueError, match="user_email"):
            ds.build_pairs(tmp_path, user_email="")

    def test_empty_vault_returns_empty(self, tmp_path: Path):
        assert ds.build_pairs(tmp_path, user_email="x@x.com") == []


# ----------------------------------------------------------------------
# write_dataset
# ----------------------------------------------------------------------


class TestWriteDataset:
    def _make_pair(self, idx: int) -> ds.Pair:
        return ds.Pair(
            thread_id=f"T{idx}",
            incoming_from="Bob",
            incoming_subject=f"Subj {idx}",
            incoming_body=f"Input {idx}",
            reply_body=f"Reply {idx}",
            reply_date=f"2026-05-{idx:02d}T10:00:00+00:00",
        )

    def test_writes_train_and_valid_jsonl(self, tmp_path: Path):
        pairs = [self._make_pair(i) for i in range(1, 11)]
        manifest = ds.write_dataset(
            pairs, tmp_path, system_prompt="SYS", train_split=0.8,
        )
        train = (tmp_path / "train.jsonl").read_text(encoding="utf-8")
        valid = (tmp_path / "valid.jsonl").read_text(encoding="utf-8")
        train_lines = [l for l in train.splitlines() if l.strip()]
        valid_lines = [l for l in valid.splitlines() if l.strip()]
        assert len(train_lines) == 8
        assert len(valid_lines) == 2
        assert manifest["train_count"] == 8
        assert manifest["valid_count"] == 2

    def test_jsonl_format_is_chat_messages(self, tmp_path: Path):
        # Минимум 5 пар, иначе int(N * 0.8) == 0 и train.jsonl пустой.
        pairs = [self._make_pair(i) for i in range(1, 6)]
        ds.write_dataset(pairs, tmp_path, system_prompt="MY_SYS")
        # Берём train.jsonl ИЛИ valid.jsonl — главное чтобы первый объект
        # был валидным chat-messages dict.
        any_file = (tmp_path / "train.jsonl").read_text(encoding="utf-8") \
                   + (tmp_path / "valid.jsonl").read_text(encoding="utf-8")
        first = [l for l in any_file.splitlines() if l.strip()][0]
        obj = json.loads(first)
        assert "messages" in obj
        roles = [m["role"] for m in obj["messages"]]
        assert roles == ["system", "user", "assistant"]
        assert obj["messages"][0]["content"] == "MY_SYS"

    def test_split_reproducible_with_seed(self, tmp_path: Path):
        pairs = [self._make_pair(i) for i in range(1, 21)]
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        ds.write_dataset(pairs, out_a, system_prompt="x", seed=42)
        ds.write_dataset(pairs, out_b, system_prompt="x", seed=42)
        assert (
            (out_a / "train.jsonl").read_text() == (out_b / "train.jsonl").read_text()
        )

    def test_empty_pairs_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="no pairs"):
            ds.write_dataset([], tmp_path, system_prompt="x")

    def test_invalid_split_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="train_split"):
            ds.write_dataset(
                [self._make_pair(1)], tmp_path, system_prompt="x", train_split=0.1,
            )

    def test_manifest_records_date_range(self, tmp_path: Path):
        pairs = [self._make_pair(1), self._make_pair(5), self._make_pair(10)]
        manifest = ds.write_dataset(pairs, tmp_path, system_prompt="x")
        assert "2026-05-01" in manifest["earliest_reply"]
        assert "2026-05-10" in manifest["latest_reply"]
