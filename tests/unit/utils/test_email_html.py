"""
Tests for ``utils.email_html`` — the HTML-email → Markdown converter that
preserves formatting from Apple Mail (bullet lists, bold, links, quoted
replies) into the vault.

Three layers of coverage:

  * ``html_to_markdown`` — direct HTML → MD conversions, one tag class
    per test so failures pinpoint the broken bit of the parser.
  * ``extract_body_from_source`` — MIME extraction from realistic
    RFC822 mail dumps (single-part, multipart/alternative, multipart/mixed).
  * ``source_to_markdown`` — end-to-end composition (RFC822 → MD).

Plus a couple of safety / robustness checks (XSS, malformed input,
empty / None / non-string).
"""

from __future__ import annotations

import textwrap

import pytest

from personal_assistant.utils.email_html import (
    extract_body_from_source,
    html_to_markdown,
    source_to_markdown,
)


# ----------------------------------------------------------------------
# html_to_markdown — inline tags
# ----------------------------------------------------------------------


def test_bold_strong():
    assert "**hi**" in html_to_markdown("<b>hi</b>")
    assert "**hi**" in html_to_markdown("<strong>hi</strong>")


def test_italic_em():
    assert "*hi*" in html_to_markdown("<i>hi</i>")
    assert "*hi*" in html_to_markdown("<em>hi</em>")


def test_strikethrough():
    assert "~~old~~" in html_to_markdown("<del>old</del>")
    assert "~~old~~" in html_to_markdown("<s>old</s>")


def test_inline_code():
    assert "`x`" in html_to_markdown("<code>x</code>")


def test_link_with_label():
    md = html_to_markdown('<a href="https://ex.com">click</a>')
    assert "[click](https://ex.com)" in md


def test_link_same_as_href_collapses_to_url():
    """When href == label, output the URL alone (no `[url](url)` noise)."""
    md = html_to_markdown('<a href="https://ex.com">https://ex.com</a>')
    assert md == "https://ex.com"


def test_image_with_alt():
    md = html_to_markdown('<img src="https://ex.com/x.png" alt="diagram">')
    assert "![diagram](https://ex.com/x.png)" in md


def test_image_data_uri_keeps_alt_drops_src():
    md = html_to_markdown('<img src="data:image/png;base64,xxx" alt="logo">')
    assert "logo" in md
    assert "data:" not in md


def test_image_cid_attachment_keeps_alt():
    md = html_to_markdown('<img src="cid:image001@01D9.AB" alt="chart">')
    assert "chart" in md
    assert "cid:" not in md


# ----------------------------------------------------------------------
# html_to_markdown — block tags
# ----------------------------------------------------------------------


def test_paragraphs():
    md = html_to_markdown("<p>First.</p><p>Second.</p>")
    assert "First." in md
    assert "Second." in md
    # Blank line between paragraphs
    assert "\n\n" in md


def test_br_becomes_newline():
    md = html_to_markdown("Line 1<br>Line 2<br>Line 3")
    assert "Line 1" in md
    assert "Line 2" in md
    assert md.count("\n") >= 2


def test_unordered_list():
    md = html_to_markdown("<ul><li>A</li><li>B</li><li>C</li></ul>")
    assert "- A" in md
    assert "- B" in md
    assert "- C" in md


def test_ordered_list():
    md = html_to_markdown("<ol><li>First</li><li>Second</li><li>Third</li></ol>")
    assert "1. First" in md
    assert "2. Second" in md
    assert "3. Third" in md


def test_nested_lists_indent():
    md = html_to_markdown(
        "<ul><li>Outer<ul><li>Inner1</li><li>Inner2</li></ul></li></ul>"
    )
    assert "- Outer" in md
    # Inner items should have 2-space indent
    assert "  - Inner1" in md


def test_blockquote():
    md = html_to_markdown("<blockquote>quoted text</blockquote>")
    assert "> quoted text" in md


def test_nested_blockquote():
    md = html_to_markdown(
        "<blockquote>outer<blockquote>inner</blockquote></blockquote>"
    )
    # Inner gets double quote prefix
    assert "> > inner" in md or ">> inner" in md.replace(" ", "")


def test_headings():
    md = html_to_markdown("<h1>Title</h1><h2>Sub</h2><h3>Sub2</h3>")
    assert "# Title" in md
    assert "## Sub" in md
    assert "### Sub2" in md


def test_hr():
    md = html_to_markdown("Above<hr>Below")
    assert "---" in md


def test_pre_code_block():
    md = html_to_markdown(
        "<pre><code>def foo():\n    return 42</code></pre>"
    )
    assert "```" in md
    assert "def foo():" in md
    assert "    return 42" in md


# ----------------------------------------------------------------------
# Security / robustness
# ----------------------------------------------------------------------


def test_script_dropped():
    md = html_to_markdown(
        "Hello<script>alert('xss')</script>World"
    )
    assert "<script>" not in md
    assert "alert" not in md
    assert "Hello" in md
    assert "World" in md


def test_style_dropped():
    md = html_to_markdown(
        "<style>body { color: red; }</style>visible"
    )
    assert "color: red" not in md
    assert "visible" in md


