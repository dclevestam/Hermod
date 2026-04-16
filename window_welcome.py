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
_PROVIDER_ASSETS = _ROOT / "assets" / "providers"
_LUCIDE_ASSETS = _ROOT / "assets" / "icons" / "lucide"
_GMAIL_ICON_PATH = _PROVIDER_ASSETS / "provider-gmail.svg"
_PROTON_ICON_PATH = _PROVIDER_ASSETS / "provider-proton.svg"
_OUTLOOK_ICON_PATH = _PROVIDER_ASSETS / "provider-microsoft.svg"
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
    return _build_provider_icon(_APP_ICON_PATH, "welcome-mark")


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
    specs = [
        ("a", Gtk.Align.START, Gtk.Align.START, 118, 154),
        ("b", Gtk.Align.START, Gtk.Align.START, 312, 120),
        ("c", Gtk.Align.END, Gtk.Align.START, 188, 172),
        ("d", Gtk.Align.END, Gtk.Align.START, 420, 104),
        ("e", Gtk.Align.CENTER, Gtk.Align.START, 0, 78),
    ]
    dots = []
    for css_suffix, halign, valign, margin_start, margin_top in specs:
        dot = Gtk.Box()
        dot.add_css_class("welcome-firefly")
        dot.add_css_class(f"firefly-{css_suffix}")
        dot.set_halign(halign)
        dot.set_valign(valign)
        dot.set_margin_start(margin_start)
        dot.set_margin_top(margin_top)
        overlay.add_overlay(dot)
        overlay.set_measure_overlay(dot, False)
        dots.append(dot)
    return dots


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


