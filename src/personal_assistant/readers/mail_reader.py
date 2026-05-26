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
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Optional

from loguru import logger

from personal_assistant.models import Contact, MailMessage
from personal_assistant.readers.applescript_base import (
    AS_PREAMBLE,
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

_LIST_MAILBOXES_SCRIPT = """\
tell application "Mail"
    set result_lines to {}
    repeat with acct in every account
        set acctName to name of acct as string
        repeat with mbox in every mailbox of acct
            set mboxName to name of mbox as string
            set end of result_lines to acctName & "|||" & mboxName
        end repeat
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
set startDate to (current date) - ({days_back} * days)
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
        end try

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

# Injected when fetch_body=True
_BODY_BLOCK = """\
        set msgBody to ""
        try
            set bodyText to content of msg
            if bodyText is not missing value then
                set msgBody to my esc(bodyText as string)
            end if
        end try"""

# Injected when fetch_body=False
_BODY_SKIP = '        set msgBody to ""'


# ---------------------------------------------------------------------------
# MailReader
# ---------------------------------------------------------------------------


class MailReader:
    """Reads Apple Mail messages via osascript.

    Splits work into per-mailbox AppleScript calls so one slow mailbox
    (e.g. a huge IMAP folder) cannot block all others.
    """

    # Per-mailbox timeout in seconds.
    PER_MBOX_TIMEOUT: int = 45
    # Max messages fetched per mailbox per sync.
    DEFAULT_MAX_MESSAGES: int = 100

    def fetch_messages(
        self,
        days_back: int = 30,
        max_messages_per_mailbox: int = DEFAULT_MAX_MESSAGES,
        fetch_body: bool = False,
        fetch_recipients: bool = False,
        skip_mailboxes: Optional[set[str]] = None,
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
            skip_mailboxes: extra folder names to skip (merged with defaults).
        """
        skip = _SKIP_MAILBOXES | (skip_mailboxes or set())

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
            f"recipients={'on' if fetch_recipients else 'off'})"
        )

        # Step 3 — fetch per mailbox
        all_messages: list[MailMessage] = []
        seen_ids: set[str] = set()  # deduplicate across nested mailboxes

        for mb in wanted:
            msgs = self._fetch_one_mailbox(
                acct_name=mb["account"],
                mbox_name=mb["mailbox"],
                days_back=days_back,
                max_messages=max_messages_per_mailbox,
                fetch_body=fetch_body,
                fetch_recipients=fetch_recipients,
            )
            for msg in msgs:
                if msg.message_id and msg.message_id in seen_ids:
                    continue
                seen_ids.add(msg.message_id)
                all_messages.append(msg)

        logger.info(f"[mail] Total messages fetched: {len(all_messages)}")
        return all_messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_mailboxes(self) -> list[dict]:
        """Return list of {account, mailbox} dicts."""
        raw = run_applescript(_LIST_MAILBOXES_SCRIPT, timeout=15)
        result: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||", 1)
            if len(parts) == 2:
                result.append(
                    {"account": parts[0].strip(), "mailbox": parts[1].strip()}
                )
        return result

    def _esc(self, s: str) -> str:
        """Escape a string for inline AppleScript double-quoted literal."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _fetch_one_mailbox(
        self,
        acct_name: str,
        mbox_name: str,
        days_back: int,
        max_messages: int,
        fetch_body: bool,
        fetch_recipients: bool,
    ) -> list[MailMessage]:
        """Fetch messages from a single mailbox with per-mailbox timeout."""
        script = _FETCH_MBOX_SCRIPT.format(
            days_back=days_back,
            max_messages=max_messages,
            acct_name_esc=self._esc(acct_name),
            mbox_name_esc=self._esc(mbox_name),
            recipients_block=_RECIPIENTS_BLOCK
            if fetch_recipients
            else _RECIPIENTS_SKIP,
            body_block=_BODY_BLOCK if fetch_body else _BODY_SKIP,
        )

        try:
            raw = run_applescript(script, timeout=self.PER_MBOX_TIMEOUT)
        except RuntimeError as e:
            err = str(e)
            if "timed out" in err.lower():
                logger.warning(
                    f"[mail] '{acct_name}/{mbox_name}' timed out after "
                    f"{self.PER_MBOX_TIMEOUT}s — skipping. "
                    f"Try reducing PA_MAIL_DAYS_BACK or PA_MAIL_MAX_MESSAGES."
                )
            elif "1743" in err or "not allowed" in err.lower():
                logger.error(
                    "[mail] Access denied (error 1743). "
                    "Go to System Settings → Privacy → Automation → Mail."
                )
            else:
                logger.warning(f"[mail] '{acct_name}/{mbox_name}' error: {e}")
            return []

        msgs = self._parse(raw, f"{acct_name}/{mbox_name}")
        if msgs:
            logger.debug(f"[mail] '{acct_name}/{mbox_name}': {len(msgs)} messages")
        return msgs

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
        return MailMessage(
            message_id=item.get("id", ""),
            subject=subject,
            sender_name=name or None,
            sender_email=email or "unknown@unknown",
            recipients=recipients,
            cc=cc,
            date=_dt(item.get("date", "")),
            mailbox=safe_str(item.get("mailbox")),
            body=safe_str(item.get("body"), max_len=None),
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
