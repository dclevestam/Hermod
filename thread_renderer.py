"""HTML builder for the threaded chat-bubble reading pane."""

import html as html_lib
import re
import urllib.parse

# Parenthetical URL produced by html2text-style conversion, e.g.
# "Download receipt (https://stripe.com/…)". We hide the raw URL from
# display and make the preceding text the anchor.
_PAREN_URL_RE = re.compile(r'\s*\((https?://[^\s)]+)\)')
# Bare URL tokens in the extracted body — linkify to the domain.
_BARE_URL_RE = re.compile(r"(?<![\w@\"'=>])(https?://[^\s<>\"'()]+)")

try:
    from .utils import (
        _format_date, _format_received_date, _thread_day_label,
        _normalize_thread_subject, _sender_key, _sender_initials, _thread_palette,
    )
except ImportError:
    from utils import (
        _format_date, _format_received_date, _thread_day_label,
        _normalize_thread_subject, _sender_key, _sender_initials, _thread_palette,
    )


def _avatar_text_color(r, g, b):
    """Return '#ffffff' or '#1a1a1a' depending on which gives better contrast on rgb(r,g,b)."""
    def _linear(c):
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)
    return '#1a1a1a' if lum > 0.22 else '#ffffff'


def build_thread_html(selected_msg, subject, first_date, last_date, records, attachments, is_self_fn, theme='night'):
    """Return a complete HTML string for the thread bubble view.

    Args:
        selected_msg: The message dict that was clicked (used for context).
        subject, first_date, last_date: Thread metadata.
        records: List of dicts with 'msg', 'body_text', 'sender_color', 'sender_lane', 'selected', 'attachments'.
        attachments: Collected attachments for the whole thread.
        is_self_fn: Callable(msg) -> bool — True if the message sender is the current account.
        theme: kept for call-site compat; the reader surface now uses the
               dark hermod_surface_reader token to match the unified-dark
               target design.
    """
    page_bg = '#0B0F12'
    text = '#F2F1ED'
    subtext = '#A6ADB3'
    separator = 'rgba(166, 173, 179, 0.08)'
    ordered_records = list(records)
    bubbles = []
    last_day = None
    root_subject = (subject or '').strip()
    normalized_root_subject = _normalize_thread_subject(root_subject)
    for record in ordered_records:
        msg = record['msg']
        uid_raw = msg.get('uid') or ''
        uid = html_lib.escape(uid_raw)
        sender_name = html_lib.escape((msg.get('sender_name') or msg.get('sender_email') or 'Unknown').strip())
        sender_email = (msg.get('sender_email') or '').strip()
        when = html_lib.escape(_format_received_date(msg.get('date')) or _format_date(msg.get('date')) or '')
        body_text = html_lib.escape(record.get('body_text') or '(no content)')
        is_self = is_self_fn(msg)
        r, g, b = record.get('sender_color') or _thread_palette(sender_email or sender_name)
        bubble_bg = 'transparent'
        bubble_border = 'transparent'
        bubble_text = f'rgb({r}, {g}, {b})'
        align_class = 'self' if is_self else 'other'
        if record.get('selected'):
            align_class += ' selected'
        lane = record.get('sender_lane', 0)
        attachment_bits = [html_lib.escape(att.get('name', 'attachment')) for att in record.get('attachments') or []]
        attachment_html = ''
        if attachment_bits:
            attachment_html = (
                '<div class="bubble-footer">'
                '<div class="bubble-chip">Attachments</div>'
                f'<div class="bubble-chip">{", ".join(attachment_bits)}</div>'
                '</div>'
            )
        inline_images = list(record.get('inline_images') or [])
        inline_images_html = ''
        if inline_images:
            image_nodes = []
            for image in inline_images:
                src = html_lib.escape(image.get('src') or '')
                label = html_lib.escape(image.get('name') or 'inline image')
                width = int(image.get('width') or 0)
                height = int(image.get('height') or 0)
                image_nodes.append(
                    f'<figure class="bubble-inline-image">'
                    f'<img src="{src}" alt="{label}" loading="lazy" '
                    f'data-width="{width}" data-height="{height}" />'
                    f'</figure>'
                )
            inline_images_html = f'<div class="bubble-inline-images">{"".join(image_nodes)}</div>'
        current_subject = (msg.get('subject') or '').strip()
        subject_change_html = ''
        if current_subject and _normalize_thread_subject(current_subject) != normalized_root_subject:
            subject_change_html = (
                '<div class="bubble-subject-change">'
                f'<span class="bubble-subject-change-label">Subject changed</span> '
                f'{html_lib.escape(current_subject)}'
                '</div>'
            )
        msg_day = None
        try:
            msg_day = msg.get('date').astimezone().date() if msg.get('date') else None
        except Exception:
            msg_day = None
        day_separator = ''
        if msg_day is not None and msg_day != last_day:
            day_separator = f'<div class="thread-day-separator"><span class="thread-day-label">{html_lib.escape(_thread_day_label(msg.get("date")) or "")}</span></div>'
            last_day = msg_day
        initials = html_lib.escape(_sender_initials(msg.get('sender_name'), sender_email))
        sender_label = html_lib.escape((msg.get('sender_name') or msg.get('sender_email') or 'Unknown').strip())
        has_original = bool(record.get('html') or record.get('text'))
        original_button = ''
        if has_original:
            original_button = (
                f'<a class="bubble-original" href="hermod://original?uid={urllib.parse.quote(uid_raw, safe="")}" '
                f'title="View original" role="button">'
                f'&#x2197;'
                f'</a>'
            )
        avatar_fg = _avatar_text_color(r, g, b)
        bubbles.append(
            f'''
            {day_separator}
            <article id="msg-{uid}" class="bubble {align_class}" style="
                --bubble-bg: {bubble_bg};
                --bubble-border: {bubble_border};
                --bubble-text: {text};
                --bubble-accent: {bubble_text};
                --avatar-fg: {avatar_fg};
                --sender-lane: {lane};
            ">
                <div class="bubble-head">
                    <div class="bubble-head-left">
                        <div class="bubble-strip"></div>
                        <div class="bubble-avatar">{initials}</div>
                        <div class="bubble-sender">{sender_label}</div>
                    </div>
                    <div class="bubble-head-right">
                        <div class="bubble-time">{when}</div>
                        {original_button}
                    </div>
                </div>
                {subject_change_html}
                <div class="bubble-body">{body_text}</div>
                {inline_images_html}
                {attachment_html}
            </article>
            '''
        )
    return f'''
    <html>
    <head>
        <meta charset="utf-8" />
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                background: {page_bg};
                color: {text};
                font-family: "DejaVu Sans", -apple-system, system-ui, sans-serif;
            }}
            body {{
                padding: 20px 18px 28px;
            }}
            .thread-shell {{
                width: 100%;
                max-width: none;
                margin: 0 auto;
            }}
            .bubble {{
                max-width: none;
                border-radius: 0;
                border: 0;
                background: transparent;
                color: {text};
                padding: 14px 4px 18px 0;
                margin: 0;
                box-shadow: none;
                border-bottom: 1px solid {separator};
            }}
            .bubble:last-child {{
                border-bottom: 0;
            }}
            .bubble.self, .bubble.other {{
                max-width: none;
                margin: 0;
            }}
            .bubble.selected {{
                box-shadow: inset 2px 0 0 0 rgba(46, 106, 112, 0.9);
                padding-left: 10px;
            }}
            .bubble-head {{
                display: flex;
                justify-content: space-between;
                gap: 16px;
                margin-bottom: 7px;
                align-items: baseline;
            }}
            .bubble-head-left {{
                display: flex;
                align-items: center;
                gap: 9px;
                min-width: 0;
            }}
            .bubble-head-right {{
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .bubble-strip {{
                display: none;
            }}
            .bubble-avatar {{
                width: 26px;
                height: 26px;
                min-width: 26px;
                min-height: 26px;
                border-radius: 999px;
                background: var(--bubble-accent);
                color: var(--avatar-fg, #ffffff);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 0.72rem;
                font-weight: 800;
                letter-spacing: 0.03em;
                flex: none;
            }}
            .bubble-sender {{
                font-weight: 700;
                color: {text};
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .bubble-time {{
                color: {subtext};
                font-size: 0.82rem;
                white-space: nowrap;
                flex: none;
            }}
            .bubble-body {{
                white-space: pre-wrap;
                line-height: 1.55;
                font-size: 0.95rem;
                color: {text};
            }}
            .bubble-subject-change {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                margin: 0 0 9px;
                padding: 4px 10px;
                border-radius: 999px;
                background: rgba(223, 228, 222, 0.06);
                color: {subtext};
                font-size: 0.78rem;
                font-weight: 600;
            }}
            .bubble-subject-change-label {{
                color: var(--bubble-accent);
                font-weight: 800;
                letter-spacing: 0.02em;
                text-transform: uppercase;
                font-size: 0.70rem;
            }}
            .bubble-inline-images {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                gap: 10px;
                margin-top: 10px;
                max-width: min(360px, 100%);
            }}
            .bubble-inline-image {{
                margin: 0;
                border-radius: 14px;
                overflow: hidden;
                background: rgba(223, 228, 222, 0.04);
                border: 1px solid rgba(223, 228, 222, 0.10);
                min-height: 92px;
                max-height: 230px;
            }}
            .bubble-inline-image img {{
                display: block;
                width: 100%;
                height: 100%;
                min-height: 92px;
                max-height: 230px;
                object-fit: cover;
                background: rgba(255,255,255,0.04);
            }}
            .bubble-footer {{
                margin-top: 8px;
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
            }}
            .bubble-chip {{
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 4px 8px;
                border-radius: 999px;
                background: rgba(223, 228, 222, 0.06);
                color: {subtext};
                font-size: 0.74rem;
                font-weight: 700;
            }}
            .bubble-attachments {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin-top: 10px;
                padding-top: 10px;
                border-top: 1px solid rgba(223, 228, 222, 0.10);
                color: {subtext};
                font-size: 0.82rem;
            }}
            .bubble-attachment-label {{
                font-weight: 700;
                color: {text};
            }}
            .bubble-attachment-list {{
                color: {subtext};
            }}
            .thread-day-separator {{
                display: flex;
                justify-content: center;
                margin: 16px 0 14px;
            }}
            .thread-day-label {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 4px 12px;
                border-radius: 999px;
                background-color: rgba(223, 228, 222, 0.06);
                color: {subtext};
                font-size: 0.78rem;
                font-weight: 700;
            }}
            .bubble-original {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                border: 1px solid rgba(223, 228, 222, 0.14);
                background: rgba(223, 228, 222, 0.06);
                color: var(--bubble-text);
                border-radius: 999px;
                padding: 2px 12px;
                font-size: 0.72em;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                cursor: pointer;
                transition: background 120ms ease;
                text-decoration: none;
            }}
            .bubble-original:hover {{
                background: rgba(223, 228, 222, 0.12);
            }}
            a {{
                color: inherit;
            }}
        </style>
    </head>
    <body>
        <div class="thread-shell">
            {''.join(bubbles)}
            <div id="thread-end"></div>
        </div>
    </body>
    </html>
    '''


