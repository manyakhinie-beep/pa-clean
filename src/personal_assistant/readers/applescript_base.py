"""
Shared AppleScript runner and helpers.

All reader modules go through _run_applescript() so error handling,
timeouts and logging are consistent.
"""

from __future__ import annotations

import hashlib
import re as _re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# AppleScript snippets reused in every reader script
# ---------------------------------------------------------------------------

# ISO-8601 date formatter (no timezone — AppleScript dates are local)
AS_ISO_DATE = """\
on isoDate(d)
    set y to year of d
    set mo to (month of d as integer)
    set dy to day of d
    set h to hours of d
    set mi to minutes of d
    set se to seconds of d
    set moStr to text -2 thru -1 of ("0" & (mo as string))
    set dyStr to text -2 thru -1 of ("0" & (dy as string))
    set hStr  to text -2 thru -1 of ("0" & (h  as string))
    set miStr to text -2 thru -1 of ("0" & (mi as string))
    set seStr to text -2 thru -1 of ("0" & (se as string))
    return (y as string) & "-" & moStr & "-" & dyStr & "T" & hStr & ":" & miStr & ":" & seStr
end isoDate
"""

# JSON-safe string escaper
# Handles all characters that can break JSON parsing or crash AppleScript's
# rep() function: backslash, double-quote, control chars (NUL, VT, FF, etc.).
# Note: tab (0x09) → space, LF (0x0A) → \n, CR (0x0D) → removed,
#       VT (0x0B) and FF (0x0C) → removed (common in Outlook mail bodies),
#       NULL (0x00) and DEL (0x7F) → removed (crash rep() in some AS builds).
AS_ESC = """\
on esc(s)
    if s is missing value then return ""
    set s to s as string
    -- Strip NULL (0x00) and DEL (0x7F) — these crash rep() inside AppleScript
    set s to my repAll(s, (ASCII character 0), "")
    set s to my repAll(s, (ASCII character 127), "")
    -- Strip Vertical Tab (0x0B) and Form Feed (0x0C) — common in Outlook bodies
    set s to my repAll(s, (ASCII character 11), "")
    set s to my repAll(s, (ASCII character 12), "")
    -- Escape for JSON: backslash must come first, then double-quote
    set s to my repAll(s, "\\\\", "\\\\\\\\")
    set s to my repAll(s, "\\"", "\\\\\\"")
    -- Normalise whitespace: LF(10) -> "\\n", CR(13) -> removed, TAB(9) -> space
    set s to my repAll(s, (ASCII character 10), "\\\\n")
    set s to my repAll(s, (ASCII character 13), "")
    set s to my repAll(s, (ASCII character 9), " ")
    return s
end esc

on repAll(txt, a, b)
    if a is "" then return txt
    set AppleScript's text item delimiters to a
    set parts to text items of (txt as string)
    set AppleScript's text item delimiters to b
    set res to parts as string
    set AppleScript's text item delimiters to ""
    return res
end repAll
"""

# Joins a list of strings with a separator
AS_JOIN = """\
on joinList(lst, sep)
    set AppleScript's text item delimiters to sep
    set res to lst as string
    set AppleScript's text item delimiters to ""
    return res
end joinList
"""

# min() doesn't exist in AppleScript — provide our own
AS_MIN = """\
on minVal(a, b)
    if a < b then return a
    return b
end minVal
"""

# Preamble shared by all readers
AS_PREAMBLE = AS_ISO_DATE + AS_ESC + AS_JOIN + AS_MIN


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------
# All subclasses inherit from ``RuntimeError`` so legacy ``except RuntimeError``
# blocks continue to work — every existing caller that catches RuntimeError and
# matches on the error text keeps functioning untouched.


class AppleScriptError(RuntimeError):
    """Base class for AppleScript / osascript failures."""


class AppleScriptTimeout(AppleScriptError):
    """osascript exceeded its timeout — eligible for retry."""


class AppleScriptPermissionDenied(AppleScriptError):
    """TCC / Automation permission denied (error 1743) — NOT retryable."""


# Backoff schedule between transient-timeout retries.  Tuple length =
# number of retries (default 2 → first retry after 2s, second after 8s).
# Exposed as module constant so tests can monkeypatch.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 8.0)


def _classify_error(stderr: str) -> type[AppleScriptError]:
    err = (stderr or "").lower()
    if "1743" in err or "not allowed" in err or "not authorised" in err or "not authorized" in err:
        return AppleScriptPermissionDenied
    return AppleScriptError