def test_head_dropped():
    md = html_to_markdown(
        "<html><head><title>X</title></head><body>body</body></html>"
    )
    assert "X" not in md
    assert "body" in md


def test_html_entities_decoded():
    md = html_to_markdown("AT&amp;T &lt;3 &quot;email&quot;")
    assert "AT&T" in md
    assert "<3" in md
    assert '"email"' in md


def test_nbsp_normalised():
    md = html_to_markdown("hello&nbsp;world")
    assert "hello world" in md
    assert "\xa0" not in md


def test_empty_input():
    assert html_to_markdown("") == ""
    assert html_to_markdown(None) == ""  # type: ignore[arg-type]


def test_whitespace_collapse():
    md = html_to_markdown("<p>too      many     spaces</p>")
    assert "many     " not in md  # collapsed to single
    assert "too many spaces" in md


# ----------------------------------------------------------------------
# Realistic Apple Mail / Outlook patterns
# ----------------------------------------------------------------------


def test_realistic_outlook_html():
    """Outlook-style HTML with mso-* attributes — most content should
    survive, vendor CSS should be dropped."""
    src = textwrap.dedent("""
        <html><head>
        <style>p.MsoNormal { margin: 0; }</style>
        </head><body>
        <p class="MsoNormal">Привет, Иван!</p>
        <p class="MsoNormal"><b>Прошу согласовать</b> бюджет на Q3.</p>
        <ul>
          <li>Расходы: 2.5М</li>
          <li>Доходы: 3.2М</li>
        </ul>
        <p>С уважением,<br>Анна</p>
        </body></html>
    """).strip()
    md = html_to_markdown(src)
    assert "Привет, Иван!" in md
    assert "**Прошу согласовать**" in md
    assert "- Расходы: 2.5М" in md
    assert "- Доходы: 3.2М" in md
    assert "Анна" in md
    # Vendor CSS dropped
    assert "MsoNormal" not in md
    assert "margin: 0" not in md


def test_apple_mail_reply_with_blockquote():
    """Apple Mail wraps quoted replies in <blockquote type=\"cite\">."""
    src = (
        '<div>My reply.</div>'
        '<blockquote type="cite">'
        '<div>On May 26, 2026, Alice wrote:</div>'
        '<div>Original message body.</div>'
        '</blockquote>'
    )
    md = html_to_markdown(src)
    assert "My reply." in md
    assert "> " in md
    assert "On May 26, 2026, Alice wrote:" in md
    assert "Original message body." in md


# ----------------------------------------------------------------------
# extract_body_from_source — MIME parsing
# ----------------------------------------------------------------------


def test_extract_plain_only():
    src = textwrap.dedent("""\
        From: alice@ex.com
        To: ivan@ex.com
        Subject: Hello
        Content-Type: text/plain; charset=utf-8

        Just plain text body.
    """)
    bodies = extract_body_from_source(src)
    assert bodies["plain"]
    assert "Just plain text body." in bodies["plain"]
    assert bodies["html"] is None


def test_extract_html_only():
    src = textwrap.dedent("""\
        From: alice@ex.com
        Content-Type: text/html; charset=utf-8

        <p><b>HTML</b> body.</p>
    """)
    bodies = extract_body_from_source(src)
    assert bodies["html"]
    assert "<b>HTML</b>" in bodies["html"]
    assert bodies["plain"] is None


def test_extract_multipart_alternative_prefers_both():
    src = textwrap.dedent("""\
        From: alice@ex.com
        Subject: Test
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="BOUND"

        --BOUND
        Content-Type: text/plain; charset=utf-8

        Plain version.

        --BOUND
        Content-Type: text/html; charset=utf-8

        <p>HTML <b>version</b>.</p>

        --BOUND--
    """)
    bodies = extract_body_from_source(src)
    assert bodies["plain"] and "Plain version." in bodies["plain"]
    assert bodies["html"] and "<b>version</b>" in bodies["html"]


def test_extract_handles_garbage_input():
    """Malformed input must NOT raise — returns empty dict."""
    bodies = extract_body_from_source("not an email at all")
    # Either None values or one of them set to the input (parser tries
    # to interpret as headerless body); both shapes are acceptable as
    # long as no exception escapes.
    assert isinstance(bodies, dict)
    assert "plain" in bodies and "html" in bodies


def test_extract_empty_input():
    assert extract_body_from_source("")   == {"plain": None, "html": None}
    assert extract_body_from_source(None) == {"plain": None, "html": None}  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# source_to_markdown — end-to-end
# ----------------------------------------------------------------------


def test_source_to_markdown_prefers_html():
    """When both parts are present, HTML wins (we get richer Markdown
    out of it than from the plain-text alternative)."""
    src = textwrap.dedent("""\
        From: alice@ex.com
        Subject: Test
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="X"

        --X
        Content-Type: text/plain; charset=utf-8

        Just plain.

        --X
        Content-Type: text/html; charset=utf-8

        <p><b>Bold</b> in HTML.</p>

        --X--
    """)
    md = source_to_markdown(src)
    # HTML path: **Bold** appears, plain-only marker doesn't
    assert "**Bold**" in md
    assert "Just plain." not in md