def thread_reply_msg_for_records(records, is_self_fn):
    """Return the last non-self message in the thread to reply to."""
    for record in reversed(records or []):
        msg = record.get('msg') or {}
        if not is_self_fn(msg):
            return msg
    return (records[-1].get('msg') if records else None)


def _split_anchor_from_pre(pre):
    """Given the text immediately before a `(URL)`, return
    (prefix, anchor) where `anchor` is the last sentence-fragment we
    can plausibly use as the link label. Default: the last 1–5 words
    after the closest sentence/line boundary. Whitespace that is *not*
    part of the anchor stays in the prefix so consecutive links don't
    visually run together."""
    if not pre:
        return '', ''
    boundary = max(
        pre.rfind('\n'),
        pre.rfind('. '),
        pre.rfind('! '),
        pre.rfind('? '),
        pre.rfind(': '),
        pre.rfind('; '),
        pre.rfind(' > '),
    )
    if boundary >= 0:
        split_at = boundary + 1
        while split_at < len(pre) and pre[split_at] == ' ':
            split_at += 1
        prefix = pre[:split_at]
        anchor_raw = pre[split_at:]
    else:
        prefix = ''
        anchor_raw = pre
    # Preserve leading whitespace from the anchor span in the prefix
    # so that `"A. (url) B. (url)"` renders with the space between the
    # two links intact.
    leading_ws = anchor_raw[: len(anchor_raw) - len(anchor_raw.lstrip())]
    prefix += leading_ws
    anchor_raw = anchor_raw.lstrip()
    words = anchor_raw.split()
    if len(words) > 5:
        # Anchor is too long — keep the last 5 words, push the rest
        # back into the prefix so we don't lose any text.
        prefix += ' '.join(words[:-5]) + ' '
        anchor = ' '.join(words[-5:])
    else:
        anchor = anchor_raw.rstrip()
    if not anchor or len(anchor) > 80:
        return pre, ''
    return prefix, anchor


