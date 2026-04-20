"""Full-window welcome and onboarding surfaces for first-start account setup."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk


_ROOT = Path(__file__).resolve().parent
_APP_ICON_PATH = (
    _ROOT / "icons" / "hicolor" / "scalable" / "apps" / "io.github.hermod.Hermod.svg"
)
_WELCOME_SCENE_PATH = _ROOT / "assets" / "welcome-scene.svg"
_WELCOME_PHOTO_PATH = _ROOT / "assets" / "welcome-photo.png"
_PROVIDER_ASSETS = _ROOT / "assets" / "providers"
_LUCIDE_ASSETS = _ROOT / "assets" / "icons" / "lucide"
_GMAIL_ICON_PATH = _PROVIDER_ASSETS / "provider-gmail.png"
_PROTON_ICON_PATH = _PROVIDER_ASSETS / "provider-proton.svg"
_OUTLOOK_ICON_PATH = _PROVIDER_ASSETS / "provider-microsoft.png"
_ICLOUD_ICON_PATH = _PROVIDER_ASSETS / "provider-icloud.svg"
_YAHOO_ICON_PATH = _PROVIDER_ASSETS / "provider-yahoo.svg"
_FASTMAIL_ICON_PATH = _PROVIDER_ASSETS / "provider-fastmail.svg"
_ZOHO_ICON_PATH = _PROVIDER_ASSETS / "provider-zoho.svg"
_EXCHANGE_ICON_PATH = _PROVIDER_ASSETS / "provider-exchange.svg"
_IMAP_ICON_PATH = _PROVIDER_ASSETS / "provider-imap.svg"
_LUCIDE_CLOSE_PATH = _LUCIDE_ASSETS / "x.svg"
_LUCIDE_LOGIN_PATH = _LUCIDE_ASSETS / "log-in.svg"

ACTIVE_ONBOARDING_PROVIDERS = [
    ("Gmail", "gmail", _GMAIL_ICON_PATH, "provider-tile-gmail", "#ea4335"),
    ("Proton", "proton", _PROTON_ICON_PATH, "provider-tile-proton", "#7c4dff"),
    (
        "Microsoft",
        "microsoft",
        _OUTLOOK_ICON_PATH,
        "provider-tile-microsoft",
        "#0078d4",
    ),
    ("IMAP", "imap-smtp", _IMAP_ICON_PATH, "provider-tile-imap-smtp", "#ff6a3d"),
]

ONBOARDING_TILE_META = {
    "gmail": ("Gmail", "Google · OAuth", "G", "#ea4335"),
    "microsoft": ("Outlook", "Microsoft · OAuth", "O", "#0078d4"),
    "proton": ("Proton Mail", "Bridge · IMAP", "P", "#7c4dff"),
    "icloud": ("iCloud Mail", "Apple · App password", "A", "#7b8794"),
    "fastmail": ("Fastmail", "IMAP · App password", "F", "#4a90e2"),
    "yahoo": ("Yahoo", "IMAP · App password", "Y", "#6001d2"),
    "zoho": ("Zoho Mail", "IMAP · OAuth", "Z", "#e42527"),
    "exchange": ("Exchange", "Microsoft · OAuth", "E", "#0078d4"),
    "imap-smtp": ("Other (IMAP/SMTP)", "Manual configuration", "@", "#a6adb3"),
}

ALL_PROVIDERS_ORDER = [
    "gmail",
    "microsoft",
    "icloud",
    "fastmail",
    "proton",
    "yahoo",
    "zoho",
    "imap-smtp",
]

try:
    from .settings_accounts import _backend_color, _backend_display_name
    from .styles import apply_accent_css_class
except ImportError:
    from settings_accounts import _backend_color, _backend_display_name
    from styles import apply_accent_css_class


def _pick_icon_name(*icon_names):
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    for icon_name in icon_names:
        if theme is None or theme.has_icon(icon_name):
            return icon_name
    return icon_names[-1] if icon_names else "image-missing-symbolic"


def hermod_app_icon_path():
    return _APP_ICON_PATH


def _load_picture(path, css_class):
    picture = Gtk.Picture.new_for_filename(str(path))
    picture.add_css_class(css_class)
    picture.set_can_shrink(True)
    picture.set_content_fit(Gtk.ContentFit.COVER)
    try:
        picture.set_can_target(False)
    except Exception:
        pass
    return picture


def _build_mark():
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    frame.add_css_class("welcome-mark")
    frame.set_size_request(64, 64)
    frame.set_halign(Gtk.Align.START)
    frame.set_valign(Gtk.Align.START)
    icon = Gtk.Image.new_from_file(str(_APP_ICON_PATH))
    icon.set_pixel_size(40)
    icon.add_css_class("welcome-mark-icon")
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)
    frame.append(icon)
    return frame


def _build_provider_icon(path, css_class):
    icon = Gtk.Picture.new_for_filename(str(path))
    icon.add_css_class(css_class)
    icon.set_can_shrink(True)
    icon.set_content_fit(Gtk.ContentFit.CONTAIN)
    try:
        icon.set_can_target(False)
    except Exception:
        pass
    return icon


def _build_symbolic_icon(icon_names, css_class, pixel_size=18):
    icon = Gtk.Image(icon_name=_pick_icon_name(*icon_names), pixel_size=pixel_size)
    icon.add_css_class(css_class)
    return icon


def _backend_logo_path(backend):
    descriptor = getattr(backend, "account_descriptor", None)
    provider_kind = str(getattr(descriptor, "provider_kind", "") or "").strip().lower()
    source = str(getattr(descriptor, "source", "") or "").strip().lower()
    metadata = dict(getattr(descriptor, "metadata", {}) or {})
    service = (
        str(
            metadata.get("service_provider")
            or metadata.get("provider_key")
            or provider_kind
            or getattr(backend, "provider", "")
            or ""
        )
        .strip()
        .lower()
    )
    if service in {"gmail", "google"}:
        return _GMAIL_ICON_PATH
    if service in {"proton", "protonmail"}:
        return _PROTON_ICON_PATH
    if service in {"microsoft", "outlook", "microsoft-graph"}:
        return _OUTLOOK_ICON_PATH
    if service in {"icloud", "icloud-mail"}:
        return _ICLOUD_ICON_PATH
    if service == "yahoo":
        return _YAHOO_ICON_PATH
    if service == "fastmail":
        return _FASTMAIL_ICON_PATH
    if service == "zoho":
        return _ZOHO_ICON_PATH
    if service in {"exchange", "microsoft365", "office365"}:
        return _EXCHANGE_ICON_PATH
    if source == "native" and provider_kind == "imap-smtp":
        return _IMAP_ICON_PATH
    if source == "native" and provider_kind == "gmail":
        return _GMAIL_ICON_PATH
    return _IMAP_ICON_PATH


def _add_fireflies(overlay):
    return []


def _build_icon_launcher(path, tooltip, callback):
    button = Gtk.Button(tooltip_text=tooltip)
    button.add_css_class("welcome-icon-launcher")
    button.connect("clicked", lambda *_args: callback() if callable(callback) else None)
    button.set_child(_build_provider_icon(path, "welcome-icon-launcher-image"))
    return button


def _build_window_close_button(widget, tooltip="Close welcome"):
    button = Gtk.Button(tooltip_text=tooltip)
    button.add_css_class("flat")
    button.add_css_class("welcome-window-close")
    button.set_child(
        _build_symbolic_icon(
            ("window-close-symbolic", "close-symbolic", "process-stop-symbolic"),
            "welcome-close-icon",
        )
    )

    def _close_target():
        for candidate in (widget.get_root(), widget.get_native()):
            if candidate is not None and hasattr(candidate, "close"):
                return candidate
        return None

    def _on_clicked(*_args):
        window = _close_target()
        if window is not None:
            try:
                window.close()
            except Exception:
                pass

    button.connect("clicked", _on_clicked)
    return button


def _build_logo_badge(label, css_class="", fallback_icon=None):
    badge = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    badge.add_css_class("provider-logo-badge")
    badge.set_halign(Gtk.Align.CENTER)
    badge.set_valign(Gtk.Align.CENTER)
    if css_class:
        badge.add_css_class(css_class)
    if fallback_icon:
        icon = Gtk.Image(icon_name=fallback_icon, pixel_size=20)
        badge.append(icon)
    else:
        text = Gtk.Label(label=label[:1].upper())
        text.add_css_class("provider-logo-badge-text")
        badge.append(text)
    return badge


def _build_provider_tile(
    label,
    callback,
    *,
    icon_path=None,
    badge_label=None,
    badge_class="",
    fallback_icon=None,
):
    button = Gtk.Button()
    button.add_css_class("flat")
    button.add_css_class("provider-tile")
    button.set_has_frame(False)
    button.set_focus_on_click(False)
    button.set_tooltip_text(label)
    button.connect("clicked", lambda *_args: callback() if callable(callback) else None)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    content.set_margin_top(10)
    content.set_margin_bottom(10)
    content.set_margin_start(10)
    content.set_margin_end(10)
    content.set_halign(Gtk.Align.CENTER)
    content.set_valign(Gtk.Align.CENTER)
    if icon_path is not None:
        icon = _build_provider_icon(icon_path, "provider-tile-icon")
        content.append(icon)
    else:
        content.append(
            _build_logo_badge(badge_label or label, badge_class, fallback_icon)
        )
    button.set_child(content)
    return button


def _build_provider_row_tile(provider_key, callback):
    meta = ONBOARDING_TILE_META.get(provider_key)
    if meta is None:
        meta = (provider_key.title(), "Manual", provider_key[:1].upper(), "#a6adb3")
    name, sub, letter, color = meta

    button = Gtk.Button()
    button.add_css_class("provider-row-tile")
    button.set_has_frame(False)
    button.set_focus_on_click(False)
    button.set_tooltip_text(name)
    button.connect("clicked", lambda *_args: callback() if callable(callback) else None)
    button.set_hexpand(True)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_margin_top(12)
    row.set_margin_bottom(12)
    row.set_margin_start(14)
    row.set_margin_end(14)
    row.set_valign(Gtk.Align.CENTER)

    glyph = Gtk.Label(label=letter)
    glyph.add_css_class("provider-glyph")
    glyph.add_css_class(f"glyph-{provider_key.replace('-', '_')}")
    glyph.set_valign(Gtk.Align.CENTER)
    glyph.set_halign(Gtk.Align.CENTER)

    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    meta_box.set_hexpand(True)
    meta_box.set_valign(Gtk.Align.CENTER)
    name_label = Gtk.Label(label=name, halign=Gtk.Align.START, xalign=0)
    name_label.add_css_class("provider-name")
    sub_label = Gtk.Label(label=sub, halign=Gtk.Align.START, xalign=0)
    sub_label.add_css_class("provider-sub")
    meta_box.append(name_label)
    meta_box.append(sub_label)

    row.append(glyph)
    row.append(meta_box)
    button.set_child(row)
    return button


def _attach_window_move_controller(widget, window_widget):
    gesture = Gtk.GestureClick()
    gesture.set_button(1)

    def _begin_move(gesture, _n_press, x, y):
        try:
            window = window_widget.get_root() or window_widget.get_native()
            surface = (
                window.get_surface()
                if window is not None and hasattr(window, "get_surface")
                else None
            )
            if surface is None or not hasattr(surface, "begin_move"):
                return
            device = gesture.get_current_event_device()
            timestamp = gesture.get_current_event_time()
            button = gesture.get_current_button() or 1
            surface.begin_move(device, button, x, y, timestamp)
        except Exception:
            pass

    gesture.connect("pressed", _begin_move)
    widget.add_controller(gesture)


def _strip_dialog_chrome(dialog):
    """Replace OS CSD titlebar with an empty stub so our internal header owns
    the chrome. Keeps the window draggable via its root surface."""
    try:
        dialog.set_titlebar(Gtk.Box())
    except Exception:
        pass
    return dialog


def _build_modal_shell(title, subtitle_text, on_close):
    """Shared modal chrome: outer rounded card with eyebrow + title + subtitle
    + close button, matching the design's `.modal` + `.modal-head` pattern."""
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    frame.add_css_class("onboarding-modal-frame")
    frame.set_hexpand(True)
    frame.set_vexpand(True)

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    head.add_css_class("onboarding-modal-head")
    head.set_margin_top(22)
    head.set_margin_bottom(16)
    head.set_margin_start(24)
    head.set_margin_end(20)

    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    eyebrow = Gtk.Label(label="ADD ACCOUNT", halign=Gtk.Align.START, xalign=0)
    eyebrow.add_css_class("welcome-eyebrow")
    titles.append(eyebrow)
    heading = Gtk.Label(label=title, halign=Gtk.Align.START, xalign=0)
    heading.add_css_class("onboarding-modal-title")
    titles.append(heading)
    if subtitle_text:
        sub = Gtk.Label(label=subtitle_text, halign=Gtk.Align.START, xalign=0)
        sub.set_wrap(True)
        sub.set_max_width_chars(58)
        sub.add_css_class("onboarding-modal-subtitle")
        titles.append(sub)
    head.append(titles)

    close_btn = Gtk.Button()
    close_btn.add_css_class("flat")
    close_btn.add_css_class("onboarding-modal-close")
    close_btn.set_valign(Gtk.Align.START)
    close_btn.set_tooltip_text("Close")
    close_btn.set_child(
        _build_symbolic_icon(
            ("window-close-symbolic", "close-symbolic"),
            "welcome-close-icon",
            pixel_size=14,
        )
    )
    if callable(on_close):
        close_btn.connect("clicked", lambda *_: on_close())
    head.append(close_btn)

    frame.append(head)

    divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    divider.add_css_class("onboarding-modal-divider")
    frame.append(divider)

    return frame