def test_source_to_markdown_falls_back_to_plain():
    src = textwrap.dedent("""\
        From: alice@ex.com
        Content-Type: text/plain; charset=utf-8

        Plain body line 1.
        Plain body line 2.
    """)
    md = source_to_markdown(src)
    assert "Plain body line 1." in md
    assert "Plain body line 2." in md


def test_source_to_markdown_empty_returns_empty():
    assert source_to_markdown("")   == ""
    assert source_to_markdown(None) == ""  # type: ignore[arg-type]


def test_source_to_markdown_realistic_russian_email():
    src = textwrap.dedent("""\
        From: anna@romashka.ru
        To: ivan@example.com
        Subject: =?utf-8?B?0JHRjtC00LbQtdGCIFEz?=
        MIME-Version: 1.0
        Content-Type: multipart/alternative; boundary="ABC"

        --ABC
        Content-Type: text/plain; charset=utf-8

        Иван, привет!

        Прошу согласовать бюджет на Q3 до пятницы.

        С уважением,
        Анна

        --ABC
        Content-Type: text/html; charset=utf-8

        <html><body>
        <p>Иван, привет!</p>
        <p>Прошу <b>согласовать</b> бюджет на Q3 до пятницы:</p>
        <ul>
          <li>Расходы: 2 500 000 руб</li>
          <li>Доходы: 3 200 000 руб</li>
        </ul>
        <p>Подробности на <a href="https://wiki.example.com/q3">странице вики</a>.</p>
        <p>С уважением,<br>Анна</p>
        </body></html>

        --ABC--
    """)
    md = source_to_markdown(src)
    assert "Иван, привет!" in md
    assert "**согласовать**" in md
    assert "- Расходы: 2 500 000 руб" in md
    assert "- Доходы: 3 200 000 руб" in md
    assert "[странице вики](https://wiki.example.com/q3)" in md
    assert "Анна" in md


# ----------------------------------------------------------------------
# mail_reader._convert — integration with the heuristic
# ----------------------------------------------------------------------


def test_mail_reader_converts_rfc822_body_to_markdown():
    """When the body field arrives as raw RFC822, ``_convert`` should
    detect it and run the conversion — bullet lists / bold survive."""
    from personal_assistant.readers.mail_reader import (
        MailReader,
        _looks_like_rfc822,
    )

    src = textwrap.dedent("""\
        From: alice@ex.com
        Subject: Test
        MIME-Version: 1.0
        Content-Type: text/html; charset=utf-8

        <p>Hi <b>Ivan</b>!</p><ul><li>One</li><li>Two</li></ul>
    """)
    assert _looks_like_rfc822(src)

    reader = MailReader()
    msg = reader._convert({
        "id": "1",
        "subject": "Test",
        "sender": "Alice <alice@ex.com>",
        "date": "2026-05-26T12:00:00",
        "body": src,
        "recipients": "",
        "cc": "",
        "mailbox": "INBOX",
        "has_attachments": False,
        "attachment_names": "",
    })
    assert msg.body
    assert "**Ivan**" in msg.body
    assert "- One" in msg.body
    assert "- Two" in msg.body
    # The RFC822 headers must NOT leak into the visible body
    assert "MIME-Version" not in msg.body
    assert "Content-Type:" not in msg.body


def test_mail_reader_keeps_plain_body_untouched():
    """Non-RFC822 bodies (the legacy ``content of msg`` path) pass through
    unchanged — heuristic must not mis-fire."""
    from personal_assistant.readers.mail_reader import MailReader

    reader = MailReader()
    msg = reader._convert({
        "id": "2",
        "subject": "Plain",
        "sender": "Bob <bob@ex.com>",
        "date": "2026-05-26T12:00:00",
        "body": "Just a plain body, no headers.\n\nSecond paragraph.",
        "recipients": "",
        "cc": "",
        "mailbox": "INBOX",
        "has_attachments": False,
        "attachment_names": "",
    })
    assert msg.body
    assert "Just a plain body" in msg.body
    assert "Second paragraph." in msg.body


def test_looks_like_rfc822_heuristic_negatives():
    """Things that look like prose must NOT trigger the heuristic."""
    from personal_assistant.readers.mail_reader import _looks_like_rfc822
    assert not _looks_like_rfc822("")
    assert not _looks_like_rfc822("Hi Ivan, thanks!")
    assert not _looks_like_rfc822("From the desk of …")  # 'From' but no colon-header
    assert not _looks_like_rfc822("a" * 100)


def test_looks_like_rfc822_heuristic_positives():
    from personal_assistant.readers.mail_reader import _looks_like_rfc822
    assert _looks_like_rfc822("From: alice@ex.com\nSubject: hi\n\nbody")
    assert _looks_like_rfc822("Return-Path: <x@y.com>\nReceived: …")
    assert _looks_like_rfc822("MIME-Version: 1.0\nContent-Type: …")
    assert _looks_like_rfc822("Content-Type: text/html\n\n<html>…")
    # Leading whitespace tolerated
    assert _looks_like_rfc822("   Subject: hi\n\nbody")
