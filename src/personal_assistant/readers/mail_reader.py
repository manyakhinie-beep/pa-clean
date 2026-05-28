"""
Apple Mail reader via osascript (Mail.app).

Reads messages using AppleScript through Mail.app.
Requires Automation permission:
  System Settings → Privacy & Security → Automation → Terminal → Mail

Performance notes:
  - Splits into per-mailbox AppleScript calls to isolate timeouts.
  - `content of msg` (full body) is disabled by default — reading the full
    body forces Mail.app to download and decode every message (~50–200 ms each).
    300 messages × 100 ms = 30 s just for body text.
    Enable with PA_MAIL_FETCH_BODY=true.
  - `to recipients of msg` is also disabled by default for the same reason.
    Enable with PA_MAIL_FETCH_RECIPIENTS=true.
  - Attachment names are fetched together with has_attachments at no extra cost
    when PA_MAIL_FETCH_BODY=true (names are read from already-loaded message).
  - Noise folders (Sent, Trash, Junk, Drafts, Archive) are skipped automatically.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Optional

from loguru import logger

from personal_assistant.models import Contact, MailMessage
from personal_assistant.readers.applescript_base import (
    AS_PREAMBLE,
    AppleScriptPermissionDenied,
    AppleScriptTimeout,
    compute_thread_id,
    run_applescript,
    safe_str,
    sanitize_json,
)

# ---------------------------------------------------------------------------
# Mailboxes to skip (case-sensitive names used by Mail.app)
# ---------------------------------------------------------------------------

_SKIP_MAILBOXES: set[str] = {
    "Sent Messages",
    "Sent",
    "Sent Items",
    "Trash",
    "Deleted Messages",
    "Deleted Items",
    "Junk",
    "Junk Email",
    "Spam",
    "Drafts",
    "Draft",
    "Archive",
    "Archives",
    "Outbox",
}

# ---------------------------------------------------------------------------
# Script 1: list all (account, mailbox) pairs  — fast, ~0.5 s
# ---------------------------------------------------------------------------

# Mail.app's ``every mailbox of acct`` does NOT recursively enumerate
# nested mailboxes on all account types — IMAP accounts with deep folder
# trees (e.g. ``Inbox/Архив/2024/Январь``) silently skip the inner
# levels.  We walk the tree explicitly via ``my listRecurse`` so every
# nested mailbox is listed.  Mail.app sometimes also flattens, so the
# Python side dedupes by ``(account, leaf_name)`` to keep behaviour
# stable across macOS versions.
_LIST_MAILBOXES_SCRIPT = """\
on listRecurse(acctName, mboxList, prefix)
    global result_lines
    repeat with mb in mboxList
        try
            set mbName to name of mb as string
            set fullPath to prefix & mbName
            set end of result_lines to acctName & "|||" & fullPath
            try
                my listRecurse(acctName, every mailbox of mb, fullPath & "/")
            end try
        end try
    end repeat
end listRecurse

global result_lines
set result_lines to {}
tell application "Mail"
    repeat with acct in every account
        set acctName to name of acct as string
        try
            my listRecurse(acctName, every mailbox of acct, "")
        end try
    end repeat
end tell
set AppleScript's text item delimiters to "\n"
set output to result_lines as string
set AppleScript's text item delimiters to ""
return output
"""

# ---------------------------------------------------------------------------
# Script 2: fetch messages from ONE mailbox  — per-mailbox, capped
# ---------------------------------------------------------------------------
# Snippet and recipients are injected conditionally to keep the fast path fast.

_FETCH_MBOX_SCRIPT = (
    AS_PREAMBLE
    + """\
set startDate to (current date) - {seconds_back}
set maxMsgs   to {max_messages}
set acctName  to "{acct_name_esc}"
set mboxName  to "{mbox_name_esc}"

set entries to {{}}

