"""
VaultWriter — saves data models as Obsidian-compatible .md files.

Vault structure:
  <vault_root>/
    calendar/
      YYYY/
        MM/
          YYYY-MM-DD_<slugified-title>_<uid8>.md
    mail/
      YYYY/
        MM/
          YYYY-MM-DD_<slugified-subject>_<id8>.md
    contacts/
      <email>.md
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger

from personal_assistant.models import CalendarEvent, Contact, MailMessage
from personal_assistant.utils.name_extractor import (
    best_name,
    name_quality,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^\w\s-]")
_SPACE_RE = re.compile(r"[\s_]+")


def _slugify(text: str, max_len: int = 60) -> str:
    text = text.lower()
    text = _SLUG_RE.sub("", text)
    text = _SPACE_RE.sub("-", text).strip("-")
    return text[:max_len]


def _short_id(uid: str, length: int = 8) -> str:
    return hashlib.md5(uid.encode(), usedforsecurity=False).hexdigest()[:length]


# ---------------------------------------------------------------------------
# VaultWriter
# ---------------------------------------------------------------------------


class VaultWriter:
    """Renders models to Markdown files in a vault directory."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root
        self.vault_root.mkdir(parents=True, exist_ok=True)

        # Locate templates directory relative to this file
        templates_dir = Path(__file__).parent.parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape([]),  # no HTML escaping for Markdown
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._env.filters["lower"] = lambda v: str(v).lower()

    # ------------------------------------------------------------------
    # Internal render + write
    # ------------------------------------------------------------------

    def _render(self, template_name: str, **kwargs) -> str:
        tmpl = self._env.get_template(template_name)
        return tmpl.render(
            now=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **kwargs
        )

    def _write(self, path: Path, content: str, overwrite: bool = False) -> bool:
        """Write content to path. Returns True if file was written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            logger.debug(f"Skipping existing file: {path.relative_to(self.vault_root)}")
            return False
        path.write_text(content, encoding="utf-8")
        logger.debug(f"Written: {path.relative_to(self.vault_root)}")
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_event(
        self, event: CalendarEvent, overwrite: bool = False
    ) -> Optional[Path]:
        """Write a CalendarEvent to vault/calendar/YYYY/MM/<slug>.md"""
        content = self._render("event.md.j2", event=event)
        date = event.start
        slug = _slugify(event.title)
        uid8 = _short_id(event.uid)
        filename = f"{date.strftime('%Y-%m-%d')}_{slug}_{uid8}.md"
        path = (
            self.vault_root
            / "calendar"
            / date.strftime("%Y")
            / date.strftime("%m")
            / filename
        )
        return path if self._write(path, content, overwrite=overwrite) else None

    def write_message(
        self, msg: MailMessage, overwrite: bool = False
    ) -> Optional[Path]:
        """Write a MailMessage to vault/mail/YYYY/MM/<slug>.md

        When *overwrite* is ``False`` and the file already exists, the method
        still checks whether ``thread_id`` in the stored frontmatter differs
        from ``msg.thread_id``.  If so, it patches **only** that field so that
        threads are correctly re-grouped after a ThreadTracker pass without
        requiring a full overwrite of the file.
        """
        content = self._render("mail.md.j2", msg=msg)
        date = msg.date
        slug = _slugify(msg.subject)
        id8 = _short_id(msg.message_id)
        filename = f"{date.strftime('%Y-%m-%d')}_{slug}_{id8}.md"
        path = (
            self.vault_root
            / "mail"
            / date.strftime("%Y")
            / date.strftime("%m")
            / filename
        )
        if path.exists() and not overwrite:
            # Patch thread_id in-place if it changed (avoids full rewrite)
            self._patch_thread_id(path, msg.thread_id)
            return None
        return path if self._write(path, content, overwrite=overwrite) else None

    # ------------------------------------------------------------------
    # thread_id patch helper
    # ------------------------------------------------------------------

    def _patch_thread_id(self, path: Path, thread_id: Optional[str]) -> None:
        """Update the ``thread_id`` field in an existing vault mail file.

        Only writes to disk when the stored value actually differs from
        *thread_id*, keeping sync idempotent for unchanged files.
        """
        new_tid = (thread_id or "").strip()
        if not new_tid:
            return  # Nothing useful to write
        try:
            fm = self._read_frontmatter(path)
            existing = str(fm.get("thread_id") or "").strip()
            if existing == new_tid:
                return  # Already up-to-date, no I/O needed

            # Rebuild frontmatter with the new thread_id
            raw = path.read_text(encoding="utf-8")
            if not raw.startswith("---"):
                return
            end_idx = raw.find("\n---", 3)
            if end_idx == -1:
                return
            body_after = raw[end_idx + 4:]  # everything after closing ---
            fm["thread_id"] = new_tid
            new_fm_text = yaml.dump(
                fm,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            ).rstrip("\n")
            path.write_text(f"---\n{new_fm_text}\n---{body_after}", encoding="utf-8")
            logger.info(
                f"Patched thread_id {existing!r} → {new_tid!r} in {path.name}"
            )
        except Exception as exc:
            logger.warning(f"Could not patch thread_id in {path}: {exc}")

    def write_contact(
        self, contact: Contact, overwrite: bool = False
    ) -> Optional[Path]:
        """Write a Contact to vault/contacts/<email>.md

        On top of the basic write, this method:
          1. Enriches ``contact.full_name`` using :mod:`name_extractor` —
             extracting the best-quality normalised name from ``contact.name``
             plus any existing ``full_name`` stored in the vault file.
          2. Merges ``contact.sources`` with sources already recorded in the
             existing file, so no source entry is ever lost.
          3. Skips writing if neither sources nor the full_name changed.
        """
        safe_email = re.sub(r"[^\w@._-]", "_", contact.email)
        path = self.vault_root / "contacts" / f"{safe_email}.md"

        # ── Step 1: enrich full_name from name_extractor ─────────────────────
        contact = self._enrich_contact_name(contact, existing_path=path)

        # ── Step 2: merge sources from existing file ─────────────────────────
        changed = True  # assume changed until proven otherwise
        if path.exists() and not overwrite:
            contact, changed = self._merge_contact_file(contact, path)
            if not changed:
                logger.debug(f"Contact up-to-date: {contact.email}")
                return None

        content = self._render("contact.md.j2", contact=contact)
        return path if self._write(path, content, overwrite=True) else None

    # ------------------------------------------------------------------
    # Contact enrichment helpers
    # ------------------------------------------------------------------

    def _enrich_contact_name(
        self, contact: Contact, existing_path: Optional[Path] = None
    ) -> Contact:
        """Return a copy of *contact* with ``full_name`` enriched.

        Reads any existing ``full_name`` / ``name_source`` from the vault file
        so the best name across all previous syncs is preserved.
        """
        candidates: list[tuple[str, str]] = []

        # Add the name from the incoming contact object
        if contact.name:
            # Determine source from contact.sources (first non-empty)
            src = (contact.sources[0] if contact.sources else "mail")
            candidates.append((contact.name, src))

        # Add any full_name already persisted in the vault file
        if existing_path and existing_path.exists():
            try:
                existing_fm = self._read_frontmatter(existing_path)
                existing_full = existing_fm.get("full_name")
                existing_src  = existing_fm.get("name_source", "contacts")
                if existing_full:
                    candidates.append((str(existing_full), str(existing_src)))
            except Exception as exc:
                logger.debug(f"Could not read existing frontmatter for {contact.email}: {exc}")

        chosen = best_name(candidates) if candidates else None

        # Only update if the chosen name is actually better than what we have
        current_q = name_quality(contact.full_name)
        chosen_q  = name_quality(chosen)

        if chosen and (chosen_q > current_q or not contact.full_name):
            # Determine which source provided this name
            winning_src = contact.sources[0] if contact.sources else "mail"
            for raw, src in candidates:
                from personal_assistant.utils.name_extractor import extract_name as _en
                if _en(raw) == chosen:
                    winning_src = src
                    break

            updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if chosen != contact.full_name:
                logger.info(
                    f"Contact {contact.email}: full_name enriched "
                    f"{contact.full_name!r} → {chosen!r} (source={winning_src})"
                )
            contact = contact.model_copy(update={
                "full_name": chosen,
                "name_source": winning_src,
                "name_updated_at": updated_at,
            })

        return contact

    def _merge_contact_file(
        self, contact: Contact, path: Path
    ) -> tuple[Contact, bool]:
        """Merge existing file's sources into *contact*.  Returns ``(merged, changed)``."""
        try:
            existing_fm = self._read_frontmatter(path)
        except Exception:
            return contact, True  # can't read → overwrite

        existing_sources: list[str] = existing_fm.get("sources") or []
        if isinstance(existing_sources, str):
            existing_sources = [existing_sources]

        merged_sources = list(dict.fromkeys(existing_sources + contact.sources))
        sources_changed = set(merged_sources) != set(existing_sources)

        # Check if full_name changed
        existing_full = existing_fm.get("full_name") or ""
        fullname_changed = (contact.full_name or "") != str(existing_full)

        if not sources_changed and not fullname_changed:
            return contact, False

        contact = contact.model_copy(update={"sources": merged_sources})
        return contact, True

    @staticmethod
    def _read_frontmatter(path: Path) -> dict:
        """Parse YAML frontmatter from a vault .md file."""
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return {}
        end_idx = raw.find("\n---", 3)
        if end_idx == -1:
            return {}
        fm_text = raw[3:end_idx]
        return yaml.safe_load(fm_text) or {}

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def write_events(
        self, events: list[CalendarEvent], overwrite: bool = False
    ) -> tuple[int, int]:
        """Returns (written, skipped)."""
        written = sum(1 for e in events if self.write_event(e, overwrite) is not None)
        return written, len(events) - written

    def write_messages(
        self, messages: list[MailMessage], overwrite: bool = False
    ) -> tuple[int, int]:
        written = sum(
            1 for m in messages if self.write_message(m, overwrite) is not None
        )
        return written, len(messages) - written

    def write_contacts(
        self, contacts: list[Contact], overwrite: bool = False
    ) -> tuple[int, int]:
        written = sum(
            1 for c in contacts if self.write_contact(c, overwrite) is not None
        )
        return written, len(contacts) - written