def build_more_providers_dialog(parent, on_pick=None):
    try:
        dialog = Gtk.Window(transient_for=parent, modal=True)
    except TypeError:
        dialog = Gtk.Window(modal=True)
    dialog.set_title("More providers")
    dialog.set_default_size(560, 640)
    _strip_dialog_chrome(dialog)
    dialog.add_css_class("onboarding-modal-window")

    frame = _build_modal_shell(
        "Connect a mail provider",
        "Pick a service. OAuth uses your system browser — credentials never pass through Hermod.",
        on_close=dialog.close,
    )

    body = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        hexpand=True,
        vexpand=True,
    )
    body.set_margin_top(8)
    body.set_margin_bottom(8)
    body.set_margin_start(8)
    body.set_margin_end(8)

    scroller = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        hexpand=True,
        vexpand=True,
    )
    scroller.add_css_class("onboarding-modal-scroller")
    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    scroller.set_child(list_box)
    body.append(scroller)
    frame.append(body)

    foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    foot.add_css_class("onboarding-modal-foot")
    foot.set_margin_top(4)
    foot.set_margin_bottom(14)
    foot.set_margin_start(24)
    foot.set_margin_end(24)
    lock_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    lock_pill.add_css_class("lock-pill")
    lock_pill.append(
        _build_symbolic_icon(
            ("changes-prevent-symbolic", "security-high-symbolic"),
            "lock-pill-icon",
            pixel_size=11,
        )
    )
    lock_text = Gtk.Label(
        label="OAuth uses your system browser · no credentials stored by us"
    )
    lock_text.add_css_class("lock-pill-text")
    lock_pill.append(lock_text)
    foot.append(lock_pill)
    frame.append(foot)

    def choose(provider_key):
        if callable(on_pick):
            on_pick(provider_key)
        dialog.close()

    for provider_key in ALL_PROVIDERS_ORDER:
        row = _build_provider_row_tile(
            provider_key,
            lambda key=provider_key: choose(key),
        )
        list_box.append(row)

    dialog.set_child(frame)
    return dialog