tell application "Mail"
    -- Locate account
    set targetAcct to missing value
    repeat with acct in every account
        if (name of acct as string) is acctName then
            set targetAcct to acct
            exit repeat
        end if
    end repeat
    if targetAcct is missing value then return "[]"

    -- Locate mailbox
    set targetMbox to missing value
    repeat with mbox in every mailbox of targetAcct
        if (name of mbox as string) is mboxName then
            set targetMbox to mbox
            exit repeat
        end if
    end repeat
    if targetMbox is missing value then return "[]"

    -- Fetch messages, capped
    set recentMsgs to (messages of targetMbox whose date received ≥ startDate)
    set msgCount to my minVal(count of recentMsgs, maxMsgs)

    repeat with i from 1 to msgCount
        set msg to item i of recentMsgs

        set msgID to ""
        try
            set msgID to my esc(message id of msg)
        end try

        set msgSubject to ""
        try
            set msgSubject to my esc(subject of msg)
        end try

        set senderRaw to ""
        try
            set senderRaw to my esc(sender of msg as string)
        end try

        set msgDate to my isoDate(date received of msg)

{attachments_block}

{recipients_block}
{body_block}

        set entry to "{{" & ¬
            "\\"id\\":\\"" & msgID & "\\"," & ¬
            "\\"subject\\":\\"" & msgSubject & "\\"," & ¬
            "\\"sender\\":\\"" & senderRaw & "\\"," & ¬
            "\\"recipients\\":\\"" & recipEmails & "\\"," & ¬
            "\\"cc\\":\\"" & recipCcEmails & "\\"," & ¬
            "\\"date\\":\\"" & msgDate & "\\"," & ¬
            "\\"mailbox\\":\\"" & my esc(mboxName) & "\\"," & ¬
            "\\"body\\":\\"" & msgBody & "\\"," & ¬
            "\\"has_attachments\\":" & hasAttach & "," & ¬
            "\\"attachment_names\\":\\"" & attachNames & "\\"," & ¬
            "\\"source\\":\\"mail\\"" & ¬
            "}}"
        set end of entries to entry
    end repeat
end tell

set AppleScript's text item delimiters to ","
set output to "[" & (entries as string) & "]"
set AppleScript's text item delimiters to ""
return output
"""
)

# Injected when fetch_recipients=True
_RECIPIENTS_BLOCK = """\
        set recipEmails to ""
        set recipCcEmails to ""
        try
            repeat with r in to recipients of msg
                set rAddr to address of r
                if rAddr is not missing value then
                    set recipEmails to recipEmails & my esc(rAddr) & ","
                end if
            end repeat
        end try
        try
            repeat with r in cc recipients of msg
                set rAddr to address of r
                if rAddr is not missing value then
                    set recipCcEmails to recipCcEmails & my esc(rAddr) & ","
                end if
            end repeat
        end try"""

# Injected when fetch_recipients=False
_RECIPIENTS_SKIP = '        set recipEmails to ""\n        set recipCcEmails to ""'

# Injected when fetch_attachment_names=True — slow on IMAP because every
# ``mail attachments of msg`` access forces Mail.app to download the
# message structure (and often the full body for not-yet-downloaded
# remote messages).  This is the single biggest cause of per-mailbox
# timeouts on heavy contact folders with multi-MB attachments.
_ATTACHMENTS_BLOCK = """\
        set hasAttach to "false"
        set attachNames to ""
        try
            set attList to mail attachments of msg
            if (count of attList) > 0 then
                set hasAttach to "true"
                repeat with att in attList
                    set attName to ""
                    try
                        set attName to my esc(name of att)
                    end try
                    if attName is not "" then
                        set attachNames to attachNames & attName & "|"
                    end if
                end repeat
            end if
        end try"""

# Injected when fetch_attachment_names=False (default).  We still mark
# ``has_attachments=false`` so downstream code has a defined field; the
# vault writer handles the missing-attachment-names case gracefully.
# Reduces per-mailbox time on heavy IMAP folders by 50–70 %.
_ATTACHMENTS_SKIP = """\
        set hasAttach to "false"
        set attachNames to \"\""""

_RFC822_HEADER_RE = re.compile(
    r"^\s*(from|return-path|received|mime-version|content-type|message-id|subject)\s*:",
    re.IGNORECASE,
)


# Minimum window AppleScript is allowed to look back over.  Zero would
# be a no-op (clock-skew risk), so we floor at 60 seconds — even on a
# zero-overlap watermark we still scan the last minute for safety.
_MIN_WINDOW_SECONDS = 60


