"""HTML builder for the threaded chat-bubble reading pane."""

import html as html_lib
import urllib.parse

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
        theme: kept for call-site compat; reader body always uses the light
               surface since emails are authored for a white background.
    """
    # Email body surface is always light in both Night and Day themes — the
    # design prototype's Night inbox screenshot shows white reader body with
    # dark text. Only the surrounding app chrome themes dark.
    page_bg = '#ffffff'
    text = '#1b2024'
    subtext = '#5a6670'
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
        bubble_bg = f'rgba({r}, {g}, {b}, 0.18)'
        bubble_border = f'rgba({r}, {g}, {b}, 0.28)'
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
                max-width: 78%;
                border-radius: 20px;
                border: 1px solid var(--bubble-border);
                background: var(--bubble-bg);
                color: var(--bubble-text);
                padding: 12px 13px 11px;
                margin: 0 0 12px;
                box-shadow: 0 1px 2px rgba(0,0,0,0.10);
            }}
            .bubble.self {{
                max-width: 74%;
                margin-left: auto;
                margin-right: 4px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            .bubble.other {{
                max-width: 78%;
                margin-right: auto;
                margin-left: calc(4px + (var(--sender-lane, 0) * 10px));
                border-top-left-radius: 6px;
                border-bottom-left-radius: 6px;
            }}
            .bubble.selected {{
                box-shadow: 0 0 0 1px rgba(116, 164, 141, 0.36), 0 0 0 5px rgba(116, 164, 141, 0.10);
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
                width: 4px;
                min-width: 4px;
                height: 16px;
                border-radius: 999px;
                background: var(--bubble-accent);
                flex: none;
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
                color: var(--bubble-accent);
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