class WelcomeScreen(Gtk.Box):
    def __init__(
        self, on_provider_selected=None, on_open_hermod=None, get_backends=None
    ):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True
        )
        self.add_css_class("welcome-screen")
        self._on_provider_selected = on_provider_selected
        self._on_open_hermod = on_open_hermod
        self._get_backends = get_backends

        header = Adw.HeaderBar()
        header.add_css_class("welcome-header-bar")
        header.add_css_class("flat")
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand_mark = Gtk.Image.new_from_file(str(_APP_ICON_PATH))
        brand_mark.set_pixel_size(18)
        brand_mark.add_css_class("welcome-header-mark")
        brand_text = Gtk.Label(label="HERMOD")
        brand_text.add_css_class("welcome-header-brand")
        brand.append(brand_mark)
        brand.append(brand_text)
        brand.set_valign(Gtk.Align.CENTER)
        header.set_title_widget(brand)
        self.append(header)

        body = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True
        )
        self.append(body)

        # Left panel: forest/aurora photo with caption at bottom-left.
        photo = Gtk.Overlay(hexpand=True, vexpand=True)
        photo.add_css_class("welcome-photo")
        photo.set_size_request(360, -1)
        if _WELCOME_PHOTO_PATH.exists():
            photo.set_child(_load_picture(_WELCOME_PHOTO_PATH, "welcome-photo-image"))
        else:
            photo.set_child(Gtk.Box(hexpand=True, vexpand=True))
        caption = Gtk.Label(
            label="— forest / aurora photography —",
            halign=Gtk.Align.START,
            valign=Gtk.Align.END,
            xalign=0,
        )
        caption.add_css_class("welcome-photo-caption")
        caption.set_margin_start(24)
        caption.set_margin_bottom(24)
        photo.add_overlay(caption)
        photo.set_measure_overlay(caption, False)
        _attach_window_move_controller(photo, self)
        body.append(photo)

        # Right panel: content column, left-aligned, scrollable.
        right_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True,
        )
        right_scroll.add_css_class("welcome-right-scroll")
        body.append(right_scroll)

        right = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True,
            vexpand=True,
        )
        right.add_css_class("welcome-right")
        right_scroll.set_child(right)

        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True,
        )
        inner.add_css_class("welcome-inner")
        inner.set_margin_top(40)
        inner.set_margin_bottom(40)
        inner.set_margin_start(72)
        inner.set_margin_end(72)
        inner.set_halign(Gtk.Align.START)
        right.append(inner)

        mark = _build_mark()
        mark.set_halign(Gtk.Align.START)
        inner.append(mark)

        eyebrow = Gtk.Label(label="HERMOD", halign=Gtk.Align.START, xalign=0)
        eyebrow.add_css_class("welcome-eyebrow")
        eyebrow.set_margin_top(24)
        inner.append(eyebrow)

        title = Gtk.Label(
            label="A quiet place\nfor your mail.",
            halign=Gtk.Align.START,
            xalign=0,
        )
        title.set_wrap(True)
        title.add_css_class("welcome-title")
        title.set_margin_top(8)
        inner.append(title)

        summary = Gtk.Label(
            label=(
                "A native Linux email client. Fast, private, built for focus. "
                "All intelligence runs on your device."
            ),
            halign=Gtk.Align.START,
            xalign=0,
        )
        summary.set_wrap(True)
        summary.set_max_width_chars(50)
        summary.add_css_class("welcome-summary")
        summary.set_margin_top(16)
        inner.append(summary)

        providers_eyebrow = Gtk.Label(
            label="CONNECT AN ACCOUNT", halign=Gtk.Align.START, xalign=0
        )
        providers_eyebrow.add_css_class("welcome-providers-eyebrow")
        providers_eyebrow.set_margin_top(40)
        inner.append(providers_eyebrow)

        provider_grid = Gtk.Grid(
            column_spacing=10,
            row_spacing=10,
            halign=Gtk.Align.FILL,
        )
        provider_grid.add_css_class("welcome-provider-grid")
        provider_grid.set_margin_top(14)
        for idx, (label, provider_key, _icon_path, _css_class, _hover) in enumerate(
            ACTIVE_ONBOARDING_PROVIDERS
        ):
            tile = _build_provider_row_tile(
                provider_key,
                lambda key=provider_key: self._select_provider(key),
            )
            provider_grid.attach(tile, idx % 2, idx // 2, 1, 1)
        inner.append(provider_grid)

        more_btn = Gtk.Button(label="Show all 8 providers  →")
        more_btn.add_css_class("welcome-more")
        more_btn.set_has_frame(False)
        more_btn.set_halign(Gtk.Align.START)
        more_btn.set_margin_top(14)
        more_btn.connect(
            "clicked", lambda *_: self._select_provider("more-providers")
        )
        inner.append(more_btn)

        # Accounts section (shown once at least one account exists).
        accounts_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        accounts_section.add_css_class("onboarding-accounts")
        accounts_section.set_halign(Gtk.Align.START)
        accounts_section.set_margin_top(32)
        accounts_heading = Gtk.Label(
            label="Accounts added", halign=Gtk.Align.START, xalign=0
        )
        accounts_heading.add_css_class("onboarding-section-title")
        accounts_section.append(accounts_heading)
        self._accounts_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._accounts_list.add_css_class("onboarding-accounts-list")
        accounts_section.append(self._accounts_list)
        inner.append(accounts_section)
        self._accounts_section = accounts_section

        # Continue button (shown once at least one account exists).
        self._open_button = Gtk.Button(label="Continue to Hermod")
        self._open_button.add_css_class("suggested-action")
        self._open_button.add_css_class("onboarding-open-btn")
        self._open_button.set_halign(Gtk.Align.START)
        self._open_button.set_margin_top(20)
        self._open_button.set_visible(False)
        if callable(self._on_open_hermod):
            self._open_button.connect(
                "clicked", lambda *_args: self._on_open_hermod()
            )
        inner.append(self._open_button)

        # Foot: lock pill.
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        foot.set_margin_top(40)
        foot.set_halign(Gtk.Align.START)
        lock_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lock_pill.add_css_class("lock-pill")
        lock_pill.append(
            _build_symbolic_icon(
                ("changes-prevent-symbolic", "security-high-symbolic"),
                "lock-pill-icon",
                pixel_size=11,
            )
        )
        lock_text = Gtk.Label(label="Zero-cloud · local model")
        lock_text.add_css_class("lock-pill-text")
        lock_pill.append(lock_text)
        foot.append(lock_pill)
        inner.append(foot)

        self.refresh_accounts()

    def _select_provider(self, provider_key):
        if callable(self._on_provider_selected):
            self._on_provider_selected(provider_key)

    def refresh_accounts(self, backends=None):
        backends = list(
            backends
            if backends is not None
            else (self._get_backends() if callable(self._get_backends) else [])
        )
        child = self._accounts_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._accounts_list.remove(child)
            child = next_child
        if not backends:
            self._accounts_section.set_visible(False)
            self._open_button.set_visible(False)
            return
        self._accounts_section.set_visible(True)
        for index, backend in enumerate(backends):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("onboarding-account-row")
            color = _backend_color(backend) or "#74a48d"
            apply_accent_css_class(row, color, index)
            bullet_icon = _build_provider_icon(
                _backend_logo_path(backend), "onboarding-account-bullet"
            )
            bullet_icon.set_tooltip_text(_backend_display_name(backend))
            bullet_icon.set_valign(Gtk.Align.CENTER)
            accent = Gtk.Box()
            accent.add_css_class("onboarding-account-accent")
            accent.set_valign(Gtk.Align.CENTER)
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True
            )
            labels.set_valign(Gtk.Align.CENTER)
            alias_value = _backend_display_name(backend)
            alias_label = Gtk.Label(label=alias_value, halign=Gtk.Align.START, xalign=0)
            alias_label.add_css_class("onboarding-account-title")
            email_value = str(getattr(backend, "identity", "") or "").strip()
            email_label = Gtk.Label(label=email_value, halign=Gtk.Align.START, xalign=0)
            email_label.add_css_class("onboarding-account-subtitle")
            labels.append(alias_label)
            labels.append(email_label)
            health_dot = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
            health_dot.set_size_request(8, 8)
            health_dot.add_css_class("onboarding-account-health")
            health_dot.set_tooltip_text("Connected")
            row.append(bullet_icon)
            row.append(accent)
            row.append(labels)
            row.append(health_dot)
            self._accounts_list.append(row)
        self._open_button.set_visible(True)