def _resolve_seconds_back(days_back: int, since: Optional[datetime]) -> int:
    """Translate (``days_back``, ``since``) into the AppleScript window.

    The reader always honours the configured ``days_back`` ceiling: a
    too-eager watermark cannot read further back than the user wants.
    A missing/future watermark falls back to the full ``days_back``
    window — same behaviour as before A+B was added.
    """
    ceiling = max(_MIN_WINDOW_SECONDS, int(days_back) * 86_400)
    if since is None:
        return ceiling
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    delta = (datetime.now(tz=timezone.utc) - since).total_seconds()
    # delta < 0 → watermark in the future (clock skew); treat as fresh.
    seconds = int(max(_MIN_WINDOW_SECONDS, delta))
    return min(seconds, ceiling)


def _looks_like_rfc822(text: str) -> bool:
    """Cheap heuristic: does *text* look like an RFC822 source dump?

    Looks for a typical mail header in the first 4 KB of the body.  We
    cap the scan because some inboxes ship 100 KB+ HTML emails — running
    a regex over the full payload on every message is wasted work.
    """
    if not text or len(text) < 20:
        return False
    head = text[:4096]
    return bool(_RFC822_HEADER_RE.match(head))


# Injected when fetch_body=True and fetch_raw_source=False — plain rendered text.
_BODY_BLOCK = """\
        set msgBody to ""
        try
            set bodyText to content of msg
            if bodyText is not missing value then
                set msgBody to my esc(bodyText as string)
            end if
        end try"""

# Injected when fetch_body=True and fetch_raw_source=True — full RFC822
# source so the Python side can extract text/html and convert to Markdown.
# Falls back to ``content of msg`` when ``source`` is missing or fails.
_BODY_BLOCK_RAW = """\
        set msgBody to ""
        try
            set rawSrc to source of msg
            if rawSrc is not missing value then
                set msgBody to my esc(rawSrc as string)
            end if
        end try
        if msgBody is "" then
            try
                set bodyText to content of msg
                if bodyText is not missing value then
                    set msgBody to my esc(bodyText as string)
                end if
            end try
        end if"""

# Injected when fetch_body=False
_BODY_SKIP = '        set msgBody to ""'


# ---------------------------------------------------------------------------
# MailReader
# ---------------------------------------------------------------------------