def build_more_providers_dialog(parent, on_pick=None):
    try:
        dialog = Gtk.Dialog(transient_for=parent, modal=True)
    except TypeError:
        dialog = Gtk.Dialog(modal=True)
    dialog.set_title("More Providers")
    dialog.set_default_size(760, 560)
    content = dialog.get_content_area()
    content.set_margin_top(16)
    content.set_margin_bottom(16)
    content.set_margin_start(16)
    content.set_margin_end(16)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    topbar.set_hexpand(True)
    heading = Gtk.Label(label="More providers", halign=Gtk.Align.START, xalign=0)
    heading.add_css_class("onboarding-modal-title")
    topbar.append(heading)
    topbar.append(_build_window_close_button(dialog, "Close more providers"))
    subtitle = Gtk.Label(
        label="Choose a provider. Hermod keeps the flow minimal and will guide you into the shortest setup path.",
        halign=Gtk.Align.START,
        xalign=0,
    )
    subtitle.add_css_class("onboarding-modal-subtitle")
    subtitle.set_wrap(True)
    outer.append(topbar)
    outer.append(subtitle)

    grid = Gtk.Grid(column_spacing=14, row_spacing=14, halign=Gtk.Align.CENTER)

    def choose(provider_key):
        if callable(on_pick):
            on_pick(provider_key)
        dialog.close()

    providers = [
        ("iCloud", "icloud", _ICLOUD_ICON_PATH),
        ("Yahoo", "yahoo", _YAHOO_ICON_PATH),
        ("Fastmail", "fastmail", _FASTMAIL_ICON_PATH),
        ("Zoho", "zoho", _ZOHO_ICON_PATH),
        ("Exchange", "exchange", _EXCHANGE_ICON_PATH),
        ("IMAP", "imap-smtp", _IMAP_ICON_PATH),
    ]
    for idx, (label, provider_key, icon_path) in enumerate(providers):
        callback = lambda _provider=provider_key: choose(_provider)
        tile = _build_provider_tile(
            label,
            callback,
            icon_path=icon_path,
            badge_class=f"provider-badge-{provider_key}",
        )
        grid.attach(tile, idx % 3, idx // 3, 1, 1)
    outer.append(grid)
    content.append(outer)
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

        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        self.append(overlay)

        overlay.set_child(_load_picture(_WELCOME_SCENE_PATH, "welcome-scene"))
        overlay.add_overlay(Gtk.Box())

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
            margin_top=28,
            margin_bottom=28,
            margin_start=32,
            margin_end=32,
        )
        overlay.add_overlay(shell)
        overlay.set_measure_overlay(shell, False)

        clamp = Adw.Clamp(maximum_size=980, tightening_threshold=820)
        shell.append(clamp)

        stage = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True, vexpand=True
        )
        stage.add_css_class("welcome-stage")
        clamp.set_child(stage)

        hero = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=18, hexpand=True, vexpand=True
        )
        hero.add_css_class("welcome-hero")
        stage.append(hero)

        hero.append(Gtk.Box(vexpand=True))

        mark = _build_mark()
        mark.set_halign(Gtk.Align.CENTER)
        hero.append(mark)

        tagline = Gtk.Label(
            label="THE INTELLIGENT EMAIL CLIENT", halign=Gtk.Align.CENTER
        )
        tagline.set_xalign(0.5)
        tagline.add_css_class("welcome-tagline")
        hero.append(tagline)

        summary = Gtk.Label(
            label="Built for focus. Powered by intelligence. Rooted in something timeless.",
            halign=Gtk.Align.CENTER,
        )
        summary.set_xalign(0.5)
        summary.set_wrap(True)
        summary.set_max_width_chars(44)
        summary.add_css_class("welcome-summary")
        hero.append(summary)

        divider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        divider_box.add_css_class("welcome-divider-box")
        divider_box.set_halign(Gtk.Align.CENTER)
        line_left = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        line_left.set_hexpand(True)
        line_left.add_css_class("welcome-divider-line")
        divider_mark = Gtk.Label(label="✕")
        divider_mark.add_css_class("welcome-divider-mark")
        line_right = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        line_right.set_hexpand(True)
        line_right.add_css_class("welcome-divider-line")
        divider_box.append(line_left)
        divider_box.append(divider_mark)
        divider_box.append(line_right)
        hero.append(divider_box)

        provider_grid = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=14, halign=Gtk.Align.CENTER
        )
        provider_grid.add_css_class("onboarding-provider-grid")
        for (
            label,
            provider_key,
            icon_path,
            css_class,
            _hover_color,
        ) in ACTIVE_ONBOARDING_PROVIDERS:
            tile = _build_provider_tile(
                label,
                lambda key=provider_key: self._select_provider(key),
                icon_path=icon_path,
            )
            tile.add_css_class(css_class)
            provider_grid.append(tile)
        hero.append(provider_grid)

        self._open_button = Gtk.Button(label="Continue to Hermod")
        self._open_button.add_css_class("suggested-action")
        self._open_button.add_css_class("onboarding-open-btn")
        self._open_button.set_halign(Gtk.Align.CENTER)
        self._open_button.set_visible(False)
        open_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_box.set_halign(Gtk.Align.CENTER)
        open_box.append(
            _build_symbolic_icon(
                ("go-next-symbolic", "pan-end-symbolic", "mail-send-receive-symbolic"),
                "onboarding-open-icon",
            )
        )
        open_label = Gtk.Label(label="Continue to Hermod")
        open_label.add_css_class("onboarding-open-label")
        open_box.append(open_label)
        self._open_button.set_child(open_box)
        if callable(self._on_open_hermod):
            self._open_button.connect("clicked", lambda *_args: self._on_open_hermod())
        hero.append(self._open_button)

        accounts_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        accounts_section.add_css_class("onboarding-accounts")
        accounts_section.set_halign(Gtk.Align.START)
        accounts_section.set_valign(Gtk.Align.START)
        accounts_section.set_margin_top(18)
        accounts_section.set_margin_start(24)
        accounts_heading = Gtk.Label(
            label="Accounts added", halign=Gtk.Align.START, xalign=0
        )
        accounts_heading.add_css_class("onboarding-section-title")
        accounts_section.append(accounts_heading)
        self._accounts_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._accounts_list.add_css_class("onboarding-accounts-list")
        accounts_section.append(self._accounts_list)
        overlay.add_overlay(accounts_section)
        overlay.set_measure_overlay(accounts_section, False)
        self._accounts_section = accounts_section

        move_strip = Gtk.Box()
        move_strip.set_halign(Gtk.Align.FILL)
        move_strip.set_valign(Gtk.Align.START)
        move_strip.set_hexpand(True)
        move_strip.set_size_request(-1, 44)
        move_strip.add_css_class("welcome-move-strip")
        _attach_window_move_controller(move_strip, self)
        overlay.add_overlay(move_strip)
        overlay.set_measure_overlay(move_strip, False)

        hero.append(Gtk.Box(vexpand=True))

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
        close_bar.append(_build_window_close_button(self))
        overlay.add_overlay(close_bar)
        overlay.set_measure_overlay(close_bar, False)

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
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("onboarding-account-row")
            color = _backend_color(backend) or "#74a48d"
            apply_accent_css_class(row, color, index)
            bullet_icon = _build_provider_icon(
                _backend_logo_path(backend), "onboarding-account-bullet"
            )
            bullet_icon.set_tooltip_text(_backend_display_name(backend))
            accent = Gtk.Box()
            accent.add_css_class("onboarding-account-accent")
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            alias_value = _backend_display_name(backend)
            alias_label = Gtk.Label(label=alias_value, halign=Gtk.Align.START, xalign=0)
            alias_label.add_css_class("onboarding-account-title")
            email_value = str(getattr(backend, "identity", "") or "").strip()
            email_label = Gtk.Label(label=email_value, halign=Gtk.Align.START, xalign=0)
            email_label.add_css_class("onboarding-account-subtitle")
            labels.append(alias_label)
            labels.append(email_label)
            row.append(bullet_icon)
            row.append(accent)
            row.append(labels)
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