class WelcomeSettingsShell(Gtk.Overlay):
    def __init__(self, content, on_back=None):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("welcome-settings-shell")

        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        self.set_child(overlay)
        overlay.set_child(_load_picture(_WELCOME_SCENE_PATH, "welcome-scene"))

        self._fireflies = _add_fireflies(overlay)

        wash = Gtk.Box()
        wash.add_css_class("welcome-wash")
        wash.set_hexpand(True)
        wash.set_vexpand(True)
        overlay.add_overlay(wash)
        overlay.set_measure_overlay(wash, False)

        shell = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True,
            vexpand=True,
            margin_top=30,
            margin_bottom=30,
            margin_start=30,
            margin_end=30,
        )
        overlay.add_overlay(shell)
        overlay.set_measure_overlay(shell, False)

        clamp = Adw.Clamp(maximum_size=1060, tightening_threshold=840)
        shell.append(clamp)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        stage.add_css_class("welcome-settings-stage")
        clamp.set_child(stage)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.add_css_class("welcome-settings-header")
        stage.append(header)

        back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        back_btn.add_css_class("flat")
        back_btn.add_css_class("welcome-settings-back")
        if callable(on_back):
            back_btn.connect("clicked", lambda *_args: on_back())
        header.append(back_btn)

        title_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True
        )
        title = Gtk.Label(label="Set Up Your First Account", halign=Gtk.Align.START)
        title.set_xalign(0.0)
        title.add_css_class("welcome-settings-title")
        subtitle = Gtk.Label(
            label="Choose Gmail or a manual mailbox. Hermod stays on the welcome screen until an account exists.",
            halign=Gtk.Align.START,
        )
        subtitle.set_xalign(0.0)
        subtitle.set_wrap(True)
        subtitle.add_css_class("welcome-settings-subtitle")
        title_box.append(title)
        title_box.append(subtitle)
        header.append(title_box)

        stage.append(content)

        close_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.START,
            margin_top=16,
            margin_start=16,
            margin_end=16,
        )
        close_bar.add_css_class("welcome-window-controls")
        close_spacer = Gtk.Box(hexpand=True)
        close_bar.append(close_spacer)
        close_bar.append(_build_window_close_button(self, tooltip="Close setup"))
        overlay.add_overlay(close_bar)
        overlay.set_measure_overlay(close_bar, False)
