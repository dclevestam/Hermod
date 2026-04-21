"""Pure module-level helpers shared across the Hermod window layer."""

import base64
import hashlib
import html as html_lib
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf

try:
    from .diagnostics.logger import (
        log_exception as _diag_log_exception,
        record_perf as _diag_record_perf,
        should_print_debug_tracebacks,
    )
    from .styles import ACCOUNT_PALETTE
    from .settings import get_settings, get_disk_cache_budget_limit_mb
except ImportError:
    from diagnostics.logger import (
        log_exception as _diag_log_exception,
        record_perf as _diag_record_perf,
        should_print_debug_tracebacks,
    )
    from styles import ACCOUNT_PALETTE
    from settings import get_settings, get_disk_cache_budget_limit_mb


# ── Folder sentinel values ────────────────────────────────────────────────────

_UNIFIED         = '__unified__'
_UNIFIED_TRASH   = '__unified_trash__'
_UNIFIED_SPAM    = '__unified_spam__'
_UNIFIED_FLAGGED = '__unified_flagged__'
_UNIFIED_DRAFTS  = '__unified_drafts__'
_UNIFIED_SENT    = '__unified_sent__'
_UNIFIED_ARCHIVE = '__unified_archive__'

# ── Disk-cache paths ──────────────────────────────────────────────────────────

_DISK_BODY_CACHE_DIR  = Path(GLib.get_user_cache_dir()) / 'hermod' / 'body-cache'
_SNAPSHOT_CACHE_DIR   = Path(GLib.get_user_cache_dir()) / 'hermod' / 'message-snapshots'


# ── Date / time formatting ────────────────────────────────────────────────────

def _format_local_timestamp(dt):
    if dt is None:
        return ''
    try:
        local_dt = dt.astimezone()
    except Exception:
        return ''
    now = datetime.now().astimezone()
    try:
        if local_dt.date() == now.date():
            return local_dt.strftime('%H:%M')
        if now - local_dt > timedelta(days=365):
            return local_dt.strftime('%m/%d/%y - %H:%M')
        return local_dt.strftime('%b %-d %H:%M')
    except Exception:
        return ''


def _format_date(dt):
    return _format_local_timestamp(dt)


def _format_received_date(dt):
    return _format_local_timestamp(dt)


def _format_row_timestamp(dt):
    """Row-optimized compact timestamp: HH:MM today, 'Yesterday', 'Mon DD' older."""
    if dt is None:
        return ''
    try:
        local_dt = dt.astimezone()
    except Exception:
        return ''
    try:
        now = datetime.now().astimezone()
        today = now.date()
        day = local_dt.date()
    except Exception:
        return ''
    if day == today:
        return local_dt.strftime('%H:%M')
    yesterday = today - timedelta(days=1)
    if day == yesterday:
        return 'Yesterday'
    if now - local_dt > timedelta(days=365):
        return local_dt.strftime('%m/%d/%y')
    try:
        return local_dt.strftime('%b %-d')
    except Exception:
        return local_dt.strftime('%b %d')


def _day_group_label(dt):
    """Short uppercase day grouping label used by the message list."""
    if dt is None:
        return ''
    try:
        local_dt = dt.astimezone()
    except Exception:
        local_dt = dt
    try:
        now = datetime.now().astimezone()
    except Exception:
        return ''
    try:
        today = now.date()
        day = local_dt.date()
    except Exception:
        return ''
    if day == today:
        return 'TODAY'
    try:
        if day == (today - timedelta(days=1)):
            return 'YESTERDAY'
    except Exception:
        pass
    try:
        if day.year == today.year:
            return local_dt.strftime('%a %-d %b').upper()
        return local_dt.strftime('%-d %b %Y').upper()
    except Exception:
        try:
            return local_dt.strftime('%a %d %b').upper()
        except Exception:
            return ''


def _day_group_key(dt):
    """Stable grouping key (local date) for a message datetime."""
    if dt is None:
        return None
    try:
        local_dt = dt.astimezone()
    except Exception:
        local_dt = dt
    try:
        return local_dt.date().isoformat()
    except Exception:
        return None