def _shorten_url_for_display(url):
    """Return domain-only display for bare URLs, with a trailing / if
    the URL has a path so the eye can tell the difference."""
    m = re.match(r'https?://([^/]+)(/?)', url)
    if not m:
        return url if len(url) <= 50 else url[:49] + '…'
    domain = m.group(1)
    return domain + ('/…' if m.group(2) else '')


def _linkify_clean_body(body_text):
    """Transform plain body text into a readable HTML fragment with
    real anchor tags. Text spans are escaped; URL patterns become
    `<a>` tags whose visible label is the preceding anchor text (or
    the URL's domain when no anchor is available). Raw `(https://…)`
    URL noise — common in receipts generated from HTML-to-text — is
    absorbed into the link so it no longer clutters the view."""
    out = []
    cursor = 0
    text = body_text or ''
    n = len(text)

    def escape_and_nl(s):
        # newlines preserved via CSS white-space: pre-wrap; escape HTML
        return html_lib.escape(s)

    while cursor < n:
        paren = _PAREN_URL_RE.search(text, cursor)
        bare = _BARE_URL_RE.search(text, cursor)

        # Choose the earliest match; paren wins ties (because bare also
        # matches URLs inside parentheses).
        candidates = []
        if paren is not None:
            candidates.append((paren.start(), 'paren', paren))
        if bare is not None:
            candidates.append((bare.start(), 'bare', bare))
        if not candidates:
            out.append(escape_and_nl(text[cursor:]))
            break
        candidates.sort(key=lambda t: (t[0], 0 if t[1] == 'paren' else 1))
        _, kind, m = candidates[0]

        if kind == 'paren':
            pre_text = text[cursor:m.start()]
            prefix, anchor = _split_anchor_from_pre(pre_text)
            url = m.group(1)
            url_attr = html_lib.escape(url, quote=True)
            if anchor:
                out.append(escape_and_nl(prefix))
                out.append(
                    f'<a href="{url_attr}" title="{url_attr}">'
                    f'{html_lib.escape(anchor)}</a>'
                )
            else:
                # No plausible anchor — keep the preceding text
                # unchanged and link the domain as its own token.
                out.append(escape_and_nl(prefix))
                out.append(
                    f' <a href="{url_attr}" title="{url_attr}">'
                    f'{html_lib.escape(_shorten_url_for_display(url))}</a>'
                )
            cursor = m.end()
        else:
            out.append(escape_and_nl(text[cursor:m.start()]))
            url = m.group(1)
            url_attr = html_lib.escape(url, quote=True)
            out.append(
                f'<a href="{url_attr}" title="{url_attr}">'
                f'{html_lib.escape(_shorten_url_for_display(url))}</a>'
            )
            cursor = m.end()

    return ''.join(out)


