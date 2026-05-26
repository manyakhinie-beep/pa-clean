"""
Chat API v2 — thread-aware, context-rich, tool-enabled.

Endpoints:
  POST /api/chat/send        — streaming chat turn
  GET  /api/chat/threads     — list threads
  GET  /api/chat/history/{tid} — messages for thread
  POST /api/chat/clear/{tid} — clear thread messages
  DELETE /api/chat/{tid}     — delete thread
"""

from __future__ import annotations

import json
import re
from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from personal_assistant.mlx_server.chat_db import (
    add_message,
    clear_thread,
    create_thread,
    delete_thread,
    get_messages,
    get_thread,
    list_threads,
    update_thread_title,
)
from personal_assistant.mlx_server.tools.executor import execute_tool_sync
from personal_assistant.profile.context_assembler import ProfileAwareAssembler
from personal_assistant.utils.timezone import format_to_msk_iso

router = APIRouter(prefix="/api/chat")

# Detect tool call markers (only the *tag* — JSON is extracted separately)
_STANDARD_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_GIGACHAT_TAG_RE = re.compile(r"<\|function_call\|>\s*(\{)", re.DOTALL)


def _extract_json_object(text: str, start: int = 0) -> Optional[str]:
    """
    Extract the first well-formed JSON object beginning at text[start:].

    Uses a brace-counting parser so it correctly handles nested objects
    regardless of what follows the closing '}'.  Returns None if no
    complete object is found.
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatSendRequest(BaseModel):
    thread_id: Optional[str] = Field(
        None, description="Existing thread ID or null for new"
    )
    message: str = Field(..., min_length=1, description="User message text")
    context_paths: list[str] = Field(
        default_factory=list, description="Vault @mention paths"
    )
    mode: str = Field("chat", description="chat | search | summarize | draft")
    max_tokens: int = Field(1024, ge=64, le=4096)
    vault_thread_id: Optional[str] = Field(
        None, description="PersonalVault thread ID to inject as context"
    )
    reply_message_id: Optional[str] = Field(
        None, description="Source inbox item ID for reply context (BUG-3 fix)"
    )

    @field_validator("thread_id", "vault_thread_id", "reply_message_id")
    @classmethod
    def _strip_thread_id(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return None
        return v


class ChatClearRequest(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_engine():
    from personal_assistant.mlx_server.server import state

    if state.engine is None:
        from personal_assistant.mlx_server.engine import MLXEngine

        state.engine = MLXEngine()
    return state.engine


def _normalize_chat_tid(tid: Optional[str]) -> Optional[str]:
    """Normalize a chat thread_id: strip whitespace, reject empty."""
    if not tid:
        return None
    tid = tid.strip()
    return tid if tid else None


def _detect_and_run_tools(text: str) -> Optional[str]:
    """
    Detect a tool call in *text* and execute it, returning the result string.

    Supports two formats:
      Standard:  <tool_call>{"name":...}</tool_call>
      GigaChat:  <|function_call|>{"name":...}  (no closing tag)

    Uses brace-counting JSON extraction so trailing text / nesting never
    causes a missed match.  Returns None when no tool call is found.
    On execution failure returns a structured error string (not None) so
    the model can retry rather than the pipeline crashing silently.
    """
    raw_json: Optional[str] = None

    # Strategy 1: standard <tool_call>...</tool_call>
    m1 = _STANDARD_TAG_RE.search(text)
    if m1:
        raw_json = m1.group(1)

    # Strategy 2: GigaChat <|function_call|>{json}
    if raw_json is None:
        m2 = _GIGACHAT_TAG_RE.search(text)
        if m2:
            # m2.start(1) points to the opening '{' — extract the full object
            raw_json = _extract_json_object(text, start=m2.start(1))
            if raw_json is None:
                logger.warning("[chat] <|function_call|> found but JSON object is incomplete")
                return None

    if raw_json is None:
        return None

    logger.debug(f"[chat] tool call detected: {raw_json[:120]}")
    try:
        raw_call = json.loads(raw_json)
    except Exception as exc:
        logger.warning(f"[chat] failed to parse tool call JSON: {exc} | raw={raw_json[:120]}")
        return f"[Tool call parsing failed: {exc}]"

    result = execute_tool_sync(raw_call)
    if not result["ok"]:
        schema_hint = result.get("expected_schema")
        err_msg = (
            f"[Tool execution failed] {result['error']}. "
            f"Correct schema: {json.dumps(schema_hint, ensure_ascii=False) if schema_hint else 'see registry'}. "
            "Retry with valid arguments."
        )
        return err_msg
    return result["result"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/send")
def chat_send(req: ChatSendRequest):
    """
    Main streaming endpoint.
    Pipeline:
      1. Get or create thread
      2. Save user message
      3. Build context (profile + config + vault + history)
      4. Stream MLX generation
      5. Detect tool calls → execute → re-generate if needed
      6. Save assistant message
    """
    engine = _get_engine()
    assembler = ProfileAwareAssembler()

    # 1. Thread validation & creation
    incoming_tid = _normalize_chat_tid(req.thread_id)
    if incoming_tid:
        thread = get_thread(incoming_tid)
        if thread is None:
            logger.warning(f"[chat] thread not found: {incoming_tid!r}")
            raise HTTPException(status_code=404, detail="Thread not found")
        tid = thread.id
    else:
        thread = create_thread(
            title=req.message[:40] + "…" if len(req.message) > 40 else req.message
        )
        tid = thread.id

    # 2. Persist user message
    add_message(tid, "user", req.message)

    # 3. Load history from DB
    db_msgs = get_messages(tid, limit=50)
    history = [{"role": m.role, "content": m.content} for m in db_msgs]

    # 4. Assemble context
    try:
        ctx = assembler.build(
            user_message=req.message,
            history=history,
            context_paths=req.context_paths,
            mode=req.mode,
            vault_thread_id=req.vault_thread_id,
            reply_message_id=req.reply_message_id,
        )
    except Exception as exc:
        logger.exception(f"[chat] context assembly failed: {exc}")
        raise HTTPException(status_code=500, detail="Context assembly failed") from exc

    system = ctx["system_prompt"]
    messages = ctx["messages"]

    # Update thread title if still default
    if thread and thread.title in ("", "Новый чат"):
        update_thread_title(tid, req.message[:60])

    def token_stream() -> Iterator[str]:
        full_text = ""
        try:
            logger.debug(f"[chat] generation start: tid={tid} msgs={len(messages)}")

            # Pass 1: stream and yield each chunk immediately for low-latency UI.
            # Buffer full_text for post-generation tool-call detection.
            for chunk in engine.stream(
                messages=messages,
                system=system,
                max_tokens=req.max_tokens,
            ):
                full_text += chunk
                yield chunk  # stream to browser immediately

            # Tool call detection (runs after full response is buffered)
            tool_result = _detect_and_run_tools(full_text)
            if tool_result:
                logger.info(
                    f"[chat] re-generating after tool result: {tool_result[:80]}"
                )
                messages.append({"role": "assistant", "content": full_text})
                messages.append({"role": "tool", "content": tool_result})
                full_text = ""
                for chunk in engine.stream(
                    messages=messages,
                    system=system,
                    max_tokens=req.max_tokens,
                ):
                    full_text += chunk
                    yield chunk

            # Persist assistant message
            add_message(tid, "assistant", full_text)
            logger.debug(f"[chat] turn complete: tid={tid} chars={len(full_text)}")

        except Exception as exc:
            logger.exception(f"[chat] stream error: {exc}")
            yield f"\n\n[Ошибка генерации: {exc}]"

    # Return X-Thread-ID so the JS can bind the new thread without a round-trip.
    msk_ts = format_to_msk_iso()
    return StreamingResponse(
        token_stream(),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-MSK-Timestamp": msk_ts,
            "X-Thread-ID": tid,
            # Expose header to browser JS (CORS preflight)
            "Access-Control-Expose-Headers": "X-Thread-ID, X-MSK-Timestamp",
        },
    )


@router.get("/threads")
def chat_threads(limit: int = 50):
    """List recent chat threads with message count."""
    from personal_assistant.mlx_server.chat_db import message_count  # noqa: PLC0415
    rows = list_threads(limit=limit)
    return {
        "threads": [
            {
                "id": r.id,
                "title": r.title,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "message_count": message_count(r.id),
            }
            for r in rows
        ]
    }


@router.get("/related")
def chat_related(thread_id: Optional[str] = None, q: Optional[str] = None):
    """Return entities related to a chat thread for the Связи side panel.

    Searches vault index for contacts, projects, calendar events and mail
    threads that match either the thread title or an explicit query string.
    Returns at most 2 items per category.
    """
    from personal_assistant.mlx_server.server import state  # noqa: PLC0415

    # Determine search query: thread title → explicit q → empty
    search_q = q or ""
    if not search_q and thread_id:
        t = get_thread(thread_id)
        if t:
            # Strip common prefixes like "Re:", "Fwd:", quotes
            search_q = re.sub(r'^(re:|fwd?:|«|»)', '', t.title, flags=re.IGNORECASE).strip()

    result: dict = {
        "contacts": [],
        "projects": [],
        "threads": [],
        "events": [],
    }

    index = getattr(state, "index", None)
    if index is None or not search_q:
        return result

    docs = getattr(index, "docs", [])

    def _score(doc, keywords: list[str]) -> int:
        txt = (doc.title + " " + doc.content[:200]).lower()
        return sum(1 for kw in keywords if kw and kw.lower() in txt)

    keywords = [w for w in re.split(r'[\s,«»"\']+', search_q) if len(w) > 2]

    contacts_raw, projects_raw, threads_raw, events_raw = [], [], [], []
    for doc in docs:
        sc = _score(doc, keywords)
        if sc == 0:
            continue
        section = doc.section or ""
        if section == "contacts":
            contacts_raw.append((sc, doc))
        elif section == "mail":
            threads_raw.append((sc, doc))
        elif section == "calendar":
            events_raw.append((sc, doc))

    # Also pull from structured projects
    try:
        from personal_assistant.webui.routes import _load_projects  # noqa: PLC0415
        projs = _load_projects()
        for p in projs:
            name = (p.get("name") or "").lower()
            desc = (p.get("description") or "").lower()
            sc = sum(1 for kw in keywords if kw and (kw.lower() in name or kw.lower() in desc))
            if sc > 0:
                projects_raw.append((sc, p))
    except Exception:
        pass

    def _top(lst, n=2):
        return [x for _, x in sorted(lst, key=lambda t: -t[0])[:n]]

    for doc in _top(contacts_raw):
        fm = doc.frontmatter or {}
        result["contacts"].append({
            "name": fm.get("name") or doc.title,
            "role": fm.get("role") or fm.get("title") or "",
            "email": fm.get("email") or fm.get("emails", [""])[0] if isinstance(fm.get("emails"), list) else fm.get("email") or "",
            "path": str(doc.path),
        })

    for doc in _top(threads_raw):
        fm = doc.frontmatter or {}
        result["threads"].append({
            "subject": fm.get("subject") or doc.title,
            "sender": fm.get("sender") or fm.get("from") or "",
            "date": doc.date or "",
            "thread_id": fm.get("thread_id") or "",
            "path": str(doc.path),
        })

    for doc in _top(events_raw):
        fm = doc.frontmatter or {}
        result["events"].append({
            "title": doc.title,
            "date": doc.date or fm.get("date") or "",
            "location": fm.get("location") or "",
            "path": str(doc.path),
        })

    for proj in _top(projects_raw):
        goals = proj.get("goals") or []
        done = sum(1 for g in goals if g.get("done"))
        result["projects"].append({
            "id": proj.get("id", ""),
            "name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "deadline": proj.get("deadline") or "",
            "goals_total": len(goals),
            "goals_done": done,
        })

    return result


@router.get("/history/{thread_id}")
def chat_history(thread_id: str, limit: int = 100):
    """Fetch message history for a thread."""
    tid = _normalize_chat_tid(thread_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if get_thread(tid) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    msgs = get_messages(tid, limit=limit)
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in msgs
        ]
    }


@router.post("/clear/{thread_id}")
def chat_clear(thread_id: str):
    """Clear all messages in a thread (keep the thread itself)."""
    tid = _normalize_chat_tid(thread_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if get_thread(tid) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    clear_thread(tid)
    return {"ok": True, "thread_id": tid}


@router.delete("/threads/all")
def chat_delete_all():
    """Delete every chat thread and all their messages."""
    from personal_assistant.mlx_server.chat_db import delete_all_threads
    count = delete_all_threads()
    return {"ok": True, "deleted": count}


@router.delete("/{thread_id}")
def chat_delete(thread_id: str):
    """Delete a thread and all its messages."""
    tid = _normalize_chat_tid(thread_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if not delete_thread(tid):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"ok": True, "thread_id": tid}


# ---------------------------------------------------------------------------
# Save draft helpers (shared)
# ---------------------------------------------------------------------------


def _build_save_draft_script(
    app_name: str,
    subject: str,
    body_file_path: str,
    to_recipients: list[str],
    reply_to_message_id: Optional[str],
) -> str:
    """Build AppleScript that opens a pre-filled draft compose window in Outlook.

    Design constraints that drove every decision here:

    BODY  — never embed as string literal (newlines cause -2741; no backslash
            escaping in AS string literals).  Instead use ``do shell script``
            to read the temp file via ``cat``.  ``do shell script`` works both
            inside and outside a ``tell application`` block because Outlook has
            no command by that name, so Standard Additions handles it.

    SUBJECT / RECIPIENTS — short, predictable strings.  Embedded as string
            literals after (a) collapsing \\r/\\n to space, (b) doubling \\,
            (c) replacing " with ``" & quote & "``.

    REPLY THREADING — NOT attempted via AppleScript.  ``first message of inbox
            whose internet message id = X`` is a multi-word ``whose``-clause
            reference that reliably causes -2741 compile errors across Outlook
            versions because ``message`` and ``internet`` are dictionary class
            names.  The caller already prefixes the subject with "Re:" so the
            compose window clearly signals the reply context.

    OPEN vs SAVE — ``save msg`` on a new outgoing message raises -1701 in
            Outlook 16.x.  ``open msg`` opens the compose window; user presses
            Cmd+S or clicks Send.
    """

    def _esc(s: str) -> str:
        """Collapse whitespace and escape for an AppleScript string literal.

        Newlines (\\r, \\n) and tabs in the string would break single-line
        AS string literals and produce -2741.  Collapse them to spaces first.
        Then: double backslashes, replace " with quote-concatenation.
        """
        s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        s = " ".join(s.split())  # collapse multiple spaces
        return s.replace("\\", "\\\\").replace('"', '" & quote & "')

    esc_subject = _esc(subject)

    # Build recipients block — 4-space indent to match tell body level.
    recipients_block = ""
    if to_recipients:
        lines = []
        for addr in to_recipients[:10]:
            esc_addr = _esc(addr)
            lines.append(
                f'    make new recipient at end of to recipients of msg '
                f'with properties {{email address:{{address:"{esc_addr}"}}}}'
            )
        recipients_block = "\n".join(lines)

    if reply_to_message_id:
        # Strip RFC 2822 header-folding whitespace from the message-id.
        # We embed it as a string literal in the script, so any \r\n
        # inside would break the line and cause -2741.
        clean_mid = re.sub(r"\s+", "", reply_to_message_id)
        esc_mid = _esc(clean_mid)

        # WHY repeat-loop instead of "whose internet message id = X":
        #   The multi-word whose-clause property "internet message id" causes
        #   a -2741 compile error because "message" is a class-name token in
        #   Outlook's dictionary.  Accessing the property directly on each
        #   object inside a repeat loop has no such ambiguity.
        return f"""\