def _thread_day_label(dt):
    if dt is None:
        return ''
    try:
        local_dt = dt.astimezone()
    except Exception:
        local_dt = dt
    try:
        return local_dt.strftime('%A, %B %-d, %Y')
    except Exception:
        try:
            return local_dt.strftime('%A, %d %B %Y')
        except Exception:
            return _format_date(dt)


# ── Size / icon utilities ─────────────────────────────────────────────────────

def _format_size(n):
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / 1024 / 1024:.1f} MB'


def _pick_icon_name(*candidates):
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    for name in candidates:
        if not name:
            continue
        if theme is None or theme.has_icon(name):
            return name
    for name in reversed(candidates):
        if name:
            return name
    return 'image-missing'


# ── Logging ───────────────────────────────────────────────────────────────────

def _log_exception(prefix, exc):
    _diag_log_exception(prefix, exc)
    if should_print_debug_tracebacks():
        print(f'{prefix}: {exc}', file=sys.stderr)
        traceback.print_exc()


def _perf_enabled():
    try:
        return bool(get_settings().get('debug_logging'))
    except Exception:
        return False


def _perf_counter():
    return time.perf_counter()


def _perf_elapsed_ms(started, finished=None):
    if finished is None:
        finished = time.perf_counter()
    try:
        return max(0.0, (float(finished) - float(started)) * 1000.0)
    except Exception:
        return 0.0


def _perf_message(kind, detail, elapsed_ms):
    base = f'Perf: {kind} {elapsed_ms:.1f}ms'
    if detail:
        return f'{base} ({detail})'
    return base


def _log_perf(kind, detail='', started=None, elapsed_ms=None):
    if elapsed_ms is None:
        if started is None:
            return None
        elapsed_ms = _perf_elapsed_ms(started)
    _diag_record_perf(kind, elapsed_ms)
    if not _perf_enabled():
        return None
    print(_perf_message(kind, detail, elapsed_ms), file=sys.stderr)
    return elapsed_ms


# ── Cache key / budget helpers ────────────────────────────────────────────────

def _body_cache_key(identity, folder, uid):
    raw = f'{identity}\0{folder or ""}\0{uid}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _disk_cache_budget_bytes():
    budget_mb = get_settings().get('disk_cache_budget_mb')
    limit_mb = get_disk_cache_budget_limit_mb()
    budget_mb = max(8, min(int(budget_mb), limit_mb))
    return budget_mb * 1024 * 1024


def _snapshot_scope(backend, folder):
    if folder == _UNIFIED:
        return 'unified-inbox'
    if backend and folder and folder.lower() == 'inbox':
        return f'{backend.identity}/inbox'
    return None


def _snapshot_path(scope):
    digest = hashlib.sha256(scope.encode('utf-8')).hexdigest()
    return _SNAPSHOT_CACHE_DIR / f'{digest}.json.gz'


# ── Attachment helpers ────────────────────────────────────────────────────────

def _attachment_content_id(att):
    cid = att.get('content_id') or ''
    return cid.strip().strip('<>').strip()


def _attachment_is_inline_image(att):
    ct = (att.get('content_type') or '').lower()
    if not ct.startswith('image/'):
        return False
    disp = (att.get('disposition') or '').lower()
    if disp == 'attachment':
        return False
    return True


def _attachment_cacheable(att):
    ct = (att.get('content_type') or '').lower()
    return ct.startswith('image/')


def _inline_image_data_uri(att):
    data = att.get('data') or b''
    ct = att.get('content_type') or 'application/octet-stream'
    if not data:
        return None
    return f'data:{ct};base64,{base64.b64encode(data).decode("ascii")}'


def _replace_cid_images(html, attachments):
    if not html or 'cid:' not in html.lower():
        return html
    cid_map = {}
    for att in attachments or []:
        cid = _attachment_content_id(att)
        if cid and _attachment_is_inline_image(att):
            uri = _inline_image_data_uri(att)
            if uri:
                cid_map[cid] = uri
    if not cid_map:
        return html

    def repl(match):
        cid = match.group(1).strip().strip('<>').strip()
        return cid_map.get(cid, match.group(0))

    return re.sub(r'cid:([^"\'>\s]+)', repl, html, flags=re.IGNORECASE)


