"""
Inbox API — /api/v1/inbox

Reads vault mail & calendar docs and returns them as a unified inbox feed.
Server-side state (read flags, tags, project assignment) persisted in
data/inbox_state.json — separate from the vault so sync doesn't overwrite it.

GET  /api/v1/inbox                           — paginated list with stats + priority
GET  /api/v1/inbox/followup-needed           — items awaiting reply (follow-up detection)
GET  /api/v1/inbox/{item_id}                 — single item detail with full body
GET  /api/v1/inbox/{item_id}/suggestions     — rule-based next-actions + tag hints
GET  /api/v1/inbox/{item_id}/extraction      — return cached extraction (or 404)
POST /api/v1/inbox/{item_id}/extract         — run/force structured extraction
POST /api/v1/inbox/{item_id}/suggest-meeting — propose meeting slots from email
POST /api/v1/inbox/{item_id}/read            — mark as read
POST /api/v1/inbox/{item_id}/unread          — mark as unread
POST /api/v1/inbox/{item_id}/tags            — set / append tags
POST /api/v1/inbox/{item_id}/assign-project  — link item to a project
POST /api/v1/inbox/summarize                 — generate TL;DR for an item via MLX
DELETE /api/v1/inbox/extraction-cache        — clear all extraction cache entries
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/inbox", tags=["inbox"])

# ---------------------------------------------------------------------------
# Persistent state store
# ---------------------------------------------------------------------------

_STATE_PATH = Path("data/inbox_state.json")
_state_lock = threading.Lock()


def _load_state() -> dict[str, Any]:
    """Load inbox_state.json.  Returns {} on missing / corrupt file."""
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[inbox] Failed to load state: {exc}")
    return {}


def _save_state(state: dict[str, Any]) -> None:
    """Atomically write state to disk."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as exc:
        logger.error(f"[inbox] Failed to save state: {exc}")


def _get_item_state(item_id: str) -> dict[str, Any]:
    with _state_lock:
        state = _load_state()
        return dict(state.get(item_id, {}))