class MailReader:
    """Reads Apple Mail messages via osascript.

    Splits work into per-mailbox AppleScript calls so one slow mailbox
    (e.g. a huge IMAP folder) cannot block all others.

    Per-run telemetry is exposed as ``self.last_report`` after every
    :meth:`fetch_messages` call:

        {
          "iCloud/INBOX": {"ok": True,  "count": 12, "error": "",       "duration_s": 4.2},
          "Work/Big":     {"ok": False, "count": 0,  "error": "timeout", "duration_s": 45.0},
        }

    Consumers (orchestrator in ``mlx_server/server.py``) read this to
    write per-bucket watermarks via ``services.sync_state``.
    """

    # Per-mailbox timeout in seconds.
    PER_MBOX_TIMEOUT: int = 45
    # Max messages fetched per mailbox per sync.
    DEFAULT_MAX_MESSAGES: int = 100

    def __init__(self) -> None:
        # Populated by fetch_messages — see class docstring for shape.
        self.last_report: dict[str, dict] = {}
        self._fetch_raw_source: bool = False

    def fetch_messages(
        self,
        days_back: int = 30,
        max_messages_per_mailbox: int = DEFAULT_MAX_MESSAGES,
        fetch_body: bool = False,
        fetch_recipients: bool = False,
        fetch_raw_source: bool = False,
        fetch_attachment_names: bool = False,
        skip_mailboxes: Optional[set[str]] = None,
        since: Optional[datetime] = None,
        since_per_mailbox: Optional[dict[str, datetime]] = None,
    ) -> list[MailMessage]:
        """Fetch messages received in the last *days_back* days.

        Args:
            days_back: how many days back to look
            max_messages_per_mailbox: hard cap per folder (prevents timeouts)
            fetch_body: read full plain-text body and attachment names (slow —
                        forces Mail.app to download each message, ~50–200 ms each).
                        Default False. Enable with PA_MAIL_FETCH_BODY=true.
            fetch_recipients: read To-header per message (slow IPC call).
                              Default False.
            fetch_raw_source: when True (and fetch_body=True) pull the full
                RFC822 ``source of msg`` instead of just rendered plain
                text.  ``_convert`` then extracts the ``text/html`` MIME
                part and converts to Markdown so bullet lists / bold /
                links survive into the vault.  Trade-off: 5-100x larger
                payload per message → slower sync.  Enable with
                ``PA_MAIL_FETCH_RAW_SOURCE=true``.
            fetch_attachment_names: enumerate every attachment of every
                message via ``mail attachments of msg``.  Default False
                — that AppleScript call forces Mail.app to download the
                message structure on IMAP accounts, which is the single
                biggest cause of per-mailbox timeouts on heavy folders.
                Enable via ``PA_MAIL_FETCH_ATTACHMENT_NAMES=true`` only
                if you actually need attachment names in the vault.
            skip_mailboxes: extra folder names to skip (merged with defaults).
            since: when provided, fetch only messages received after this
                timestamp.  Combined with ``days_back`` as a min() so the
                actual window never exceeds the configured limit, never
                goes below a 60s floor (avoids zero-second windows on
                back-to-back syncs).  This is the watermark hook used by
                incremental sync.
            since_per_mailbox: per-mailbox watermarks keyed by ``"account/mailbox"``;
                takes precedence over the global ``since`` when present.
        """
        skip = _SKIP_MAILBOXES | (skip_mailboxes or set())
        # Reset telemetry for this run
        self.last_report = {}

        # Step 1 — list mailboxes
        try:
            mailboxes = self._list_mailboxes()
        except RuntimeError as e:
            err = str(e)
            if "1743" in err or "not allowed" in err.lower():
                logger.error(
                    "[mail] Access denied (error 1743). "
                    "Go to System Settings → Privacy → Automation → Mail."
                )
            else:
                logger.error(f"[mail] Cannot list mailboxes: {e}")
            return []

        # Step 2 — filter out noise folders
        wanted = [mb for mb in mailboxes if mb["mailbox"] not in skip]
        skipped_names = [mb["mailbox"] for mb in mailboxes if mb["mailbox"] in skip]
        if skipped_names:
            logger.debug(
                f"[mail] Skipping {len(skipped_names)} noise folders: {skipped_names}"
            )

        if not wanted:
            logger.warning("[mail] No mailboxes to fetch")
            return []

        logger.info(
            f"[mail] Fetching {len(wanted)} mailboxes  "
            f"({days_back}d back, max {max_messages_per_mailbox} msgs each, "
            f"body={'on' if fetch_body else 'off'}, "
            f"recipients={'on' if fetch_recipients else 'off'}, "
            f"raw_source={'on' if fetch_raw_source else 'off'})"
        )

        # Step 3 — fetch per mailbox.  A failure in one mailbox is isolated
        # (returns [] and is logged into ``self.last_report``) so it never
        # blocks the rest — this is the «B: skip-on-fail» half of A+B.
        all_messages: list[MailMessage] = []
        seen_ids: set[str] = set()  # deduplicate across nested mailboxes

        for mb in wanted:
            mb_key = f"{mb['account']}/{mb['mailbox']}"
            # Pick the most specific watermark available for this mailbox.
            mb_since = None
            if since_per_mailbox and mb_key in since_per_mailbox:
                mb_since = since_per_mailbox[mb_key]
            elif since is not None:
                mb_since = since
            seconds_back = _resolve_seconds_back(days_back, mb_since)

            started = time.monotonic()
            msgs, err = self._fetch_one_mailbox(
                acct_name=mb["account"],
                mbox_name=mb["mailbox"],
                seconds_back=seconds_back,
                max_messages=max_messages_per_mailbox,
                fetch_body=fetch_body,
                fetch_recipients=fetch_recipients,
                fetch_raw_source=fetch_raw_source,
                fetch_attachment_names=fetch_attachment_names,
            )
            duration = round(time.monotonic() - started, 2)

            new_count = 0
            for msg in msgs:
                if msg.message_id and msg.message_id in seen_ids:
                    continue
                seen_ids.add(msg.message_id)
                all_messages.append(msg)
                new_count += 1

            self.last_report[mb_key] = {
                "ok": err is None,
                "count": new_count,
                "error": err or "",
                "duration_s": duration,
                "since": mb_since.isoformat() if mb_since else "",
            }

        ok = sum(1 for r in self.last_report.values() if r["ok"])
        fail = len(self.last_report) - ok
        logger.info(
            f"[mail] Total messages fetched: {len(all_messages)} "
            f"(mailboxes ok={ok}, failed={fail})"
        )
        return all_messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_mailboxes(self) -> list[dict]:
        """Return list of ``{account, mailbox, path}`` dicts.

        ``path`` is the full hierarchical path returned by AppleScript
        (e.g. ``"Inbox/Архив/2024"``); ``mailbox`` is the leaf name —
        what the fetch script matches against ``name of mbox``.  Python
        deduplicates by ``(account, leaf_name)`` because Mail.app on
        some macOS versions already flat-enumerates and we'd otherwise
        emit duplicates after my explicit recursion.

        Limitation: two siblings with the same leaf name in different
        parents collapse into one bucket here.  Acceptable trade-off —
        the alternative is rewriting the fetch script to walk paths,
        which is invasive and rarely needed in practice.
        """
        raw = run_applescript(_LIST_MAILBOXES_SCRIPT, timeout=15)
        result: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||", 1)
            if len(parts) != 2:
                continue
            account = parts[0].strip()
            full_path = parts[1].strip()
            leaf = full_path.rsplit("/", 1)[-1]
            key = (account, leaf)
            if key in seen:
                continue
            seen.add(key)
            result.append({"account": account, "mailbox": leaf, "path": full_path})
        return result

    def _esc(self, s: str) -> str:
        """Escape a string for inline AppleScript double-quoted literal."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _fetch_one_mailbox(
        self,
        acct_name: str,
        mbox_name: str,
        seconds_back: int,
        max_messages: int,
        fetch_body: bool,
        fetch_recipients: bool,
        fetch_raw_source: bool = False,
        fetch_attachment_names: bool = False,
    ) -> tuple[list[MailMessage], Optional[str]]:
        """Fetch messages from a single mailbox with per-mailbox timeout.

        Returns ``(messages, error_str | None)``.  Errors are captured
        and returned — never raised — so the orchestrator can keep
        going through the remaining mailboxes and record per-bucket
        outcome in the sync_state watermark file.
        """
        # Pick the body-extraction snippet: skip / plain content / raw RFC822.
        if not fetch_body:
            body_block = _BODY_SKIP
        elif fetch_raw_source:
            body_block = _BODY_BLOCK_RAW
        else:
            body_block = _BODY_BLOCK
        self._fetch_raw_source = fetch_raw_source  # used by _convert
        script = _FETCH_MBOX_SCRIPT.format(
            seconds_back=seconds_back,
            max_messages=max_messages,
            acct_name_esc=self._esc(acct_name),
            mbox_name_esc=self._esc(mbox_name),
            attachments_block=_ATTACHMENTS_BLOCK
            if fetch_attachment_names
            else _ATTACHMENTS_SKIP,
            recipients_block=_RECIPIENTS_BLOCK
            if fetch_recipients
            else _RECIPIENTS_SKIP,
            body_block=body_block,
        )

        try:
            raw = run_applescript(script, timeout=self.PER_MBOX_TIMEOUT)
        except AppleScriptTimeout as e:
            logger.warning(
                f"[mail] '{acct_name}/{mbox_name}' timed out after "
                f"{self.PER_MBOX_TIMEOUT}s (incl. retries) — skipping. "
                f"Try reducing PA_MAIL_DAYS_BACK or PA_MAIL_MAX_MESSAGES."
            )
            return [], f"timeout after {self.PER_MBOX_TIMEOUT}s"
        except AppleScriptPermissionDenied:
            logger.error(
                "[mail] Access denied (error 1743). "
                "Go to System Settings → Privacy → Automation → Mail."
            )
            return [], "permission denied (TCC 1743)"
        except RuntimeError as e:
            logger.warning(f"[mail] '{acct_name}/{mbox_name}' error: {e}")
            return [], str(e)

        msgs = self._parse(raw, f"{acct_name}/{mbox_name}")
        if msgs:
            logger.debug(f"[mail] '{acct_name}/{mbox_name}': {len(msgs)} messages")
        return msgs, None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, raw: str, label: str = "") -> list[MailMessage]:
        if not raw or raw.strip() == "[]":
            return []
        try:
            data = json.loads(sanitize_json(raw))
        except json.JSONDecodeError as e:
            logger.error(f"[mail] JSON parse error ({label}): {e}")
            logger.debug(f"Raw (first 300): {raw[:300]}")
            return []

        messages: list[MailMessage] = []
        for item in data:
            try:
                messages.append(self._convert(item))
            except Exception as exc:
                logger.warning(
                    f"[mail] Skipping message {item.get('id', '?')!r}: {exc}"
                )
        return messages

    def _convert(self, item: dict) -> MailMessage:
        sender_raw = item.get("sender", "")
        name, email = parseaddr(sender_raw)
        if not email:
            m = re.search(r"<(.+@.+)>", sender_raw)
            email = m.group(1) if m else sender_raw.strip()
        email = email.strip().lower()

        def _addr_list(raw: str) -> list[str]:
            return [a.strip().lower() for a in raw.split(",") if a.strip() and "@" in a]

        recipients = _addr_list(item.get("recipients", ""))
        cc = _addr_list(item.get("cc", ""))

        def _dt(s: str) -> datetime:
            # AppleScript emits local wall-clock digits without an offset;
            # we tag as UTC for storage compatibility — see calendar_reader._dt
            # for the rationale.  Display layers read the digits as wall-clock.
            try:
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            except Exception:
                return datetime.now(tz=timezone.utc)

        subject = item.get("subject", "(no subject)")
        attachments = [
            a.strip()
            for a in item.get("attachment_names", "").split("|")
            if a.strip()
        ]

        # Body extraction.
        #
        # When PA_MAIL_FETCH_RAW_SOURCE=true the AppleScript layer dumped
        # the full RFC822 ``source of msg`` into ``body``. Detect that by
        # looking for typical headers, then run it through the MIME parser
        # + HTML→Markdown converter so the vault keeps bullet lists, bold,
        # italics, links — and the WebUI ``_emailToHtml`` renders them.
        #
        # Heuristic for "this is raw RFC822":
        #   * starts with one of {From:, Return-Path:, Received:, MIME-Version:,
        #     Content-Type:, Message-ID:, Subject:} (case-insensitive,
        #     ignoring any leading whitespace)
        #
        # The heuristic, not the ``fetch_raw_source`` flag, decides — that
        # way ``_convert`` is testable without instance state and stale
        # legacy items still come through correctly.
        raw_body = safe_str(item.get("body"), max_len=None)
        if raw_body and _looks_like_rfc822(raw_body):
            try:
                from personal_assistant.utils.email_html import source_to_markdown
                converted = source_to_markdown(raw_body)
                if converted.strip():
                    raw_body = converted
            except Exception as exc:  # noqa: BLE001 — never block sync
                logger.warning(f"[mail] RFC822 conversion failed: {exc}")

        return MailMessage(
            message_id=item.get("id", ""),
            subject=subject,
            sender_name=name or None,
            sender_email=email or "unknown@unknown",
            recipients=recipients,
            cc=cc,
            date=_dt(item.get("date", "")),
            mailbox=safe_str(item.get("mailbox")),
            body=raw_body,
            has_attachments=item.get("has_attachments") in (True, "true"),
            attachments=attachments,
            thread_id=compute_thread_id(subject),
        )

    # ------------------------------------------------------------------
    # Contact extraction
    # ------------------------------------------------------------------

    def extract_contacts(self, messages: list[MailMessage]) -> list[Contact]:
        seen: dict[str, Contact] = {}
        for msg in messages:
            email = msg.sender_email.strip().lower()
            if not email or "@" not in email:
                continue
            if email not in seen:
                seen[email] = Contact(
                    email=email, name=msg.sender_name, sources=["mail"]
                )
            else:
                c = seen[email]
                if not c.name and msg.sender_name:
                    c.name = msg.sender_name
                if "mail" not in c.sources:
                    c.sources.append("mail")
        return list(seen.values())