def _image_dimensions_from_bytes(data):
    if not data:
        return 0, 0
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return 0, 0
        return pixbuf.get_width(), pixbuf.get_height()
    except Exception:
        return 0, 0


def _thread_inline_image_records(html, attachments, max_images=4):
    if not html:
        return []
    ordered_cids = []
    seen_cids = set()
    for match in re.finditer(r'(?is)<img\b[^>]+src=["\']cid:([^"\'>\s]+)', html):
        cid = (match.group(1) or '').strip().strip('<>').strip()
        if not cid or cid in seen_cids:
            continue
        seen_cids.add(cid)
        ordered_cids.append(cid)
    if not ordered_cids:
        return []

    cid_map = {}
    for att in attachments or []:
        cid = _attachment_content_id(att)
        if cid and _attachment_is_inline_image(att):
            cid_map[cid] = att

    decorative_tokens = (
        'logo', 'icon', 'avatar', 'footer', 'facebook', 'instagram',
        'linkedin', 'twitter', 'youtube', 'spacer', 'pixel',
    )
    records = []
    for cid in ordered_cids:
        att = cid_map.get(cid)
        if not att:
            continue
        uri = _inline_image_data_uri(att)
        if not uri:
            continue
        width, height = _image_dimensions_from_bytes(att.get('data') or b'')
        if width <= 0 or height <= 0:
            continue
        area = width * height
        if width < 96 or height < 72 or area < 12000:
            continue
        name_seed = ' '.join(
            str(att.get(key, '') or '').lower()
            for key in ('name', 'content_id')
        )
        if any(token in name_seed for token in decorative_tokens) and area < 60000:
            continue
        if width >= (height * 4) and height < 140:
            continue
        records.append({
            'src': uri,
            'name': att.get('name') or 'inline image',
            'width': width,
            'height': height,
            'content_id': cid,
        })
        if len(records) >= max_images:
            break
    return records


# ── GTK widget helpers ────────────────────────────────────────────────────────

def _make_count_slot():
    slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, width_request=24, halign=Gtk.Align.END)
    slot.set_hexpand(False)
    return slot


# ── Thread / message text helpers ────────────────────────────────────────────

def _normalize_thread_subject(subject):
    text = (subject or '').strip()
    if not text:
        return ''
    while True:
        new_text = re.sub(r'^(?:(?:re|fw|fwd)\s*:\s*)+', '', text, flags=re.IGNORECASE).strip()
        if new_text == text:
            return text.lower()
        text = new_text


def _html_to_text(html):
    if not html:
        return ''
    text = re.sub(r'(?is)<(script|style).*?>.*?</\1>', '', html)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</p>|</div>|</li>|</tr>|</h[1-6]>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = html_lib.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_thread_quotes(text):
    if not text:
        return ''
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    cleaned = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not cleaned and not stripped:
            continue
        if re.match(r'^(on .+ wrote:|from: .+|sent: .+|to: .+|subject: .+)$', stripped, re.IGNORECASE):
            break
        if stripped in ('--', '-- ', '__', '___'):
            break
        if stripped.startswith('-----Original Message-----'):
            break
        if stripped.startswith('>') and cleaned:
            break
        cleaned.append(line)
    result = '\n'.join(cleaned).strip()
    if not result:
        result = text.strip()
    return result


def _thread_message_summary(text, limit=92):
    if not text:
        return ''
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + '…'


# ── Sender / color utilities ──────────────────────────────────────────────────

def _rgb_to_hex(rgb):
    r, g, b = rgb
    return f'#{r:02x}{g:02x}{b:02x}'


def _sender_key(msg):
    sender_email = (msg.get('sender_email') or '').strip().lower()
    sender_name = (msg.get('sender_name') or '').strip().lower()
    return sender_email or sender_name or 'unknown'


