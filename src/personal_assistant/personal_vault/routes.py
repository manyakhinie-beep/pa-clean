"""
FastAPI routes for PersonalVault.

Endpoints:
  POST /api/v1/vault/items          — create item (and optional thread)
  GET  /api/v1/vault/items          — list items
  GET  /api/v1/vault/threads        — list threads
  GET  /api/v1/vault/threads/{tid}  — get thread with items
  GET  /api/v1/vault/threads/{tid}/item/{idx} — get N-th item in thread
  POST /api/v1/vault/context        — build AI context
  DELETE /api/v1/vault/threads/{tid} — delete thread
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger

from personal_assistant.personal_vault.context import build_context
from personal_assistant.personal_vault.db import (
    create_thread,
    delete_thread,
    ensure_thread,
    get_item,
    get_item_by_index,
    get_thread,
    insert_item,
    list_items,
    list_threads,
    update_thread_participants,
)
from personal_assistant.personal_vault.models import (
    ContextRequest,
    ContextResponse,
    Thread,
    VaultItem,
)

router = APIRouter(prefix="/api/v1/vault")


def _normalize_tid(tid: Optional[str]) -> Optional[str]:
    if not tid:
        return None
    tid = tid.strip()
    return tid if tid else None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _virtual_thread_from_vault(tid: str) -> Optional[Thread]:
    """Build a mail thread from Markdown vault docs when SQLite has no row."""
    try:
        from personal_assistant.mlx_server.server import state  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - defensive during import cycles
        logger.debug(f"[personal_vault] vault fallback unavailable: {exc}")
        return None

    index = getattr(state, "index", None)
    docs = getattr(index, "docs", []) if index is not None else []
    matches = [
        d
        for d in docs
        if d.section == "mail" and str(d.frontmatter.get("thread_id") or "") == tid
    ]
    if not matches:
        return None

    matches.sort(key=lambda d: d.date or "")
    items: list[VaultItem] = []
    participants: list[str] = []

    for doc in matches:
        fm = doc.frontmatter
        sender_email = str(fm.get("from") or doc.sender_email or "").strip() or None
        sender = (
            str(fm.get("sender_name") or "").strip()
            or sender_email
            or str(fm.get("sender") or "").strip()
            or "unknown"
        )
        if sender and sender not in participants:
            participants.append(sender)
        if sender_email and sender_email not in participants:
            participants.append(sender_email)

        subject = str(fm.get("title") or fm.get("subject") or doc.title).strip()
        date_iso = str(doc.date or fm.get("created") or "unknown")
        items.append(
            VaultItem(
                id=str(fm.get("message_id") or doc.path),
                item_type="email",
                thread_id=tid,
                subject=subject or doc.path.stem,
                sender=sender,
                sender_email=sender_email,
                recipients=_as_list(fm.get("recipients")),
                full_body=doc.content,
                date_iso=date_iso,
                metadata={
                    "path": str(doc.path),
                    "source": str(fm.get("source") or ""),
                    "mailbox": str(fm.get("mailbox") or ""),
                },
            )
        )

    root_subject = items[0].subject if items else tid
    return Thread(id=tid, root_subject=root_subject, items=items, participants=participants)


@router.post("/items")
def create_item(item: VaultItem):
    """Persist a new item. Auto-creates thread row when needed.

    - If ``thread_id`` is absent: generates a new thread and assigns its ID.
    - If ``thread_id`` is provided: ensures the thread row exists (creates it
      if missing) so that ``GET /threads/{tid}`` always returns 200.
    """
    if not item.thread_id:
        item.thread_id = create_thread(
            root_subject=item.subject,
            participants=[item.sender],
        )
    else:
        # BUG-FIX: ensure thread row exists for the supplied thread_id so that
        # GET /api/v1/vault/threads/{tid} does not return 404.
        ensure_thread(
            tid=item.thread_id,
            root_subject=item.subject,
            participants=[item.sender],
        )
        # BUG-FIX: merge sender + sender_email into thread participants so
        # that every contributor is reflected in thread metadata.
        new_parts = [p for p in [item.sender, item.sender_email] if p]
        update_thread_participants(item.thread_id, new_parts)
    existing = get_item(item.id)
    if existing:
        raise HTTPException(409, f"Item {item.id} already exists")
    insert_item(item)
    logger.debug(f"[personal_vault] created item {item.id} in thread {item.thread_id}")
    return {"ok": True, "item_id": item.id, "thread_id": item.thread_id}


@router.get("/items")
def read_items(
    item_type: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 500,
):
    """List items with optional filters."""
    items = list_items(item_type=item_type, thread_id=thread_id, limit=limit)
    return {"items": [i.serialize() for i in items], "total": len(items)}


@router.get("/threads")
def read_threads(limit: int = 50):
    """List recent threads."""
    threads = list_threads(limit=limit)
    return {"threads": [t.serialize() for t in threads if t], "total": len(threads)}


@router.get("/threads/{tid}")
@router.get("//threads/{tid}", include_in_schema=False)
def read_thread(tid: str):
    """Get a full thread with ordered items."""
    normalized = _normalize_tid(tid)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    t = get_thread(normalized)
    if t is None:
        t = _virtual_thread_from_vault(normalized)
    if t is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return t.serialize()


@router.get("/threads/{tid}/item/{idx}")
@router.get("//threads/{tid}/item/{idx}", include_in_schema=False)
def read_thread_item(tid: str, idx: int):
    """Get the N-th item (0-based) inside a thread ordered by date."""
    normalized = _normalize_tid(tid)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if get_thread(normalized) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    item = get_item_by_index(normalized, idx)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item at index {idx} not found")
    return item.serialize()


@router.post("/context")
def assemble_context(req: ContextRequest):
    """Build MLX-ready system prompt + messages from a thread.

    If thread_id is not provided but query is, returns an empty context
    placeholder so the caller can proceed without crashing.
    """
    if not req.thread_id:
        # Graceful fallback: no thread selected yet
        return ContextResponse(
            thread_id="",
            system_prompt="",
            messages=[],
            total_chars=0,
        )
    normalized = _normalize_tid(req.thread_id)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if get_thread(normalized) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        ctx = build_context(normalized, mode=req.mode, max_chars=req.max_chars)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextResponse(
        thread_id=normalized,
        system_prompt=str(ctx["system_prompt"]),
        messages=list(ctx["messages"]),
        total_chars=int(ctx["total_chars"]),
    )


@router.delete("/threads/{tid}")
@router.delete("//threads/{tid}", include_in_schema=False)
def remove_thread(tid: str):
    """Delete a thread and all its items (cascade)."""
    normalized = _normalize_tid(tid)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if not delete_thread(normalized):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"ok": True, "thread_id": normalized}