def _update_item_state(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _state_lock:
        state = _load_state()
        item_st = state.get(item_id, {})
        item_st.update(patch)
        state[item_id] = item_st
        _save_state(state)
        return dict(item_st)


def _all_states() -> dict[str, Any]:
    with _state_lock:
        return _load_state()


# ---------------------------------------------------------------------------
# Tag / urgency constants
# ---------------------------------------------------------------------------

_TAG_URGENT  = {"срочно", "urgency:critical", "urgency:high", "urgency:urgent", "urgent"}
_TAG_IMPORT  = {"важно", "important", "urgency:medium", "urgency:important",
                "category:finance", "category:legal", "finance", "finances", "финансы"}
_TAG_MEETING = {"meeting", "встреча", "calendar", "событие", "category:meetings"}

# cls values must match rules-tag-pill--{cls} CSS modifier names
_TAG_DISPLAY: dict[str, dict] = {
    # ── urgency ──────────────────────────────────────────────────────────────
    "urgency:urgent":   {"label": "Срочно",         "cls": "urgency-urgent"},
    "urgency:critical": {"label": "Срочно",         "cls": "urgency-urgent"},
    "urgency:high":     {"label": "Срочно",         "cls": "urgency-urgent"},
    "urgent":           {"label": "Срочно",         "cls": "urgency-urgent"},
    "срочно":           {"label": "Срочно",         "cls": "urgency-urgent"},
    "urgency:important":{"label": "Важно",          "cls": "urgency-important"},
    "urgency:medium":   {"label": "Важно",          "cls": "urgency-important"},
    "важно":            {"label": "Важно",          "cls": "urgency-important"},
    "important":        {"label": "Важно",          "cls": "urgency-important"},
    "urgency:low":      {"label": "Обычный",        "cls": "urgency-low"},
    "urgency:normal":   {"label": "Обычный",        "cls": "urgency-low"},
    # ── category ─────────────────────────────────────────────────────────────
    "category:finance":  {"label": "Финансы",       "cls": "category-finance"},
    "finances":          {"label": "Финансы",       "cls": "category-finance"},
    "финансы":           {"label": "Финансы",       "cls": "category-finance"},
    "category:meetings": {"label": "Встречи",       "cls": "category-meetings"},
    "meeting":           {"label": "Встречи",       "cls": "category-meetings"},
    "встреча":           {"label": "Встречи",       "cls": "category-meetings"},
    "calendar":          {"label": "Встречи",       "cls": "category-meetings"},
    "category:projects": {"label": "Проекты",       "cls": "category-projects"},
    "category:hr":       {"label": "HR",            "cls": "category-hr"},
    "category:legal":    {"label": "Юридическое",   "cls": "category-legal"},
    "legal":             {"label": "Юридическое",   "cls": "category-legal"},
    "category:travel":   {"label": "Командировки",  "cls": "category-travel"},
}

_AVATAR_COLORS = [
    "#4F6AF5", "#7C3AED", "#DB2777", "#059669",
    "#D97706", "#0284C7", "#DC2626", "#65A30D",
]

# Rule-based suggestions: (condition, action_label, tag_hints)
_SUGGESTION_RULES: list[tuple[set, str, list[str]]] = [
    ({"urgency:urgent", "urgency:critical", "urgent", "срочно"},
     "Ответить сегодня", ["urgency:urgent"]),
    ({"category:finance", "finances", "финансы"},
     "Передать в бухгалтерию", ["category:finance"]),
    ({"category:legal", "legal"},
     "Проверить с юристом", ["category:legal"]),
    ({"meeting", "встреча", "category:meetings"},
     "Создать событие в календаре", ["category:meetings"]),
    ({"category:travel"},
     "Забронировать / согласовать", ["category:travel"]),
    ({"category:hr"},
     "Проверить в HR-системе", ["category:hr"]),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avatar_color(name: str) -> str:
    idx = int(hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(), 16) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def _initials(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _fmt_time(date_str: Optional[str]) -> str:
    """Return HH:MM or 'вчера' or 'дд.мм' relative label."""
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now.date() - dt.date()
        if delta.days == 0:
            return dt.strftime("%H:%M")
        if delta.days == 1:
            return "вчера"
        if delta.days <= 6:
            _days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            return _days[dt.weekday()]
        return dt.strftime("%d.%m")
    except Exception:
        return str(date_str)[:5]


def _display_tags(tags: list[str]) -> list[dict]:
    """Convert raw tag list to displayable {label, cls} pairs (deduplicated)."""
    seen_labels: set[str] = set()
    result = []
    for t in tags:
        t_lower = t.lower()
        info = _TAG_DISPLAY.get(t_lower)
        if not info and ":" in t_lower:
            kind, _, value = t_lower.partition(":")
            label = value.replace("-", " ").replace("_", " ").capitalize()
            css_cls = f"{kind}-{value.replace('_', '-')}"
            info = {"label": label, "cls": css_cls}
        if info and info["label"] not in seen_labels:
            seen_labels.add(info["label"])
            result.append(info)
    return result


def _parse_sender_role(sender_str: str) -> tuple[str, str]:
    if not sender_str:
        return ("Неизвестный", "")
    name = re.sub(r"\s*<[^>]+>", "", sender_str).strip()
    for sep in (" · ", " — ", " - ", ", "):
        if sep in name:
            parts = name.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return name, ""


def _build_suggestions(tags_raw: list[str], item_type: str) -> dict:
    """Generate rule-based next_actions and tag_suggestions without LLM."""
    tags_lower = {t.lower() for t in tags_raw}
    next_actions: list[str] = []
    tag_hints: list[str] = []

    # Apply rules
    for condition_tags, action, hints in _SUGGESTION_RULES:
        if condition_tags & tags_lower:
            next_actions.append(action)
            for h in hints:
                if h not in tag_hints:
                    tag_hints.append(h)

    # Default actions based on type
    if item_type == "email" and "Ответить сегодня" not in next_actions:
        next_actions.append("Составить ответ")
    if item_type == "meeting":
        next_actions.append("Подготовить повестку")

    # Generic tag suggestions derived from content
    if not tag_hints:
        if item_type == "meeting":
            tag_hints = ["category:meetings"]
        else:
            tag_hints = ["urgency:medium"]

    return {
        "next_actions": next_actions[:4],
        "tag_suggestions": tag_hints[:4],
    }


def _doc_to_item(doc, item_state: Optional[dict] = None) -> dict:
    """Convert a VaultDoc to an inbox item dict, merging server-side state."""
    fm = doc.frontmatter
    st = item_state or {}

    # Sender
    sender_raw = (
        str(fm.get("sender_name") or fm.get("sender") or fm.get("from") or "").strip()
        or doc.sender_email
        or "Неизвестный"
    )
    sender_name, sender_role = _parse_sender_role(sender_raw)
    sender_email = doc.sender_email or ""

    subject = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()
    date_str = doc.date
    thread_id = str(fm.get("thread_id") or "")
    thread_count = int(fm.get("thread_count") or 0) or None

    # Merge vault tags + user-applied tags from state
    vault_tags = list(doc.tags)
    extra_tags = list(st.get("extra_tags", []))
    tags_raw = vault_tags + [t for t in extra_tags if t not in vault_tags]

    is_urgent = any(t.lower() in _TAG_URGENT for t in tags_raw)
    is_important = any(t.lower() in _TAG_IMPORT for t in tags_raw)
    item_type = "meeting" if doc.section == "calendar" or any(
        t.lower() in _TAG_MEETING for t in tags_raw
    ) else "email"

    preview = doc.ui_preview(180)
    item_id = str(fm.get("id") or doc.path.stem)

    return {
        "id": item_id,
        "type": item_type,
        "subject": subject,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "sender_role": sender_role,
        "sender_initials": _initials(sender_name),
        "sender_color": _avatar_color(sender_name),
        "date": date_str or "",
        "time_label": _fmt_time(date_str),
        "tags_raw": tags_raw,
        "tags": _display_tags(tags_raw),
        "thread_id": thread_id,
        "thread_count": thread_count,
        "body_preview": preview,
        "body": doc.content,
        "is_urgent": is_urgent,
        "is_important": is_important,
        "source": doc.section,
        "path": str(doc.path),
        # Server-side state fields
        "read": bool(st.get("read", False)),
        "project_id": st.get("project_id"),
        "project_name": st.get("project_name"),
        # Structured extraction (merged from cache, may be None)
        "extraction": st.get("extraction"),
        # Thread graph summary — top-3 participant avatars for list view
        # (full graph available via GET /thread/{thread_id}/graph)
        "participants_summary": st.get("participants_summary"),
        "my_turn": bool(st.get("my_turn", False)),
    }


def _get_index():
    """Safely get the shared VaultIndex from app state."""
    try:
        from personal_assistant.mlx_server import server as _srv
        idx = getattr(_srv.state, "index", None)
        return idx
    except Exception:
        return None


def _get_vault_path() -> Optional[Path]:
    """Return vault root path from index or env."""
    idx = _get_index()
    if idx is not None and hasattr(idx, "root"):
        return Path(idx.root)
    vp = os.environ.get("PA_VAULT_PATH", "")
    if vp:
        return Path(vp)
    return None


def _get_my_email() -> str:
    """Return user's own email from env / profile for followup detection."""
    email = os.environ.get("PA_USER_EMAIL", "")
    if not email:
        try:
            from personal_assistant.profile.service import load_profile
            email = load_profile().user_email or ""
        except Exception:
            pass
    return email


def _get_my_name() -> str:
    """Return user's display name from profile."""
    try:
        from personal_assistant.profile.service import load_profile
        return load_profile().full_name or ""
    except Exception:
        return ""


def _get_mlx_engine():
    """Return the loaded MLX engine if available, else None."""
    try:
        from personal_assistant.mlx_server import server as _srv
        engine = getattr(_srv.state, "engine", None)
        if engine and getattr(engine, "is_loaded", False):
            return engine
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Routes — list & detail
# ---------------------------------------------------------------------------

@router.get("/followup-needed")
def get_followup_needed(
    threshold_days: int = Query(default=2, ge=0, le=30),
):
    """
    Return inbox items awaiting a follow-up reply.

    An item is flagged when:
      • it is an email (not calendar)
      • extraction says reply_required=True OR intent ∈ {request, question}
      • the letter is older than threshold_days days
      • no outgoing reply found in the same thread in the vault

    Returns:
        {items: [item_id,...], count: int, threshold_days: int}
    """
    from personal_assistant.services.followup_service import detect_followup_needed

    idx = _get_index()
    if idx is None:
        return {"items": [], "count": 0, "threshold_days": threshold_days}

    all_states = _all_states()
    all_items = [
        _doc_to_item(d, all_states.get(str(d.frontmatter.get("id") or d.path.stem)))
        for d in idx.docs
        if d.section in ("mail", "calendar")
    ]

    vault_path = _get_vault_path()
    my_email = _get_my_email()

    flagged = detect_followup_needed(
        all_items,
        vault_path=vault_path,
        my_email=my_email,
        threshold_days=threshold_days,
    )
    return {"items": flagged, "count": len(flagged), "threshold_days": threshold_days}


@router.get("")
def get_inbox(
    filter: str = "all",   # "all" | "mail" | "calendar" | "urgent" | "important" | "followup"
    sort_by: str = "date",  # "date" | "priority"
    limit: int = 200,
    offset: int = 0,
):
    """
    Return inbox items with priority score and followup flag, with summary stats.

    Fields added per item:
      priority (int 0–100), priority_label ("low"|"medium"|"high"),
      followup_needed (bool)

    Sorting:
      sort_by=date     — newest first (default)
      sort_by=priority — highest priority first, then newest within same bucket
    """
    from personal_assistant.mlx_server.tasks.priority import enrich_with_priority
    from personal_assistant.services.followup_service import enrich_with_followup

    idx = _get_index()
    if idx is None:
        return {"items": [], "stats": {"total": 0, "unread": 0, "urgent": 0, "important": 0, "followup": 0}}

    docs = list(idx.docs)
    all_states = _all_states()

    # Base section filter
    if filter == "mail":
        docs = [d for d in docs if d.section == "mail"]
    elif filter == "calendar":
        docs = [d for d in docs if d.section == "calendar"]
    else:
        docs = [d for d in docs if d.section in ("mail", "calendar")]

    # Sort by date descending initially
    docs.sort(key=lambda d: str(d.date or ""), reverse=True)

    # Build all items (needed for stats + urgency filter)
    all_items = [
        _doc_to_item(d, all_states.get(str(d.frontmatter.get("id") or d.path.stem)))
        for d in docs
    ]

    # --- Priority enrichment (always, lightweight) ---
    vault_path = _get_vault_path()
    mlx_engine = _get_mlx_engine()
    try:
        enrich_with_priority(all_items, vault_path=vault_path, mlx_engine=mlx_engine)
    except Exception as exc:
        logger.warning(f"[inbox] priority enrichment failed: {exc}")
        for it in all_items:
            it.setdefault("priority", 0)
            it.setdefault("priority_label", "low")

    # --- Follow-up enrichment ---
    my_email = _get_my_email()
    try:
        enrich_with_followup(all_items, vault_path=vault_path, my_email=my_email)
    except Exception as exc:
        logger.warning(f"[inbox] followup enrichment failed: {exc}")
        for it in all_items:
            it.setdefault("followup_needed", False)

    # Apply urgency/importance/followup filter
    if filter == "urgent":
        all_items = [it for it in all_items if it["is_urgent"]]
    elif filter == "important":
        all_items = [it for it in all_items if it["is_important"]]
    elif filter == "followup":
        all_items = [it for it in all_items if it.get("followup_needed")]

    # Priority re-sort if requested
    def _date_ts(s: str) -> float:
        """Parse ISO date string to Unix timestamp for secondary sort; 0.0 on failure."""
        if not s:
            return 0.0
        try:
            from datetime import datetime, timezone  # noqa: PLC0415
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0

    if sort_by == "priority":
        all_items.sort(key=lambda it: (-it.get("priority", 0), -_date_ts(it.get("date", ""))))

    total = len(all_items)
    unread_count = sum(1 for it in all_items if not it["read"])
    urgent_count = sum(1 for it in all_items if it["is_urgent"])
    important_count = sum(1 for it in all_items if it["is_important"])
    followup_count = sum(1 for it in all_items if it.get("followup_needed"))

    return {
        "items": all_items[offset: offset + limit],
        "stats": {
            "total": total,
            "unread": unread_count,
            "urgent": urgent_count,
            "important": important_count,
            "followup": followup_count,
        },
        "has_more": (offset + limit) < total,
    }


@router.post("/{item_id}/draft-context")
def get_draft_context(item_id: str):
    """
    Build full thread context for draft reply generation (Stage 4: Thread-Aware Draft).

    Scans the vault for all messages in the same thread as item_id,
    identifies the user's own previous replies, extracts key facts, and
    assembles a ready-to-use context_prompt for /api/chat/send.

    Returns:
        {
            item_id, subject, sender, sender_email, thread_id,
            thread_messages, thread_summary, key_facts,
            my_previous_replies, draft_hint, context_prompt, message_count
        }

    Graceful degradation:
        - Returns minimal context when vault is not loaded or has no thread.
        - Works without MLX (rule-based summary fallback).
    """
    from personal_assistant.services.draft_context_service import build_draft_context

    vault_path = _get_vault_path()
    my_email = _get_my_email()
    mlx_engine = _get_mlx_engine()

    try:
        ctx = build_draft_context(
            item_id=item_id,
            vault_path=vault_path,
            my_email=my_email,
            mlx_engine=mlx_engine,
        )
    except Exception as exc:
        logger.warning(f"[inbox] draft-context failed for {item_id!r}: {exc}")
        ctx = {
            "item_id": item_id,
            "subject": "Без темы",
            "sender": "",
            "sender_email": "",
            "thread_id": "",
            "thread_messages": [],
            "thread_summary": "Контекст треда недоступен.",
            "key_facts": [],
            "my_previous_replies": [],
            "draft_hint": "",
            "context_prompt": f"Составь черновик ответа на письмо (id: {item_id}).",
            "message_count": 0,
        }

    return ctx


@router.get("/{item_id}/suggestions")
def get_suggestions(item_id: str):
    """Return rule-based next_actions and tag_suggestions for an inbox item."""
    idx = _get_index()
    if idx is None:
        raise HTTPException(503, "Vault не загружен")

    for doc in idx.docs:
        fm = doc.frontmatter
        doc_id = str(fm.get("id") or doc.path.stem)
        if doc_id == item_id:
            st = _get_item_state(item_id)
            item = _doc_to_item(doc, st)
            return _build_suggestions(item["tags_raw"], item["type"])

    raise HTTPException(404, f"Item '{item_id}' not found")


@router.get("/{item_id}")
def get_inbox_item(item_id: str):
    """Return a single inbox item by ID (vault file stem)."""
    idx = _get_index()
    if idx is None:
        raise HTTPException(503, "Vault не загружен")

    for doc in idx.docs:
        fm = doc.frontmatter
        doc_id = str(fm.get("id") or doc.path.stem)
        if doc_id == item_id:
            st = _get_item_state(item_id)
            return _doc_to_item(doc, st)

    raise HTTPException(404, f"Item '{item_id}' not found")


# ---------------------------------------------------------------------------
# Routes — state mutations
# ---------------------------------------------------------------------------

@router.post("/{item_id}/read")
def mark_read(item_id: str):
    """Mark inbox item as read."""
    st = _update_item_state(item_id, {"read": True})
    return {"id": item_id, "read": True, "state": st}


@router.post("/{item_id}/unread")
def mark_unread(item_id: str):
    """Mark inbox item as unread."""
    st = _update_item_state(item_id, {"read": False})
    return {"id": item_id, "read": False, "state": st}


class TagsRequest(BaseModel):
    tags: list[str]
    mode: str = "set"   # "set" | "append"


@router.post("/{item_id}/tags")
def set_tags(item_id: str, req: TagsRequest):
    """Set or append extra tags to an inbox item."""
    if req.mode == "append":
        existing = _get_item_state(item_id).get("extra_tags", [])
        merged = list(existing)
        for t in req.tags:
            if t not in merged:
                merged.append(t)
        st = _update_item_state(item_id, {"extra_tags": merged})
    else:
        st = _update_item_state(item_id, {"extra_tags": list(req.tags)})
    return {"id": item_id, "extra_tags": st.get("extra_tags", []), "state": st}


class AssignProjectRequest(BaseModel):
    project_id: str
    project_name: Optional[str] = None


@router.post("/{item_id}/assign-project")
def assign_project(item_id: str, req: AssignProjectRequest):
    """Link inbox item to a project."""
    patch: dict[str, Any] = {"project_id": req.project_id}
    if req.project_name:
        patch["project_name"] = req.project_name
    # Also add category:projects tag hint
    existing = _get_item_state(item_id).get("extra_tags", [])
    if "category:projects" not in existing:
        existing = list(existing) + ["category:projects"]
    patch["extra_tags"] = existing
    st = _update_item_state(item_id, patch)
    return {"id": item_id, "project_id": req.project_id, "state": st}


# ---------------------------------------------------------------------------
# Routes — Structured Extraction
# ---------------------------------------------------------------------------

def _find_doc_body(item_id: str) -> Optional[str]:
    """Look up document body from VaultIndex by item_id. Returns None if not found."""
    idx = _get_index()
    if idx is None:
        return None
    for doc in idx.docs:
        fm = doc.frontmatter
        doc_id = str(fm.get("id") or doc.path.stem)
        if doc_id == item_id:
            return doc.content
    return None


class ExtractRequest(BaseModel):
    body: Optional[str] = None   # pass body directly (overrides vault lookup)
    force: bool = False           # ignore cache and re-extract


@router.post("/{item_id}/extract")
def extract_item(item_id: str, req: ExtractRequest = ExtractRequest()):
    """
    Run structured extraction on an inbox item.

    Returns:
        ExtractionResult dict with action_items, entities, intent, tone,
        reply_required, deadline, summary_one_line.

    Always returns 200. Falls back to regex if MLX unavailable.
    The result is cached by body sha256 and stored in item state.
    """
    from personal_assistant.mlx_server.tasks.extract import extract as _extract

    body = req.body or _find_doc_body(item_id)
    if not body:
        # No vault body — still run extraction on empty string (returns minimal result)
        body = ""

    result = _extract(body, force=req.force)

    # Persist extraction into item state so it's returned with _doc_to_item
    _update_item_state(item_id, {"extraction": result.to_dict()})

    return {"id": item_id, "extraction": result.to_dict(), "method": result.method}


@router.get("/{item_id}/extraction")
def get_extraction(item_id: str):
    """
    Return cached extraction for an item without re-running MLX.

    404 if no extraction exists yet — call POST /{item_id}/extract first.
    """
    st = _get_item_state(item_id)
    extraction = st.get("extraction")
    if extraction is None:
        raise HTTPException(404, "Extraction not available — call POST /extract first")
    return {"id": item_id, "extraction": extraction}


@router.delete("/extraction-cache")
def clear_extraction_cache():
    """Clear all extraction cache entries (useful after model upgrade)."""
    from personal_assistant.mlx_server.tasks.extract import clear_cache as _clear
    removed = _clear()
    return {"removed": removed, "status": "ok"}


# ---------------------------------------------------------------------------
# Routes — AI actions
# ---------------------------------------------------------------------------

class SummarizeRequest(BaseModel):
    item_id: str
    body: Optional[str] = None   # can pass body directly instead of looking up


@router.post("/summarize")
def summarize_item(req: SummarizeRequest):
    """
    Generate a TL;DR summary for an inbox item using the loaded MLX model.
    Returns {"summary": "..."} synchronously (non-streaming, short output).
    Falls back to extractive summary if model not available.
    """
    body = req.body

    if not body:
        idx = _get_index()
        if idx:
            for doc in idx.docs:
                fm = doc.frontmatter
                doc_id = str(fm.get("id") or doc.path.stem)
                if doc_id == req.item_id:
                    body = doc.content
                    break

    if not body:
        return {"summary": "Тело письма не найдено."}

    # Try MLX
    try:
        from personal_assistant.mlx_server import server as _srv
        engine = getattr(_srv.state, "engine", None)
        if engine and getattr(engine, "is_loaded", False):
            prompt = (
                "Кратко суммируй следующее письмо в 1-2 предложениях на русском. "
                "Укажи: кто написал, что просит и дедлайн (если есть). "
                "Начни с 'TL;DR:'\n\n"
                + body[:2000]
            )
            result = engine.generate(prompt, max_tokens=120, temperature=0.3)
            return {"summary": result.strip()}
    except Exception as e:
        logger.debug(f"[inbox] MLX summarize failed: {e}")

    # Extractive fallback: first 3 non-empty lines after frontmatter
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    snippet = " ".join(lines[:3])[:300]
    return {"summary": snippet or "Нет содержания."}


# ---------------------------------------------------------------------------
# Thread participant graph
# ---------------------------------------------------------------------------

@router.get("/thread/{thread_id}/graph")
def get_thread_graph(thread_id: str):
    """
    Build and return a participant graph for a mail thread.

    Returns:
      - participants: list of all senders/recipients with role + avatar
      - initiator: who started the thread
      - last_sender: most recent sender
      - my_turn: True if I haven't replied to the last message
      - days_without_reply: days since last external message (if my_turn)
      - timeline: chronologically ordered list of messages in thread

    Used by Inbox detail panel to show the thread participant section.
    """
    idx = _get_index()
    if idx is None:
        raise HTTPException(status_code=503, detail="Vault index не загружен")

    from personal_assistant.services.thread_graph_service import (
        build_thread_graph,
        graph_to_dict,
    )

    graph = build_thread_graph(
        thread_id=thread_id,
        docs=idx.docs,
        my_email=_get_my_email(),
        my_name=_get_my_name(),
    )

    if graph is None:
        raise HTTPException(
            status_code=404,
            detail=f"Тред {thread_id!r} не найден в vault"
        )

    return graph_to_dict(graph)


# ---------------------------------------------------------------------------
# Suggest meeting slots from email
# ---------------------------------------------------------------------------

def _suggest_meeting_slots(
    item_id: str,
    vault_path: Optional[Path],
    num_slots: int = 3,
) -> dict:
    """Rule-based meeting slot suggestion.

    Logic:
    1. Find the email doc in vault by item_id.
    2. Extract participants (recipients/cc) and subject from frontmatter.
    3. Scan vault/calendar/ for busy times in the next 14 days.
    4. Propose ``num_slots`` free slots at standard meeting hours
       (09:00, 10:00, 11:00, 14:00, 15:00, 16:00) skipping busy blocks.

    Returns:
        {
            item_id, title, participants, slots: [
                { start_iso, end_iso, display_str }
            ],
            busy_count,
        }
    """
    from datetime import timedelta

    from personal_assistant.services.calendar_service import _parse_frontmatter

    # ---- locate the email doc ------------------------------------------
    doc = None
    if vault_path and vault_path.exists():
        for section in ("mail", "threads"):
            section_dir = vault_path / section
            if not section_dir.exists():
                continue
            for md_path in section_dir.rglob("*.md"):
                if md_path.stem == item_id or md_path.stem.endswith(item_id):
                    doc = md_path
                    break
            if doc:
                break

    participants: list[str] = []
    subject_text = "Встреча"

    if doc:
        try:
            text = doc.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            subject_text = fm.get("subject") or fm.get("title") or "Встреча"
            # Collect sender + recipients + cc as participants
            sender = fm.get("sender", "")
            if sender:
                participants.append(sender)
            recips = fm.get("recipients") or fm.get("attendees") or []
            if isinstance(recips, str):
                recips = [r.strip() for r in recips.split(",") if r.strip()]
            participants.extend(recips)
            cc = fm.get("cc") or []
            if isinstance(cc, str):
                cc = [r.strip() for r in cc.split(",") if r.strip()]
            participants.extend(cc)
            # Deduplicate, strip my email
        except Exception:
            pass

    my_email = _get_my_email().lower()
    seen: set[str] = set()
    clean_parts: list[str] = []
    for p in participants:
        p = p.strip()
        if not p:
            continue
        key = p.lower()
        if key == my_email or key in seen:
            continue
        seen.add(key)
        clean_parts.append(p)
    participants = clean_parts[:10]

    # ---- scan busy calendar blocks in the next 14 days -----------------
    now_dt = datetime.now(timezone.utc)
    cutoff_dt = now_dt + timedelta(days=14)
    busy_intervals: list[tuple[datetime, datetime]] = []

    if vault_path and (vault_path / "calendar").exists():
        for md_path in (vault_path / "calendar").rglob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
                fm = _parse_frontmatter(text)
                date_str = str(fm.get("date") or fm.get("start") or "")
                if not date_str:
                    continue
                start = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if not (now_dt <= start <= cutoff_dt):
                    continue
                duration_min = int(fm.get("duration_minutes") or 60)
                end = start + timedelta(minutes=duration_min)
                busy_intervals.append((start, end))
            except Exception:
                continue

    # ---- propose free slots -------------------------------------------
    _PREFERRED_HOURS = [9, 10, 11, 14, 15, 16]
    _MEETING_DURATION = timedelta(hours=1)
    _SKIP_WEEKDAYS = {5, 6}  # Sat, Sun

    slots: list[dict] = []
    check_date = (now_dt + timedelta(days=1)).date()  # start from tomorrow

    _days_ru = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

    while len(slots) < num_slots and check_date <= cutoff_dt.date():
        if check_date.weekday() not in _SKIP_WEEKDAYS:
            for hour in _PREFERRED_HOURS:
                if len(slots) >= num_slots:
                    break
                slot_start = datetime(
                    check_date.year, check_date.month, check_date.day,
                    hour, 0, 0, tzinfo=timezone.utc
                )
                slot_end = slot_start + _MEETING_DURATION

                # Check no overlap with busy blocks
                overlap = any(
                    not (slot_end <= b_start or slot_start >= b_end)
                    for b_start, b_end in busy_intervals
                )
                if not overlap:
                    wd = _days_ru[slot_start.weekday()]
                    local_start = slot_start.astimezone()
                    display = (
                        f"{wd} {local_start.day:02d}.{local_start.month:02d} "
                        f"в {local_start.hour:02d}:00"
                    )
                    slots.append({
                        "start_iso": slot_start.isoformat(),
                        "end_iso": slot_end.isoformat(),
                        "display_str": display,
                    })
        check_date += timedelta(days=1)

    return {
        "item_id": item_id,
        "title": f"Встреча: {subject_text}",
        "participants": participants,
        "slots": slots,
        "busy_count": len(busy_intervals),
        "doc_found": doc is not None,
    }


@router.post("/{item_id}/suggest-meeting")
def suggest_meeting(item_id: str):
    """
    Suggest meeting slots based on email participants and calendar availability.

    Scans vault/calendar/ for existing events in the next 14 days,
    then proposes 3 free slots at standard meeting hours (Mon–Fri, 09–17).

    Returns:
        {
            item_id: str,
            title: str,          — suggested meeting title (from email subject)
            participants: [str], — extracted from email frontmatter
            slots: [
                { start_iso, end_iso, display_str }
            ],
            busy_count: int,     — number of calendar events found
            doc_found: bool,     — whether the email doc was located in vault
        }
    """
    vault_path = _get_vault_path()

    try:
        result = _suggest_meeting_slots(item_id=item_id, vault_path=vault_path)
    except Exception as exc:
        logger.warning(f"[inbox] suggest_meeting failed for {item_id!r}: {exc}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации слотов: {exc}")

    return result