def _sender_initials(name, email):
    text = (name or '').strip() or (email or '').strip()
    if not text:
        return '?'
    parts = [part for part in re.split(r'[\s._\-]+', text) if part]
    if len(parts) >= 2:
        initials = ''.join(part[0] for part in parts[:2])
    else:
        initials = ''.join(ch for ch in text if ch.isalnum())[:2]
    return (initials or '?').upper()


def _thread_palette(seed_text):
    palette = []
    for color in ACCOUNT_PALETTE:
        palette.append(tuple(int(color[offset:offset + 2], 16) for offset in (1, 3, 5)))
    idx = int(hashlib.sha256((seed_text or '').encode('utf-8')).hexdigest(), 16) % len(palette)
    return palette[idx]


def _thread_color_map(thread_seed, sender_keys):
    palette = []
    for color in ACCOUNT_PALETTE:
        palette.append(tuple(int(color[offset:offset + 2], 16) for offset in (1, 3, 5)))
    digest = hashlib.sha256((thread_seed or 'thread').encode('utf-8')).digest()
    order = list(range(len(palette)))
    order.sort(key=lambda idx: digest[idx % len(digest)])
    colors = [palette[idx] for idx in order]
    mapping = {}
    for idx, key in enumerate(sender_keys):
        mapping[key] = colors[idx % len(colors)]
    return mapping


def _email_background_hint(html, text, fallback_rgb):
    candidates = []
    sources = [html or '', text or '']
    patterns = [
        r'(?i)background(?:-color)?\s*:\s*(#[0-9a-f]{3,8}|rgb\([^)]+\)|rgba\([^)]+\))',
        r'(?i)bgcolor\s*=\s*["\']?(#[0-9a-f]{3,8}|[a-z]+)',
        r'(?i)background\s*=\s*["\']?(#[0-9a-f]{3,8}|[a-z]+)',
    ]
    for source in sources:
        for pattern in patterns:
            match = re.search(pattern, source)
            if match:
                candidates.append(match.group(1))
    for color in candidates:
        rgba = Gdk.RGBA()
        try:
            if rgba.parse(color):
                r = int(round(rgba.red * 255))
                g = int(round(rgba.green * 255))
                b = int(round(rgba.blue * 255))
                return (r, g, b)
        except Exception:
            continue
    return fallback_rgb


def _email_surface_hint(html, text):
    sources = [html or '', text or '']
    patterns = [
        r'(?i)background(?:-color)?\s*:\s*(#[0-9a-f]{3,8}|rgb\([^)]+\)|rgba\([^)]+\))',
        r'(?i)bgcolor\s*=\s*["\']?(#[0-9a-f]{3,8}|[a-z]+)',
        r'(?i)background\s*=\s*["\']?(#[0-9a-f]{3,8}|[a-z]+)',
    ]
    text_patterns = [
        r'(?i)(?:^|[\s;{])color\s*:\s*(#[0-9a-f]{3,8}|rgb\([^)]+\)|rgba\([^)]+\))',
        r'(?i)fgcolor\s*=\s*["\']?(#[0-9a-f]{3,8}|[a-z]+)',
    ]

    def _linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _contrast_fg(rgb):
        r, g, b = rgb
        sr = r / 255.0
        sg = g / 255.0
        sb = b / 255.0
        luminance = 0.2126 * _linear(sr) + 0.7152 * _linear(sg) + 0.0722 * _linear(sb)
        return (17, 17, 17) if luminance > 0.55 else (255, 255, 255)

    def _parse_rgb(color):
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            return None
        r = int(round(rgba.red * 255))
        g = int(round(rgba.green * 255))
        b = int(round(rgba.blue * 255))
        sr = r / 255.0
        sg = g / 255.0
        sb = b / 255.0
        luminance = 0.2126 * _linear(sr) + 0.7152 * _linear(sg) + 0.0722 * _linear(sb)
        return (r, g, b, luminance)

    for source in sources:
        bg_candidates = []
        fg_candidates = []
        for pattern in patterns:
            for match in re.finditer(pattern, source):
                parsed = _parse_rgb(match.group(1))
                if parsed is not None:
                    bg_candidates.append(parsed)
        for pattern in text_patterns:
            for match in re.finditer(pattern, source):
                parsed = _parse_rgb(match.group(1))
                if parsed is not None:
                    fg_candidates.append(parsed)

        for r, g, b, luminance in bg_candidates:
            if luminance >= 0.90 or luminance <= 0.12:
                return {'background_rgb': (r, g, b), 'foreground_rgb': _contrast_fg((r, g, b))}
            for fr, fg, fb, fg_luminance in fg_candidates:
                if luminance >= 0.80 and fg_luminance >= 0.88:
                    return {'background_rgb': (r, g, b), 'foreground_rgb': _contrast_fg((r, g, b))}
                if luminance <= 0.20 and fg_luminance <= 0.18:
                    return {'background_rgb': (r, g, b), 'foreground_rgb': _contrast_fg((r, g, b))}
    return None