set bodyContent to do shell script "cat " & quoted form of "{body_file_path}"
tell application "{app_name}"
    set targetId to "{esc_mid}"
    set origMsg to missing value
    try
        repeat with checkMsg in (messages of inbox)
            if (internet message id of checkMsg) = targetId then
                set origMsg to checkMsg
                exit repeat
            end if
        end repeat
    end try
    if origMsg is not missing value then
        set msg to reply origMsg opening window false
    else
        set msg to make new outgoing message
    end if
    set subject of msg to "{esc_subject}"
    set content of msg to bodyContent
{recipients_block}
    open msg
end tell
"""

    return f"""\
set bodyContent to do shell script "cat " & quoted form of "{body_file_path}"
tell application "{app_name}"
    set msg to make new outgoing message
    set subject of msg to "{esc_subject}"
    set content of msg to bodyContent
{recipients_block}
    open msg
end tell
"""


# ---------------------------------------------------------------------------
# Apple Mail draft
# ---------------------------------------------------------------------------


def _resolve_reply_message_id(vault_item_id: str) -> Optional[str]:
    """Look up a vault mail file by *vault_item_id* and return its Mail.app message_id.

    *vault_item_id* is typically the file stem (e.g. ``msg_q2_001``) or the
    ``id`` frontmatter field.  We scan ``vault/mail/**/*.md`` and match on:

    1. ``id`` frontmatter field == *vault_item_id*
    2. file stem == *vault_item_id*

    Returns the ``message_id`` frontmatter value (Mail.app internal integer ID)
    or ``None`` if no match is found.

    Uses the lenient frontmatter parser so legacy vault entries with run-on
    YAML still resolve — without this, the draft silently fell through to
    "new outgoing message" instead of threading into the existing reply.
    """
    from personal_assistant.config import settings
    from personal_assistant.utils.frontmatter import parse_lenient

    mail_root = settings.vault_path / "mail"
    if not mail_root.exists():
        return None

    needle = vault_item_id.strip()
    for md_file in mail_root.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            fm = parse_lenient(text)
            file_stem = md_file.stem
            doc_id = str(fm.get("id") or file_stem).strip()
            if doc_id == needle:
                mid = str(fm.get("message_id") or "").strip()
                return mid or None
        except Exception:
            continue
    return None


class SaveDraftMailRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=500, description="Email subject")
    body: str = Field(..., min_length=1, description="Draft body text")
    to_recipients: list[str] = Field(default_factory=list, description="To: addresses")
    cc_recipients: list[str] = Field(default_factory=list, description="CC: addresses")
    reply_to_message_id: Optional[str] = Field(
        None, description="RFC 2822 Message-ID of the original message (for reply threading)"
    )
    save_to_drafts: Optional[bool] = Field(
        None,
        description=(
            "True = save silently to Drafts; False = open compose window; "
            "None = fall back to the mail_auto_draft setting"
        ),
    )


def _build_save_draft_mail_script(
    subject: str,
    body_file_path: str,
    to_recipients: list[str],
    cc_recipients: list[str],
    reply_to_message_id: Optional[str],
    save_to_drafts: bool = False,
) -> str:
    """Build AppleScript for Apple Mail: open compose window or save directly to Drafts.

    Design decisions:
      BODY      — read from temp file via ``do shell script "cat ..."`` to avoid
                  embedding arbitrary text as an AppleScript string literal (avoids -2741).
      THREADING — when ``reply_to_message_id`` is provided, we look up the original
                  message with a repeat loop (avoids "whose" multi-word clauses that
                  trigger -2741 because "message" is a class-name token in Mail's dict).
                  ``reply origMsg opening window false/true`` preserves RFC 2822
                  In-Reply-To / References headers automatically.
      CC        — added via ``make new cc recipient`` on the compose object.
      TO        — when ``to_recipients`` is provided, they are added explicitly; in
                  reply mode Mail auto-adds the original sender, so explicit To is
                  additive (duplicates are harmless — Mail deduplicates on send).
      SAVE MODE — ``save_to_drafts=True``: ``opening window false`` + ``save newMsg``
                  saves the message directly to Drafts without showing a compose window.
                  ``save_to_drafts=False``: ``opening window true`` / ``visible:true``
                  opens the compose window for the user to review and send.
    """

    def _esc(s: str) -> str:
        """Collapse whitespace and escape for an AppleScript string literal."""
        s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        s = " ".join(s.split())
        # Backslash has no special meaning in AppleScript double-quoted strings;
        # only double-quote itself needs escaping (via concatenation with quote constant).
        return s.replace('"', '" & quote & "')

    esc_subject = _esc(subject)
    visible_flag = "false" if save_to_drafts else "true"

    def _make_to_block(addrs: list[str]) -> str:
        lines = [
            f'    make new to recipient at end of to recipients of newMsg '
            f'with properties {{address:"{_esc(a)}"}}'
            for a in addrs[:10]
        ]
        return "\n".join(lines)

    def _make_cc_block(addrs: list[str]) -> str:
        lines = [
            f'    make new cc recipient at end of cc recipients of newMsg '
            f'with properties {{address:"{_esc(a)}"}}'
            for a in addrs[:10]
        ]
        return "\n".join(lines)

    to_block = _make_to_block(to_recipients)
    cc_block = _make_cc_block(cc_recipients)
    extra_recipients = "\n".join(filter(None, [to_block, cc_block]))

    # activate only when opening compose window — silent Drafts save doesn't need it
    activate_line = "    activate" if not save_to_drafts else ""

    if reply_to_message_id:
        clean_mid = re.sub(r"\s+", "", reply_to_message_id)
        esc_mid = _esc(clean_mid)

        if save_to_drafts:
            # Silent save: reply with window closed, then save newMsg
            found_branch = f"""\
        set newMsg to reply origMsg opening window false
        set content of newMsg to bodyContent
        set subject of newMsg to "{esc_subject}"
"""
            notfound_branch = f"""\
        set newMsg to make new outgoing message with properties {{subject:"{esc_subject}", visible:false}}
        set content of newMsg to bodyContent
"""
            extra_block = (extra_recipients + "\n") if extra_recipients.strip() else ""
            tail = f"{extra_block}    save newMsg"
        else:
            # Open compose window:
            # - found: reply origMsg opening window true  → already shows window
            # - not found: make new + open newMsg
            found_branch = f"""\
        set newMsg to reply origMsg opening window true
        set content of newMsg to bodyContent
        set subject of newMsg to "{esc_subject}"
"""
            notfound_branch = f"""\
        set newMsg to make new outgoing message with properties {{subject:"{esc_subject}", visible:true}}
        set content of newMsg to bodyContent
        open newMsg
"""
            extra_block = (extra_recipients + "\n") if extra_recipients.strip() else ""
            tail = extra_block.rstrip()

        return f"""\
set bodyContent to do shell script "cat " & quoted form of "{body_file_path}"
tell application "Mail"
{activate_line}
    set targetId to "{esc_mid}"
    set origMsg to missing value
    try
        repeat with anAccount in every account
            try
                repeat with mbox in mailboxes of anAccount
                    try
                        repeat with checkMsg in (messages of mbox)
                            if (message id of checkMsg) as string = targetId then
                                set origMsg to checkMsg
                                exit repeat
                            end if
                        end repeat
                    end try
                    if origMsg is not missing value then exit repeat
                end repeat
            end try
            if origMsg is not missing value then exit repeat
        end repeat
    end try
    if origMsg is not missing value then
{found_branch}    else
{notfound_branch}    end if
    {tail}
end tell
"""

    # No threading — plain new outgoing message
    if save_to_drafts:
        final_action = "save newMsg"
    else:
        final_action = "open newMsg"

    return f"""\
set bodyContent to do shell script "cat " & quoted form of "{body_file_path}"
tell application "Mail"
{activate_line}
    set newMsg to make new outgoing message with properties {{subject:"{esc_subject}", visible:{visible_flag}}}
    set content of newMsg to bodyContent
{extra_recipients}
    {final_action}
end tell
"""


@router.post("/save-draft-mail")
def save_draft_mail(req: SaveDraftMailRequest):
    """
    Create a draft email in Apple Mail via AppleScript.

    - ``save_to_drafts=false`` (default): opens the compose window so the user
      can review, edit, and send (Cmd+S saves to Drafts, Cmd+Return sends).
    - ``save_to_drafts=true``: saves silently to the Drafts mailbox without
      opening a compose window.

    macOS-only. Requires Automation permission for Mail.app
    (System Settings → Privacy & Security → Automation).
    """
    import os
    import platform
    import tempfile

    from personal_assistant.config import settings
    from personal_assistant.services.mail_service import resolve_save_to_drafts

    # Honour the mail_auto_draft setting when the caller didn't specify.
    save = resolve_save_to_drafts(req.save_to_drafts)

    # Test mode: never touch Mail.app — simulate success for scenario/e2e tests.
    if settings.e2e_test_mode:
        logger.info(f"[chat] e2e_test_mode: skipped real Mail draft {req.subject!r}")
        return {
            "ok": True,
            "message": "e2e_test_mode: черновик не создавался (тестовый режим)",
            "e2e": True,
        }

    if platform.system() != "Darwin":
        raise HTTPException(
            status_code=501,
            detail="Save to Mail draft is only supported on macOS",
        )

    try:
        from personal_assistant.readers.applescript_base import run_applescript
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail=f"AppleScript unavailable: {exc}"
        ) from exc

    # Resolve reply_to_message_id: if it's a vault item ID (file stem or 'id'
    # frontmatter) rather than a Mail.app internal integer ID, look up the
    # vault doc and extract the real message_id.
    resolved_reply_id = req.reply_to_message_id
    if resolved_reply_id and not resolved_reply_id.strip().isdigit():
        resolved_reply_id = _resolve_reply_message_id(resolved_reply_id.strip())

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="pa_draft_mail_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(req.body)

        script = _build_save_draft_mail_script(
            subject=req.subject,
            body_file_path=tmp_path,
            to_recipients=req.to_recipients,
            cc_recipients=req.cc_recipients,
            reply_to_message_id=resolved_reply_id,
            save_to_drafts=save,
        )

        action = "saved to Drafts" if save else "opened"
        msg_ru = (
            "Черновик сохранён в папке Черновики Mail"
            if save
            else "Черновик открыт в Mail — нажмите Cmd+S для сохранения или Cmd+Return для отправки"
        )
        try:
            run_applescript(script, timeout=30)
            logger.info(f"[chat] Mail draft {action}: {req.subject!r}")
            return {"ok": True, "message": msg_ru}
        except Exception as exc:
            logger.error(f"[chat] Mail draft {action} failed: {exc}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create draft in Mail: {exc}",
            ) from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Mail message metadata — for pre-populating the draft edit panel
# ---------------------------------------------------------------------------


@router.get("/mail/message-meta")
def get_mail_message_meta(message_id: str):
    """Return metadata for a vault mail file matching *message_id*.

    Scans vault/mail/**/*.md for a file whose ``message_id`` frontmatter field
    matches the given value.  Used by the frontend to pre-populate the draft
    edit panel (To, CC, Subject fields) when composing a reply.

    Returns:
        ``{message_id, subject, sender_email, sender_name, recipients, cc, thread_id}``
        or 404 if not found.
    """
    import yaml

    from personal_assistant.config import settings

    mail_root = settings.vault_path / "mail"
    if not mail_root.exists():
        raise HTTPException(status_code=404, detail="Vault mail directory not found")

    clean_id = message_id.strip()

    def _parse_fm(text: str) -> dict:
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 3)
        if end == -1:
            return {}
        try:
            return yaml.safe_load(text[3:end].strip()) or {}
        except Exception:
            return {}

    for md_file in mail_root.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            fm = _parse_fm(text)
            file_stem = md_file.stem
            doc_id = str(fm.get("id") or file_stem).strip()
            msg_id = str(fm.get("message_id") or "").strip()
            # Match by Mail.app message_id, vault item id, or file stem
            if msg_id != clean_id and doc_id != clean_id:
                continue
            # Vault mail docs store sender as "sender:" field in several formats:
            #   "Name <email>"           — angle-bracket format
            #   "[[contacts/email]]"     — wiki-link format (vault default)
            #   "email"                  — plain email
            import re as _re
            sender_raw = str(fm.get("sender", fm.get("from", "")))
            _m_angle = _re.search(r'<([^>]+@[^>]+)>', sender_raw)
            _m_wiki  = _re.search(r'\[\[contacts/([^\]]+@[^\]]+)\]\]', sender_raw)
            if _m_angle:
                sender_email = _m_angle.group(1).strip()
            elif _m_wiki:
                sender_email = _m_wiki.group(1).strip()
            else:
                # Plain text — fall back to the 'from:' field if sender looks like a wiki-link
                plain = sender_raw.strip()
                if "[[" in plain or not plain:
                    plain = str(fm.get("from", "")).strip()
                sender_email = plain
            # Normalise recipients / cc — may be str or list in vault frontmatter
            def _to_list(val) -> list:
                if not val:
                    return []
                if isinstance(val, list):
                    return [str(v).strip() for v in val if v]
                # comma-separated string
                return [s.strip() for s in str(val).split(',') if s.strip()]
            return {
                "message_id": msg_id or doc_id,
                "subject": fm.get("title", fm.get("subject", "")),
                "sender_email": sender_email,
                "sender_name": fm.get("sender_name", ""),
                "recipients": _to_list(fm.get("recipients")),
                "cc": _to_list(fm.get("cc")),
                "thread_id": fm.get("thread_id", ""),
            }
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Message not found: {message_id!r}")


# ---------------------------------------------------------------------------
# Mail service — thread summarisation
# ---------------------------------------------------------------------------


class SummarizeMailThreadRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, description="Thread ID as in vault frontmatter")
    max_tokens: int = Field(768, ge=64, le=2048)


@router.post("/mail/summarize-thread")
def summarize_mail_thread(req: SummarizeMailThreadRequest):
    """Summarise an email thread from the vault using the local MLX model."""
    try:
        from personal_assistant.services.mail_service import summarize_thread
        result = summarize_thread(thread_id=req.thread_id, max_tokens=req.max_tokens)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[chat] summarize_mail_thread failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Mail service — fetch thread messages
# ---------------------------------------------------------------------------


class FetchThreadMessagesRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, description="Thread ID")


@router.post("/mail/thread-messages")
def fetch_thread_messages(req: FetchThreadMessagesRequest):
    """Return all vault .md files belonging to the given thread_id."""
    from personal_assistant.services.mail_service import fetch_thread_messages as do_fetch
    messages = do_fetch(thread_id=req.thread_id)
    return {"thread_id": req.thread_id, "messages": messages, "count": len(messages)}


# ---------------------------------------------------------------------------
# Calendar service — create meeting draft
# ---------------------------------------------------------------------------


class CreateMeetingDraftRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    start_iso: str = Field(..., description="ISO 8601 datetime, e.g. 2026-06-01T10:00:00")
    duration_minutes: int = Field(60, ge=5, le=480)
    location: str = Field("", max_length=500)
    notes: str = Field("", max_length=4000)
    attendees: list[str] = Field(default_factory=list, max_length=20)
    calendar_name: str = Field("", max_length=200)


@router.post("/calendar/create-meeting")
def create_meeting_draft(req: CreateMeetingDraftRequest):
    """Open a pre-filled meeting draft in Apple Calendar via AppleScript."""
    import platform
    if platform.system() != "Darwin":
        raise HTTPException(status_code=501, detail="Calendar integration is macOS-only")

    from datetime import datetime, timedelta

    from personal_assistant.services.calendar_service import create_meeting_draft as do_create

    try:
        start_dt = datetime.fromisoformat(req.start_iso)
        end_dt = start_dt + timedelta(minutes=req.duration_minutes)
        result = do_create(
            title=req.title,
            start_dt=start_dt,
            end_dt=end_dt,
            location=req.location,
            notes=req.notes,
            attendees=req.attendees or None,
            calendar_name=req.calendar_name,
        )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Calendar service — fetch upcoming events
# ---------------------------------------------------------------------------


@router.get("/calendar/upcoming")
def fetch_upcoming_events(days: int = 7):
    """Return upcoming calendar events from the vault (next N days)."""
    from personal_assistant.services.calendar_service import fetch_upcoming_events as do_fetch
    events = do_fetch(days_forward=max(1, min(days, 90)))
    return {"events": events, "count": len(events)}
