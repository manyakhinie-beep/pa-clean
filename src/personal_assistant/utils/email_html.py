"""
email_html — convert RFC822 / HTML email bodies to readable Markdown.

Used by ``readers/mail_reader.py`` when ``PA_MAIL_FETCH_RAW_SOURCE=true``.
Apple Mail's AppleScript ``content of msg`` returns the Mail-rendered
plain-text version of an email — HTML structure (bold, italic, lists,
links) is **stripped**.  To preserve formatting in the vault we instead
fetch ``source of msg`` (full RFC822), extract the ``text/html`` part if
present, and convert to Markdown here.  The Markdown then round-trips
through the existing vault → WebUI ``_emailToHtml`` rendering pipeline
and the user sees the message structured the way they wrote it.

Design notes
------------

* **No external dependencies.**  ``mlx-lm`` and the FastAPI stack are
  already heavy; pulling ``markdownify`` / ``beautifulsoup4`` /
  ``html2text`` would add weight for a single use case.  This module
  uses ``html.parser`` from the stdlib + targeted regexes.
* **Conservative output.**  We do **not** try to perfectly recreate the
  HTML — only the patterns that matter for reading: paragraphs, line
  breaks, bold / italic, ordered / unordered lists, links, images,
  blockquotes, code, headings.  Unknown tags are stripped silently.
* **Safe by default.**  ``<script>`` / ``<style>`` are dropped; HTML
  entities decoded; whitespace collapsed reasonably without losing
  intentional paragraph structure.
* **MIME-aware.**  ``extract_body_from_source`` walks the message tree,
  prefers ``text/html`` over ``text/plain`` (since plain is what we
  already get without this module), and decodes the charset declared in
  the MIME headers.

Public API
----------

* :func:`extract_body_from_source` — RFC822 → ``{"plain": str|None,
  "html": str|None}``.
* :func:`html_to_markdown` — HTML → Markdown string.
* :func:`source_to_markdown` — RFC822 → Markdown (composes the two above
  with a sensible fallback to plain text).
"""

from __future__ import annotations

import email
import html as _html
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Optional


# ---------------------------------------------------------------------------
# MIME extraction
# ---------------------------------------------------------------------------


def extract_body_from_source(source: str) -> dict:
    """Parse an RFC822 source string and return the plain / HTML bodies.

    :returns: ``{"plain": str | None, "html": str | None}``.  Either or
        both can be ``None`` when the corresponding MIME part is absent
        or unreadable.
    """
    if not source or not isinstance(source, str):
        return {"plain": None, "html": None}

    try:
        # IMPORTANT: parse from bytes, not str.  ``email.message_from_string``
        # + ``get_payload(decode=True)`` re-encodes non-ASCII characters as
        # ``\uXXXX`` escape sequences inside the returned bytes — a known
        # quirk of Python's email module for str-sourced messages.  Going
        # through bytes preserves the original UTF-8 payload, and
        # ``decode=True`` then returns clean bytes we can decode with the
        # MIME charset.
        msg = email.message_from_bytes(source.encode("utf-8", errors="replace"))
    except Exception:
        return {"plain": None, "html": None}

    plain: Optional[str] = None
    html: Optional[str] = None

    def _decode(part: Message) -> Optional[str]:
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                # Fallback for str-payload messages (no Content-Transfer-Encoding)
                raw = part.get_payload()
                return raw if isinstance(raw, str) else None
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return None

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _decode(part)
            elif ctype == "text/html" and html is None:
                html = _decode(part)
    else:
        ctype = msg.get_content_type()
        decoded = _decode(msg)
        if decoded:
            if ctype == "text/html":
                html = decoded
            else:
                plain = decoded

    return {"plain": plain, "html": html}


# ---------------------------------------------------------------------------
# HTML → Markdown converter
# ---------------------------------------------------------------------------