def run_applescript(
    script: str,
    timeout: int = 180,
    *,
    retries: int = 2,
    retry_on_timeout: bool = True,
) -> str:
    """
    Execute *script* via osascript using a temp file.

    Writing the script to a UTF-8 temp file (instead of passing it via
    the -e flag) avoids two classes of bugs:

    1. Encoding / shell-quoting issues: passing multi-line scripts with
       the ¬ continuation character and Unicode (≥, ≤) via -e can trigger
       osascript's inline parser to mis-locate syntax errors and
       occasionally refuse to compile valid scripts (error -2741).

    2. Character-position reporting: file-based execution gives accurate
       LINE:COL positions in error messages, making debugging much easier.

    Resilience:
      * ``retries`` controls how many extra attempts we make on
        ``subprocess.TimeoutExpired``. Default ``2`` → up to 3 total
        attempts with backoff ``RETRY_BACKOFF_SECONDS``.
      * Non-timeout errors (TCC denials, AppleScript compile errors)
        are NEVER retried — they are deterministic and retrying just
        wastes time.

    Returns stdout as a stripped string.

    Raises ``AppleScriptTimeout`` after all retries are exhausted,
    ``AppleScriptPermissionDenied`` on TCC errors, ``AppleScriptError``
    otherwise. All subclass ``RuntimeError`` for backward compatibility
    with existing call sites.
    """
    import time as _time

    if sys.platform != "darwin":
        raise AppleScriptError("AppleScript is only available on macOS.")

    backoff = list(RETRY_BACKOFF_SECONDS)
    # Number of attempts = 1 (initial) + retries (only on timeout)
    max_attempts = max(1, retries + 1) if retry_on_timeout else 1
    last_timeout_attempts = 0

    for attempt in range(max_attempts):
        # Each attempt uses its own temp file — keeps cleanup simple and
        # avoids any chance of stale state if a previous run crashed.
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".applescript",
                encoding="utf-8",
                delete=False,
            ) as tf:
                tf.write(script)
                tmp_path = tf.name

            result = subprocess.run(
                ["osascript", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            last_timeout_attempts = attempt + 1
            if attempt + 1 < max_attempts:
                # Schedule next retry after backoff.  Use min(attempt, ...)
                # so a longer-than-expected backoff list still works.
                wait = backoff[min(attempt, len(backoff) - 1)] if backoff else 0
                if wait > 0:
                    _time.sleep(wait)
                continue
            raise AppleScriptTimeout(
                f"osascript timed out after {timeout}s "
                f"(attempts={last_timeout_attempts})"
            )
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

        if result.returncode != 0:
            err_cls = _classify_error(result.stderr)
            raise err_cls(f"osascript error: {result.stderr.strip()}")

        return result.stdout.strip()

    # Unreachable — the loop either returns or raises — but keeps mypy happy.
    raise AppleScriptError("osascript: no attempts made")


# ---------------------------------------------------------------------------
# App availability
# ---------------------------------------------------------------------------

_APP_SEARCH_PATHS = [
    Path("/Applications"),
    Path.home() / "Applications",
    Path("/System/Applications"),
]


def is_app_installed(app_name: str) -> bool:
    """
    Check whether *app_name*.app exists on disk without launching anything.
    E.g. is_app_installed("Microsoft Outlook")
    """
    for base in _APP_SEARCH_PATHS:
        if (base / f"{app_name}.app").exists():
            return True
    return False


def is_app_running(app_name: str) -> bool:
    """Return True if the app is currently running (fast osascript check)."""
    try:
        out = run_applescript(
            f'tell application "System Events" to (name of processes) contains "{app_name}"',
            timeout=5,
        )
        return out.strip().lower() == "true"
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Safe property getter helper (Python-side)
# ---------------------------------------------------------------------------


def safe_str(value: Optional[str], max_len: Optional[int] = 500) -> Optional[str]:
    """
    Coerce *value* to str, stripping AppleScript sentinel values.

    Parameters
    ----------
    max_len:
        Maximum characters to keep.  Pass ``None`` (or 0) to keep the full
        string without truncation — required for email body / event notes
        so that long content is not silently lost.
    """
    if not value or value in ("missing value", ""):
        return None
    s = value.strip()
    if not s:
        return None
    if max_len:
        s = s[:max_len]
    return s or None


# ---------------------------------------------------------------------------
# JSON sanitizer (Python-side)
# ---------------------------------------------------------------------------

# Prefixes stripped when normalising a subject to derive a thread ID.
# Covers common Re/Fwd variants in English, Russian, German, French, Spanish.
_REPLY_PREFIX_RE = _re.compile(
    r"^\s*("
    r"re|fwd?|aw|tr|sv|rv|vl|rép|rep|ref"  # EN/DE/FR/ES/RU shortcuts
    r"|отв|пер|fwd"  # RU: ответ/пересылка
    r")\s*(\[\d+\])?\s*:\s*",
    flags=_re.IGNORECASE,
)


def compute_thread_id(subject: str) -> str:
    """
    Return a short stable hex ID that is the same for all messages in a
    reply/forward chain.

    Algorithm:
      1. Strip Re:/Fwd:/Отв: prefixes repeatedly until none remain.
      2. Lowercase + strip whitespace.
      3. MD5 → first 12 hex chars (collision-safe for personal email volume).
    """
    s = subject
    prev = None
    while s != prev:
        prev = s
        s = _REPLY_PREFIX_RE.sub("", s)
    normalised = s.strip().lower()
    return hashlib.md5(normalised.encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()[:12]


# Control characters that are illegal in JSON strings:
# all ASCII 0x00–0x1F except 0x09 (\t), 0x0A (\n), 0x0D (\r)
# which AppleScript already escapes to the two-char sequences \t, \n, \r.
_CTRL_RE = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_json(raw: str) -> str:
    """
    Strip illegal control characters from an AppleScript JSON output string
    before passing it to json.loads(). Email bodies often contain form-feed
    (0x0C), vertical-tab (0x0B), null bytes etc. that are not valid in JSON.
    """
    return _CTRL_RE.sub("", raw)