def build_clean_body_html(body_text):
    """Wrap extracted (quoted-trimmed) plain text in a reader-matched
    document. The single-message reader uses this for its default
    "clean" view — same font/colors as the thread bubble body, no
    chrome, so the app-level message header (`.reader-header`) can own
    the sender/date line without duplicating it here."""
    page_bg = '#0B0F12'
    text_color = '#F2F1ED'
    link_color = '#9fb7b9'
    body_html = _linkify_clean_body(body_text or '')
    return f'''<html>
<head>
<meta charset="utf-8" />
<style>
html, body {{
    margin: 0;
    padding: 0;
    background: {page_bg};
    color: {text_color};
    font-family: "DejaVu Sans", -apple-system, system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.55;
}}
body {{ padding: 24px 26px 32px; }}
.clean-body {{
    white-space: pre-wrap;
    word-break: break-word;
    color: {text_color};
}}
a {{
    color: {link_color};
    text-decoration: none;
    border-bottom: 1px solid rgba(159, 183, 185, 0.35);
    padding-bottom: 1px;
}}
a:hover {{
    color: #c7dadc;
    border-bottom-color: rgba(199, 218, 220, 0.70);
}}
</style>
</head>
<body>
<div class="clean-body">{body_html}</div>
</body>
</html>'''