# Tags that we should drop entirely along with their content (security + noise).
_DROP_TAGS = {"script", "style", "head", "meta", "link"}

# Block tags — force a newline before/after when closing.
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "table", "tr",
    "ul", "ol", "li",
    "hr",
}


class _MarkdownEmitter(HTMLParser):
    """HTML parser that emits a Markdown string.

    Single-pass converter — keeps a small state stack of list / quote /
    pre / link contexts so nested structures (``<ol><li><b>X</b></li>``)
    come out correctly.  Output is post-processed once at the end to
    collapse runaway blank lines.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop_stack: list[str] = []     # currently inside <script>/<style>/<head>?
        self._list_stack: list[dict] = []    # [{"type": "ul"|"ol", "n": int}, ...]
        self._quote_depth: int = 0           # nested <blockquote>
        self._pre_depth: int = 0             # currently inside <pre>?
        self._link_href: Optional[str] = None
        self._link_buf: Optional[list[str]] = None  # text accumulator inside <a>

    # ── Output helpers ────────────────────────────────────────────────

    def _emit(self, s: str) -> None:
        if self._drop_stack:
            return
        if self._link_buf is not None:
            self._link_buf.append(s)
            return
        self._out.append(s)

    def _emit_newline(self, n: int = 1) -> None:
        self._emit("\n" * n)

    def _quote_prefix(self) -> str:
        return ("> " * self._quote_depth) if self._quote_depth else ""

    # ── Tag handlers ──────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs):  # type: ignore[override]
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._drop_stack.append(tag)
            return
        if self._drop_stack:
            return

        attr = dict(attrs)

        if tag == "br":
            self._emit_newline(1)
            self._emit(self._quote_prefix())
            return

        if tag == "hr":
            self._emit_newline(2)
            self._emit("---")
            self._emit_newline(2)
            return

        if tag in {"p", "div", "section", "article", "header", "footer"}:
            self._emit_newline(2)
            self._emit(self._quote_prefix())
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._emit_newline(2)
            self._emit("#" * level + " ")
            return

        if tag == "blockquote":
            self._quote_depth += 1
            self._emit_newline(2)
            self._emit(self._quote_prefix())
            return

        if tag == "pre":
            self._pre_depth += 1
            self._emit_newline(2)
            self._emit("```\n")
            return

        if tag == "code" and self._pre_depth == 0:
            self._emit("`")
            return

        if tag in {"b", "strong"}:
            self._emit("**")
            return

        if tag in {"i", "em"}:
            self._emit("*")
            return

        if tag in {"s", "strike", "del"}:
            self._emit("~~")
            return

        if tag == "ul":
            self._list_stack.append({"type": "ul", "n": 0})
            self._emit_newline(1)
            return

        if tag == "ol":
            self._list_stack.append({"type": "ol", "n": 0})
            self._emit_newline(1)
            return

        if tag == "li":
            self._emit_newline(1)
            self._emit(self._quote_prefix())
            depth = max(0, len(self._list_stack) - 1)
            self._emit("  " * depth)
            if self._list_stack and self._list_stack[-1]["type"] == "ol":
                self._list_stack[-1]["n"] += 1
                self._emit(f"{self._list_stack[-1]['n']}. ")
            else:
                self._emit("- ")
            return

        if tag == "a":
            self._link_href = (attr.get("href") or "").strip()
            self._link_buf = []
            return

        if tag == "img":
            alt = (attr.get("alt") or "").strip()
            src = (attr.get("src") or "").strip()
            if src:
                # Skip data: URIs and tracking pixels — keep alt-text if any.
                if src.startswith("data:") or src.startswith("cid:"):
                    if alt:
                        self._emit(f"[{alt}]")
                else:
                    self._emit(f"![{alt}]({src})")
            elif alt:
                self._emit(alt)
            return

        # tr/td/th — table cells.  We don't try to render Markdown tables
        # (most clients won't), but separate cells with spaces.
        if tag in {"tr"}:
            self._emit_newline(1)
            self._emit(self._quote_prefix())
            return
        if tag in {"td", "th"}:
            self._emit("  ")
            return

    def handle_endtag(self, tag: str):  # type: ignore[override]
        tag = tag.lower()

        if self._drop_stack:
            if self._drop_stack[-1] == tag:
                self._drop_stack.pop()
            return

        if tag in {"p", "div", "section", "article", "header", "footer"}:
            self._emit_newline(1)
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._emit_newline(2)
            return

        if tag == "blockquote":
            if self._quote_depth > 0:
                self._quote_depth -= 1
            self._emit_newline(2)
            return

        if tag == "pre":
            if self._pre_depth > 0:
                self._pre_depth -= 1
            self._emit("\n```\n")
            return

        if tag == "code" and self._pre_depth == 0:
            self._emit("`")
            return

        if tag in {"b", "strong"}:
            self._emit("**")
            return

        if tag in {"i", "em"}:
            self._emit("*")
            return

        if tag in {"s", "strike", "del"}:
            self._emit("~~")
            return

        if tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._emit_newline(2)
            return

        if tag == "a" and self._link_buf is not None:
            label = "".join(self._link_buf).strip()
            href = self._link_href or ""
            self._link_buf = None
            self._link_href = None
            if href and label and href != label:
                self._emit(f"[{label}]({href})")
            elif href:
                self._emit(href)
            elif label:
                self._emit(label)
            return

    # ── Text ──────────────────────────────────────────────────────────

    def handle_data(self, data: str):  # type: ignore[override]
        if self._drop_stack:
            return
        if self._pre_depth > 0:
            self._emit(data)
            return
        # Inside the body, collapse runs of whitespace inside text nodes
        # but keep at least one space — Apple Mail HTML uses ``&nbsp;``
        # and pad spaces aggressively.
        collapsed = re.sub(r"[ \t\f\v]+", " ", data)
        collapsed = collapsed.replace("\xa0", " ")
        # Don't collapse "\n" inside data — block tags handle layout.
        self._emit(collapsed)

    # ── Final output ──────────────────────────────────────────────────

    def get_markdown(self) -> str:
        text = "".join(self._out)
        # Decode any stray entities (convert_charrefs handles most).
        text = _html.unescape(text)
        # Trim each line so the quote prefix doesn't carry trailing spaces.
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        # Collapse 3+ blank lines into 2.
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to readable Markdown.

    Safe for arbitrary input — ``<script>`` and ``<style>`` are dropped,
    unknown tags are silently ignored, output is whitespace-normalised.
    """
    if not html or not isinstance(html, str):
        return ""
    parser = _MarkdownEmitter()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # html.parser is permissive but some pathological inputs (extremely
        # malformed Outlook HTML) can still trip it.  Return the partial
        # output collected so far rather than empty.
        pass
    return parser.get_markdown()


# ---------------------------------------------------------------------------
# Composite helper
# ---------------------------------------------------------------------------


def source_to_markdown(source: str) -> str:
    """Convert an RFC822 source to Markdown.

    Strategy:
      1. Parse MIME via ``extract_body_from_source``.
      2. If a ``text/html`` part is present, convert with
         :func:`html_to_markdown`.
      3. Else fall back to the ``text/plain`` part (cleaned of
         trailing whitespace).
      4. Empty string if neither part exists.
    """
    bodies = extract_body_from_source(source)
    html = bodies.get("html")
    plain = bodies.get("plain")
    if html:
        md = html_to_markdown(html)
        if md.strip():
            return md
    if plain:
        # Normalise: strip Windows line endings, trim each line, collapse
        # leading/trailing whitespace.
        plain = plain.replace("\r\n", "\n").replace("\r", "\n")
        plain = "\n".join(line.rstrip() for line in plain.split("\n"))
        return plain.strip()
    return ""