# ── Backend lookup ────────────────────────────────────────────────────────────

def _backend_for_identity(backends, identity):
    return next((b for b in backends if b.identity == identity), None)


def _backend_for_message(backends, msg):
    backend = msg.get('backend_obj')
    if backend is not None:
        return backend
    identity = (msg.get('account') or '').strip()
    if identity:
        return _backend_for_identity(backends, identity)
    return None


# ── Demo fixture (debug helper) ───────────────────────────────────────────────

def _demo_thread_fixture(identity='hermod-demo@local'):
    base = datetime.now(timezone.utc).replace(hour=9, minute=10, second=0, microsecond=0)
    senders = [
        ('David Clevestam', identity),
        ('Mina Park', 'mina@example.com'),
        ('Alex Stone', 'alex@example.com'),
    ]
    texts = [
        'Morning team, here is the first pass on the thread UI.',
        'Looks good. Can we keep the latest reply at the bottom like a chat?',
        'Yes, and we should make the day separators more visible.',
        'Agreed. Also, can we keep attachments collected in the header?',
        'I added the attachment chips and the quick jump panel for that.',
        'Nice. Let us make the sender color consistent with the receiving account.',
        'Done. I also softened the background outside the mail body.',
        'One more thing: the thread overview should not overcrowd the top bar.',
        'I moved the summary into the compact info strip.',
        'Perfect. Send the final version and we can call this ready.',
    ]
    subjects = [
        'Hermod thread UI test',
        'Hermod thread UI test',
        'Hermod thread UI test',
        'Re: Hermod thread UI test',
        'Re: Hermod thread UI test',
        'Re: Hermod thread UI test',
        'Re: Hermod thread UI test',
        'Updated: Hermod thread UI test',
        'Updated: Hermod thread UI test',
        'Updated: Hermod thread UI test',
    ]
    thread_id = 'hermod-demo-thread-10'
    members = []
    for index in range(10):
        sender_name, sender_email = senders[index % len(senders)]
        date = base.replace(hour=9 + (index // 3), minute=10 + (index * 7) % 50)
        attachments = []
        has_attachments = False
        if index in {4, 8}:
            attachments = [{
                'name': f'hermod-design-{index + 1}.png',
                'size': 182344 + index * 2048,
                'content_type': 'image/png',
                'disposition': 'attachment',
            }]
            has_attachments = True
        msg = {
            'uid': f'hermod-demo-{index + 1}',
            'subject': subjects[index],
            'sender_name': sender_name,
            'sender_email': sender_email,
            'to_addrs': [{'name': 'Hermod Demo', 'email': identity}],
            'cc_addrs': [],
            'date': date,
            'is_read': True,
            'has_attachments': has_attachments,
            'snippet': texts[index][:120],
            'folder': 'INBOX',
            'backend': 'demo',
            'account': identity,
            'backend_obj': None,
            'thread_id': thread_id,
            'thread_source': 'demo',
            'message_id': f'<hermod-demo-{index + 1}@local>',
            'thread_count': 10,
            'thread_key': (identity, 'demo', thread_id),
            'attachments': attachments,
            'body_text': texts[index],
        }
        members.append(msg)
    return members
