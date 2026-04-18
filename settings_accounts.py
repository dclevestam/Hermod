"""Account settings UI and native account management."""

import hashlib
import imaplib
import os
import smtplib
import ssl
import threading
import uuid
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

_ROOT = Path(__file__).resolve().parent

try:
    from .styles import ACCOUNT_PALETTE, apply_accent_css_class
    from .accounts.account_prefs import (
        get_account_preference_record,
        remove_account_preference,
    )
    from .accounts.native_store import (
        NativeAccountRecord,
        get_native_account_record,
        remove_native_account,
        store_native_oauth_token_bundle,
        store_native_password,
        upsert_native_account_with_prefs,
    )
    from .accounts.auth.google_native import (
        GOOGLE_GMAIL_NATIVE_SCOPES,
        run_google_native_oauth_authorization,
    )
    from .accounts.auth.oauth_common import OAuthTokenAcquisitionError
except ImportError:
    from styles import ACCOUNT_PALETTE, apply_accent_css_class
    from accounts.account_prefs import (
        get_account_preference_record,
        remove_account_preference,
    )
    from accounts.native_store import (
        NativeAccountRecord,
        get_native_account_record,
        remove_native_account,
        store_native_oauth_token_bundle,
        store_native_password,
        upsert_native_account_with_prefs,
    )
    from accounts.auth.google_native import (
        GOOGLE_GMAIL_NATIVE_SCOPES,
        run_google_native_oauth_authorization,
    )
    from accounts.auth.oauth_common import OAuthTokenAcquisitionError


def _normalize_hex_color(value, fallback="#4c7fff"):
    value = str(value or "").strip()
    if not value:
        return fallback
    if not value.startswith("#"):
        value = f"#{value}"
    if len(value) != 7:
        return fallback
    try:
        int(value[1:], 16)
    except Exception:
        return fallback
    return value.lower()


def _rgba_from_hex(value, fallback="#4c7fff"):
    color = _normalize_hex_color(value, fallback=fallback)
    rgba = Gdk.RGBA()
    if not rgba.parse(color):
        rgba.parse(fallback)
    return rgba


def _hex_from_rgba(rgba, fallback="#4c7fff"):
    try:
        red = int(round(max(0.0, min(1.0, float(rgba.red))) * 255))
        green = int(round(max(0.0, min(1.0, float(rgba.green))) * 255))
        blue = int(round(max(0.0, min(1.0, float(rgba.blue))) * 255))
        return "#{0:02x}{1:02x}{2:02x}".format(red, green, blue)
    except Exception:
        return _normalize_hex_color(fallback, fallback="#4c7fff")


def _pick_icon_name(*icon_names):
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
    for icon_name in icon_names:
        if theme is None or theme.has_icon(icon_name):
            return icon_name
    return icon_names[-1] if icon_names else "image-missing-symbolic"


def _icon_for_account(backend):
    provider = str(getattr(backend, "provider", "") or "").strip().lower()
    descriptor = getattr(backend, "account_descriptor", None)
    kind = (
        str(getattr(descriptor, "provider_kind", provider) or provider).strip().lower()
    )
    if kind == "imap-smtp":
        return _pick_icon_name(
            "mail-send-receive-symbolic",
            "internet-mail-symbolic",
            "mail-message-new-symbolic",
        )
    if kind == "gmail":
        return _pick_icon_name(
            "mail-google-symbolic", "google-gmail-symbolic", "internet-mail-symbolic"
        )
    return _pick_icon_name(
        "internet-mail-symbolic", "mail-message-new-symbolic", "mail-send-symbolic"
    )


def _backend_display_name(backend):
    return (
        getattr(backend, "presentation_name", "")
        or getattr(backend, "identity", "")
        or ""
    )


def _backend_subtitle(backend):
    descriptor = getattr(backend, "account_descriptor", None)
    source = str(getattr(descriptor, "source", "") or "").strip().lower()
    provider = str(getattr(descriptor, "provider_kind", "") or "").strip().lower()
    identity = getattr(backend, "identity", "") or ""
    parts = []
    if source == "native":
        parts.append("Local")
    else:
        parts.append(source or "Account")
    if provider:
        parts.append(provider.replace("-", " ").title())
    if identity:
        parts.append(identity)
    return " · ".join(parts)


def _backend_color(backend):
    descriptor = getattr(backend, "account_descriptor", None)
    metadata = getattr(descriptor, "metadata", None) or {}
    return _normalize_hex_color(metadata.get("accent_color") or "", fallback="")


def _displayed_backend_color(backend, index=0):
    color = _backend_color(backend)
    if color:
        return color
    return ACCOUNT_PALETTE[index % len(ACCOUNT_PALETTE)]


def _default_alias_from_identity(identity):
    identity = str(identity or "").strip()
    if not identity:
        return ""
    if "@" in identity:
        return identity.split("@", 1)[0].strip() or identity
    return identity


def _default_google_oauth_client_id():
    return str(os.environ.get("HERMOD_GOOGLE_CLIENT_ID") or "").strip()


def _default_google_oauth_client_secret():
    return str(os.environ.get("HERMOD_GOOGLE_CLIENT_SECRET") or "").strip()


def _provider_profile(provider_key):
    provider_key = str(provider_key or "imap-smtp").strip().lower() or "imap-smtp"
    profiles = {
        "gmail": {
            "title": "Connect Gmail",
            "subtitle": "Hermod opens Google sign-in in your browser and stores the account securely.",
            "provider_kind": "gmail",
        },
        "proton": {
            "title": "Connect Proton Mail",
            "subtitle": "Use Proton Bridge or your IMAP details to connect Proton Mail.",
            "provider_kind": "imap-smtp",
            "service_provider": "proton",
            "imap_host": "127.0.0.1",
            "imap_port": "1143",
            "imap_use_ssl": False,
            "imap_use_tls": False,
            "smtp_host": "127.0.0.1",
            "smtp_port": "1025",
            "smtp_use_ssl": False,
            "smtp_use_tls": False,
        },
        "microsoft": {
            "title": "Connect Microsoft",
            "subtitle": "Use your Outlook or Microsoft 365 IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "microsoft",
            "imap_host": "outlook.office365.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.office365.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
        "icloud": {
            "title": "Connect iCloud Mail",
            "subtitle": "Use Apple Mail or iCloud IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "icloud",
            "imap_host": "imap.mail.me.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.mail.me.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
        "yahoo": {
            "title": "Connect Yahoo Mail",
            "subtitle": "Use Yahoo IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "yahoo",
            "imap_host": "imap.mail.yahoo.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.mail.yahoo.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
        "fastmail": {
            "title": "Connect Fastmail",
            "subtitle": "Use Fastmail IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "fastmail",
            "imap_host": "imap.fastmail.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.fastmail.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
        "zoho": {
            "title": "Connect Zoho Mail",
            "subtitle": "Use Zoho IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "zoho",
            "imap_host": "imap.zoho.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.zoho.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
        "exchange": {
            "title": "Connect Exchange",
            "subtitle": "Use your Exchange or Office 365 IMAP/SMTP settings.",
            "provider_kind": "imap-smtp",
            "service_provider": "exchange",
            "imap_host": "outlook.office365.com",
            "imap_port": "993",
            "imap_use_ssl": True,
            "imap_use_tls": False,
            "smtp_host": "smtp.office365.com",
            "smtp_port": "587",
            "smtp_use_ssl": False,
            "smtp_use_tls": True,
        },
    }
    return profiles.get(
        provider_key,
        {
            "title": "Add IMAP/SMTP Account",
            "subtitle": "Create a new mail account with IMAP and SMTP.",
            "provider_kind": "imap-smtp",
        },
    )


def _parse_port(value, fallback):
    try:
        port = int(str(value or "").strip())
        return port if 1 <= port <= 65535 else int(fallback)
    except Exception:
        return int(fallback)


def _unique_alias(desired, backends, ignore_identity=""):
    desired = str(desired or "").strip()
    ignore_identity = str(ignore_identity or "").strip().lower()
    existing = {
        (
            getattr(backend, "presentation_name", "")
            or getattr(backend, "identity", "")
            or ""
        )
        .strip()
        .lower()
        for backend in (backends or [])
        if str(getattr(backend, "identity", "") or "").strip().lower()
        != ignore_identity
    }
    base = desired or _default_alias_from_identity(ignore_identity) or "Account"
    candidate = base
    counter = 2
    while candidate.strip().lower() in existing:
        candidate = f"{base} ({counter})"
        counter += 1
    return candidate


def _auto_account_color(backends, ignore_identity=""):
    ignore_identity = str(ignore_identity or "").strip().lower()
    used = set()
    for index, backend in enumerate(backends or []):
        backend_identity = str(getattr(backend, "identity", "") or "").strip().lower()
        if backend_identity == ignore_identity:
            continue
        used.add(_displayed_backend_color(backend, index))
    for color in ACCOUNT_PALETTE:
        if color not in used:
            return color
    seed = ignore_identity or str(len(used))
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    base = "#{0:02x}{1:02x}{2:02x}".format(digest[0], digest[1], digest[2])
    if base not in used:
        return base
    for step in range(1, 256):
        candidate = "#{0:02x}{1:02x}{2:02x}".format(
            (digest[0] + step) % 256,
            (digest[1] + step * 3) % 256,
            (digest[2] + step * 5) % 256,
        )
        if candidate not in used:
            return candidate
    return base


def _make_icon_button(icon_names, tooltip, callback):
    btn = Gtk.Button()
    btn.add_css_class("flat")
    btn.set_focus_on_click(False)
    btn.set_tooltip_text(tooltip)
    btn.connect("clicked", callback)
    btn.set_child(Gtk.Image(icon_name=_pick_icon_name(*icon_names), pixel_size=16))
    return btn


def _tile_button(icon_names, title, subtitle, callback):
    btn = Gtk.Button()
    btn.add_css_class("flat")
    btn.add_css_class("account-tile")
    btn.set_focus_on_click(False)
    btn.connect("clicked", callback)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(14)
    box.set_margin_end(14)
    icon = Gtk.Image(icon_name=_pick_icon_name(*icon_names), pixel_size=26)
    icon.add_css_class("account-tile-icon")
    title_lbl = Gtk.Label(label=title, halign=Gtk.Align.START, xalign=0)
    title_lbl.add_css_class("heading")
    subtitle_lbl = Gtk.Label(label=subtitle, halign=Gtk.Align.START, xalign=0)
    subtitle_lbl.add_css_class("dim-label")
    subtitle_lbl.set_wrap(True)
    subtitle_lbl.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    subtitle_lbl.set_max_width_chars(28)
    box.append(icon)
    box.append(title_lbl)
    box.append(subtitle_lbl)
    btn.set_child(box)
    return btn


class AccountSettingsController:
    def __init__(self, parent, stack, main_page, editor_page, settings, on_back=None):
        self.parent = parent
        self.stack = stack
        self.main_page = main_page
        self.editor_page = editor_page
        self.settings = settings
        self.on_back = on_back
        self.accounts_group = None
        self.editor_done_callback = None
        self.editor_state = {
            "mode": "new",
            "source": "native",
            "provider_kind": "imap-smtp",
            "backend": None,
            "native_account_id": "",
        }

    def _toast(self, message):
        if self.parent is not None and hasattr(self.parent, "_show_toast"):
            self.parent._show_toast(message)

    def _show_main(self):
        self.stack.set_visible_child_name("main")

    def _finish_editor(self):
        if callable(self.editor_done_callback):
            self.editor_done_callback()
        else:
            self._show_main()

    def _go_back(self):
        if callable(self.on_back):
            self.on_back()
        else:
            self._show_main()

    def show_main(self):
        self._show_main()

    def open_account_editor(self, provider_kind="imap-smtp"):
        provider_kind = str(provider_kind or "imap-smtp").strip().lower() or "imap-smtp"
        self._open_account_editor(None, provider_kind)

    def _refresh_runtime(self):
        if self.parent is None:
            return
        if hasattr(self.parent, "reload_backends"):
            self.parent.reload_backends()
        elif hasattr(self.parent, "refresh_account_chrome"):
            self.parent.refresh_account_chrome()

    def _clear_container(self, widget):
        child = widget.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            widget.remove(child)
            child = next_child

    def build_sections(self):
        add_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        add_heading = Gtk.Label(label="Add Account", halign=Gtk.Align.START)
        add_heading.add_css_class("settings-section-title")
        add_heading.add_css_class("heading")
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        add_box.append(self._render_add_tiles())
        add_hint = Gtk.Label(
            label="Fast accounts render first. Slower IMAP mail can load in the background.",
            halign=Gtk.Align.START,
            xalign=0,
        )
        add_hint.add_css_class("dim-label")
        add_hint.set_wrap(True)
        add_box.append(add_hint)
        add_section.append(add_heading)
        add_section.append(add_box)
        self.main_page.append(add_section)

        configured_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        configured_heading = Gtk.Label(
            label="Configured Accounts", halign=Gtk.Align.START
        )
        configured_heading.add_css_class("settings-section-title")
        configured_heading.add_css_class("heading")
        self.accounts_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        configured_section.append(configured_heading)
        configured_section.append(self.accounts_group)
        self.main_page.append(configured_section)
        self._render_accounts()

    def _render_add_tiles(self):
        tile_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True
        )
        tile_box.append(
            _tile_button(
                (
                    "mail-google-symbolic",
                    "google-gmail-symbolic",
                    "internet-mail-symbolic",
                ),
                "Gmail",
                "Connect Gmail directly with Google sign-in in your browser.",
                lambda *_: self._open_account_editor(None, "gmail"),
            )
        )
        tile_box.append(
            _tile_button(
                (
                    "mail-send-receive-symbolic",
                    "internet-mail-symbolic",
                    "mail-message-new-symbolic",
                ),
                "IMAP / SMTP",
                "Add a mail account directly using server details.",
                lambda *_: self._open_account_editor(None, "imap-smtp"),
            )
        )
        return tile_box

    def _render_accounts(self):
        self._clear_container(self.accounts_group)
        backends = list(getattr(self.parent, "backends", []) or [])
        if not backends:
            empty = Adw.ActionRow(
                title="No accounts yet",
                subtitle="Add Gmail directly or create an IMAP/SMTP account.",
            )
            empty.set_sensitive(False)
            self.accounts_group.append(empty)
            return
        for index, backend in enumerate(backends):
            display_name = _backend_display_name(backend)
            subtitle = _backend_subtitle(backend)
            row = Adw.ActionRow(title=display_name, subtitle=subtitle)
            row.add_css_class("account-row")
            row.set_activatable(True)
            row.backend = backend
            icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image(icon_name=_icon_for_account(backend), pixel_size=18)
            icon_box.append(icon)
            preview_color = _backend_color(backend)
            if preview_color:
                preview = Gtk.Box()
                preview.add_css_class("account-color-preview")
                preview.set_size_request(18, 18)
                apply_accent_css_class(preview, preview_color, index)
                icon_box.append(preview)
            row.add_prefix(icon_box)
            row.add_suffix(
                _make_icon_button(
                    (
                        "preferences-system-symbolic",
                        "emblem-system-symbolic",
                        "document-edit-symbolic",
                    ),
                    "Edit account settings",
                    lambda _btn, current=backend: self._open_account_editor(current),
                )
            )
            row.add_suffix(
                _make_icon_button(
                    ("user-trash-symbolic",),
                    "Remove account",
                    lambda _btn, current=backend: self._remove_account(current),
                )
            )
            row.connect(
                "activated",
                lambda *_args, current=backend: self._open_account_editor(current),
            )
            self.accounts_group.append(row)

    def _identity_in_use(
        self, identity, source="", provider_kind="", current_native_id=""
    ):
        identity = str(identity or "").strip().lower()
        if not identity:
            return False
        current_native_id = str(current_native_id or "").strip()
        for backend in getattr(self.parent, "backends", []) or []:
            backend_identity = (
                str(getattr(backend, "identity", "") or "").strip().lower()
            )
            if backend_identity != identity:
                continue
            descriptor = getattr(backend, "account_descriptor", None)
            backend_source = (
                str(getattr(descriptor, "source", "") or "").strip().lower()
            )
            backend_provider = (
                str(getattr(descriptor, "provider_kind", "") or "").strip().lower()
            )
            native_id = str(
                (getattr(descriptor, "metadata", {}) or {}).get("native_account_id")
                or ""
            ).strip()
            if (
                source == "native"
                and backend_source == "native"
                and current_native_id
                and native_id == current_native_id
            ):
                continue
            if (
                source
                and backend_source
                and source == backend_source
                and provider_kind
                and provider_kind == backend_provider
                and current_native_id
                and native_id == current_native_id
            ):
                continue
            return True
        return False

    def _alias_in_use(self, alias, identity=""):
        alias = str(alias or "").strip().lower()
        if not alias:
            return False
        identity = str(identity or "").strip().lower()
        for backend in getattr(self.parent, "backends", []) or []:
            backend_identity = (
                str(getattr(backend, "identity", "") or "").strip().lower()
            )
            if backend_identity == identity:
                continue
            display_name = (
                str(getattr(backend, "presentation_name", "") or backend_identity)
                .strip()
                .lower()
            )
            if display_name == alias:
                return True
        return False

    def _remove_account(self, backend):
        descriptor = getattr(backend, "account_descriptor", None)
        source = str(getattr(descriptor, "source", "") or "").strip().lower()
        provider_kind = (
            str(getattr(descriptor, "provider_kind", "") or "").strip().lower()
        )
        identity = str(getattr(backend, "identity", "") or "").strip()
        if not identity:
            return
        if source == "native":
            native_id = str(
                (getattr(descriptor, "metadata", {}) or {}).get("native_account_id")
                or ""
            ).strip()
            if native_id:
                remove_native_account(native_id)
            else:
                remove_account_preference(source, provider_kind, identity)
        else:
            remove_account_preference(source, provider_kind, identity)
        self._refresh_runtime()
        self._render_accounts()
        self._finish_editor()
        self._toast(f"Removed {_backend_display_name(backend)}")

    def _update_google_status(self, form, message):
        label = form.get("google_status_label")
        if label is not None:
            text = str(message or "").strip()
            label.set_text(text)
            label.set_visible(bool(text))

    def _queue_google_status_update(self, form, message):
        def apply():
            self._update_google_status(form, message)
            return False

        GLib.idle_add(apply)

    def _google_progress_callback(self, form):
        return lambda message: self._queue_google_status_update(form, message)

    def _save_native_google_record(
        self,
        account_id,
        identity_value,
        alias,
        color,
        enabled,
        client_id,
        client_secret="",
    ):
        record_config = {
            "service_provider": "gmail",
            "oauth_provider": "google",
            "oauth_client_id": client_id,
            "oauth_scopes": list(GOOGLE_GMAIL_NATIVE_SCOPES),
            "api_only": True,
            "send_via_api": True,
        }
        client_secret = str(client_secret or "").strip()
        if client_secret:
            record_config["oauth_client_secret"] = client_secret
        new_record = NativeAccountRecord(
            id=account_id,
            provider_kind="gmail",
            identity=identity_value,
            presentation_name=alias or identity_value,
            alias=alias,
            accent_color=color,
            config=record_config,
            enabled=enabled,
        )
        upsert_native_account_with_prefs(new_record)
        return new_record

    def _finish_native_google_auth_success(
        self,
        *,
        account_id,
        bundle,
        identity_value,
        alias,
        color,
        enabled,
        client_id,
        client_secret="",
        form,
    ):
        try:
            store_native_oauth_token_bundle(account_id, bundle)
            self._save_native_google_record(
                account_id,
                identity_value,
                alias,
                color,
                enabled,
                client_id,
                client_secret,
            )
            self._refresh_runtime()
            self._render_accounts()
            self._finish_editor()
            self._toast(f"Added Gmail account for {identity_value}")
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc)) or "Unknown error"
            message = f"Google sign-in completed, but Hermod could not save the account: {detail}"
            self._update_google_status(form, message)
            form["save_btn"].set_sensitive(True)
            form["cancel_btn"].set_sensitive(True)
            self._toast(message)
        return False

    def _finish_native_google_auth_error(self, *, form, message):
        self._update_google_status(form, message)
        form["save_btn"].set_sensitive(True)
        form["cancel_btn"].set_sensitive(True)
        self._toast(message)
        return False

    def _save_gmail_account(self, context, form):
        alias = form["alias_entry"].get_text().strip()
        enabled = bool(context.get("enabled", True))
        account_id = context["native_account_id"] or uuid.uuid4().hex
        backend_list = list(getattr(self.parent, "backends", []) or [])
        color_picker = form.get("color_picker")
        color = (
            _hex_from_rgba(color_picker.get_rgba()) if color_picker is not None else ""
        )
        if not color:
            color = _auto_account_color(
                backend_list,
                ignore_identity=context["identity"]
                or (
                    context["record"].identity if context["record"] is not None else ""
                ),
            )

        if context["backend"] is not None and context["record"] is not None:
            identity_value = context["record"].identity
            if not alias:
                alias = _unique_alias(
                    _default_alias_from_identity(identity_value),
                    backend_list,
                    ignore_identity=identity_value,
                )
            elif self._alias_in_use(alias, identity_value):
                self._toast(f'Alias "{alias}" is already in use')
                return
            client_id = str(
                context["record"].config.get("oauth_client_id") or ""
            ).strip()
            client_secret = str(
                context["record"].config.get("oauth_client_secret") or ""
            ).strip()
            self._save_native_google_record(
                account_id,
                identity_value,
                alias,
                color,
                enabled,
                client_id,
                client_secret,
            )
            self._refresh_runtime()
            self._render_accounts()
            self._finish_editor()
            return

        client_id = str(
            (
                context["record"].config.get("oauth_client_id")
                if context["record"] is not None
                else ""
            )
            or self.settings.get("google_oauth_client_id")
            or _default_google_oauth_client_id()
        ).strip()
        client_secret = str(
            (
                context["record"].config.get("oauth_client_secret")
                if context["record"] is not None
                else ""
            )
            or self.settings.get("google_oauth_client_secret")
            or _default_google_oauth_client_secret()
        ).strip()
        if not client_id:
            self._toast("Google sign-in is not configured yet")
            return
        form["save_btn"].set_sensitive(False)
        form["cancel_btn"].set_sensitive(False)
        self._update_google_status(form, "Opening Google sign-in in your browser.")

        def run_native_google_auth():
            try:
                bundle = run_google_native_oauth_authorization(
                    client_id,
                    client_secret=client_secret,
                    progress_callback=self._google_progress_callback(form),
                )
                identity_value = str(bundle.get("identity") or "").strip()
                if not identity_value:
                    raise OAuthTokenAcquisitionError(
                        "Google sign-in did not return a Gmail address",
                        stage="profile",
                        retryable=False,
                        source="google",
                    )
                final_alias = alias
                if not final_alias:
                    final_alias = _unique_alias(
                        _default_alias_from_identity(identity_value),
                        backend_list,
                        ignore_identity=identity_value,
                    )
                elif self._alias_in_use(final_alias, identity_value):
                    raise OAuthTokenAcquisitionError(
                        f'Alias "{final_alias}" is already in use',
                        stage="account save",
                        retryable=False,
                        source="google",
                    )
                if self._identity_in_use(identity_value, "native", "gmail", account_id):
                    raise OAuthTokenAcquisitionError(
                        f"{identity_value} already exists in Hermod",
                        stage="account save",
                        retryable=False,
                        source="google",
                    )

                def finish_success():
                    return self._finish_native_google_auth_success(
                        account_id=account_id,
                        bundle=bundle,
                        identity_value=identity_value,
                        alias=final_alias,
                        color=color,
                        enabled=enabled,
                        client_id=client_id,
                        client_secret=client_secret,
                        form=form,
                    )

                GLib.idle_add(finish_success)
                return
            except Exception as exc:
                message = getattr(exc, "detail", str(exc)) or "Google sign-in failed"

                def finish_error():
                    return self._finish_native_google_auth_error(
                        form=form,
                        message=message,
                    )

                GLib.idle_add(finish_error)

        threading.Thread(target=run_native_google_auth, daemon=True).start()

    def _save_imap_account(self, context, form):
        alias = form["alias_entry"].get_text().strip()
        enabled = bool(context.get("enabled", True))
        account_id = context["native_account_id"] or uuid.uuid4().hex
        identity_value = (
            (
                form["email_entry"].get_text().strip()
                if form.get("email_entry") is not None
                else ""
            )
            or alias
            or context["identity"]
            or ""
        )
        if not identity_value:
            self._toast("Email address is required for IMAP/SMTP accounts")
            return
        if self._identity_in_use(identity_value, "native", "imap-smtp", account_id):
            self._toast(f"{identity_value} already exists in Hermod")
            return
        backend_list = list(getattr(self.parent, "backends", []) or [])
        if not alias:
            alias = _unique_alias(
                _default_alias_from_identity(identity_value),
                backend_list,
                ignore_identity=identity_value,
            )
        elif self._alias_in_use(alias, identity_value):
            self._toast(f'Alias "{alias}" is already in use')
            return
        if not form["imap_host"].get_text().strip():
            self._toast("IMAP host is required")
            return
        if not form["smtp_host"].get_text().strip():
            self._toast("SMTP host is required")
            return
        imap_port = _parse_port(
            form.get("imap_port").get_text()
            if form.get("imap_port") is not None
            else "",
            993,
        )
        smtp_port = _parse_port(
            form.get("smtp_port").get_text()
            if form.get("smtp_port") is not None
            else "",
            587,
        )
        color_picker = form.get("color_picker")
        color = (
            _hex_from_rgba(color_picker.get_rgba()) if color_picker is not None else ""
        )
        if not color:
            color = _auto_account_color(backend_list, ignore_identity=identity_value)
        record_config = {
            "service_provider": profile.get("service_provider") or provider,
            "imap_host": form["imap_host"].get_text().strip(),
            "imap_port": imap_port,
            "imap_user_name": form["imap_user"].get_text().strip() or identity_value,
            "imap_use_ssl": form["imap_ssl"].get_active(),
            "imap_use_tls": form["imap_tls"].get_active(),
            "imap_accept_ssl_errors": form["imap_accept"].get_active(),
            "smtp_host": form["smtp_host"].get_text().strip(),
            "smtp_port": smtp_port,
            "smtp_user_name": form["smtp_user"].get_text().strip() or identity_value,
            "smtp_use_ssl": form["smtp_ssl"].get_active(),
            "smtp_use_tls": form["smtp_tls"].get_active(),
            "smtp_accept_ssl_errors": form["smtp_accept"].get_active(),
            "smtp_use_auth": form["smtp_auth"].get_active(),
            "smtp_auth_login": False,
            "smtp_auth_plain": True,
            "smtp_auth_xoauth2": False,
        }
        new_record = NativeAccountRecord(
            id=account_id,
            provider_kind="imap-smtp",
            identity=identity_value,
            presentation_name=alias or identity_value,
            alias=alias,
            accent_color=color,
            config=record_config,
            enabled=enabled,
        )
        upsert_native_account_with_prefs(new_record)
        password = (
            form["password_entry"].get_text()
            if form.get("password_entry") is not None
            else ""
        )
        if password:
            store_native_password(account_id, "imap-password", password)
            store_native_password(account_id, "smtp-password", password)
        self._refresh_runtime()
        self._render_accounts()
        self._finish_editor()

    def _test_imap_account(self, form):
        status = form.get("test_status")

        def set_status(message):
            if status is not None:
                status.set_text(message)

        imap_host = form["imap_host"].get_text().strip()
        smtp_host = form["smtp_host"].get_text().strip()
        password = (
            form.get("password_entry").get_text()
            if form.get("password_entry") is not None
            else ""
        ).strip()
        if not imap_host or not smtp_host:
            set_status("Enter both IMAP and SMTP hosts first.")
            return
        if not password:
            set_status("Enter a password before testing.")
            return
        imap_port = _parse_port(
            form.get("imap_port").get_text()
            if form.get("imap_port") is not None
            else "",
            993,
        )
        smtp_port = _parse_port(
            form.get("smtp_port").get_text()
            if form.get("smtp_port") is not None
            else "",
            587,
        )
        imap_use_ssl = bool(form["imap_ssl"].get_active())
        imap_use_tls = bool(form["imap_tls"].get_active())
        imap_accept = bool(form["imap_accept"].get_active())
        smtp_use_ssl = bool(form["smtp_ssl"].get_active())
        smtp_use_tls = bool(form["smtp_tls"].get_active())
        smtp_accept = bool(form["smtp_accept"].get_active())
        smtp_auth = bool(form["smtp_auth"].get_active())
        user = (
            form["imap_user"].get_text().strip()
            or form["email_entry"].get_text().strip()
        )
        smtp_user = form["smtp_user"].get_text().strip() or user

        def run_test():
            try:
                context_factory = ssl.create_default_context
                if imap_accept or smtp_accept:
                    context_factory = ssl._create_unverified_context
                ssl_context = context_factory()
                imap = None
                smtp = None
                if imap_use_ssl:
                    imap = imaplib.IMAP4_SSL(
                        imap_host, imap_port, ssl_context=ssl_context
                    )
                else:
                    imap = imaplib.IMAP4(imap_host, imap_port)
                    if imap_use_tls:
                        imap.starttls(ssl_context=ssl_context)
                imap.login(user, password)
                try:
                    imap.logout()
                except Exception:
                    pass
                if smtp_use_ssl:
                    smtp = smtplib.SMTP_SSL(
                        smtp_host, smtp_port, context=ssl_context, timeout=15
                    )
                else:
                    smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                    if smtp_use_tls:
                        smtp.starttls(context=ssl_context)
                if smtp_auth:
                    smtp.login(smtp_user, password)
                try:
                    smtp.quit()
                except Exception:
                    pass

                def ok():
                    set_status("Connection test passed.")
                    return False

                GLib.idle_add(ok)
            except Exception as exc:
                detail = getattr(exc, "args", [str(exc)])
                message = (
                    str(detail[0] if detail else exc).strip()
                    or "Connection test failed"
                )

                def fail():
                    set_status(message)
                    return False

                GLib.idle_add(fail)

        threading.Thread(target=run_test, daemon=True).start()

    def _open_account_editor(
        self, backend=None, provider_kind="imap-smtp", profile=None, show_header=True
    ):
        descriptor = (
            getattr(backend, "account_descriptor", None)
            if backend is not None
            else None
        )
        source = (
            str(getattr(descriptor, "source", "") or "native").strip().lower()
            if backend is not None
            else "native"
        )
        provider = (
            str(getattr(descriptor, "provider_kind", "") or provider_kind)
            .strip()
            .lower()
            if backend is not None
            else provider_kind
        )
        identity = (
            str(getattr(backend, "identity", "") or "").strip()
            if backend is not None
            else ""
        )
        record = None
        prefs = None
        if backend is not None:
            prefs = get_account_preference_record(source, provider, identity)
            if source == "native":
                native_id = str(
                    (getattr(descriptor, "metadata", {}) or {}).get("native_account_id")
                    or ""
                ).strip()
                record = get_native_account_record(native_id) if native_id else None
        self.editor_state.update(
            {
                "mode": "edit" if backend is not None else "new",
                "source": source,
                "provider_kind": provider or "imap-smtp",
                "backend": backend,
                "native_account_id": str(
                    (getattr(descriptor, "metadata", {}) or {}).get("native_account_id")
                    or ""
                ).strip()
                if backend is not None
                else "",
            }
        )
        profile = dict(profile or {})
        if not profile:
            profile = _provider_profile(provider if backend is None else provider)
        is_native_gmail = provider == "gmail"
        self._clear_container(self.editor_page)

        if show_header:
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header.add_css_class("account-editor-page")
            back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
            back_btn.add_css_class("flat")
            back_btn.connect("clicked", lambda *_: self._go_back())
            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            if backend is not None:
                title_text = "Edit Account"
                subtitle_text = _backend_subtitle(backend)
            else:
                title_text = profile.get("title") or (
                    "Connect Gmail" if is_native_gmail else "Add IMAP/SMTP Account"
                )
                subtitle_text = profile.get("subtitle") or (
                    "Sign in in your browser and let Hermod keep the connection secure."
                    if is_native_gmail
                    else "Create a new mail account with IMAP and SMTP."
                )
            title_lbl = Gtk.Label(label=title_text, halign=Gtk.Align.START, xalign=0)
            title_lbl.add_css_class("account-editor-header")
            subtitle_lbl = Gtk.Label(
                label=subtitle_text, halign=Gtk.Align.START, xalign=0
            )
            subtitle_lbl.add_css_class("dim-label")
            subtitle_lbl.set_wrap(True)
            title_box.append(title_lbl)
            title_box.append(subtitle_lbl)
            header.append(back_btn)
            header.append(title_box)
            self.editor_page.append(header)

        editor_group = Adw.PreferencesGroup()
        self.editor_page.append(editor_group)

        def field_row(title, subtitle=""):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_activatable(False)
            editor_group.add(row)
            return row

        def add_entry(title, subtitle="", value="", placeholder=""):
            row = field_row(title, subtitle)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_text(value or "")
            if placeholder:
                entry.set_placeholder_text(placeholder)
            row.add_suffix(entry)
            return entry

        def add_switch(title, subtitle="", active=False):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(bool(active))
            editor_group.add(row)
            return row

        prefs_alias = (
            (prefs.alias if prefs is not None else "") if prefs is not None else ""
        )
        prefs_color = (
            (prefs.accent_color if prefs is not None else "")
            if prefs is not None
            else ""
        )
        base_name = _backend_display_name(backend) if backend is not None else ""
        backend_list = list(getattr(self.parent, "backends", []) or [])
        backend_index = next(
            (i for i, row in enumerate(backend_list) if row is backend), 0
        )
        if backend is not None:
            alias_value = (
                prefs_alias
                or (record.alias if record is not None else "")
                or _default_alias_from_identity(base_name)
                or base_name
            )
            alias_value = _unique_alias(
                alias_value,
                backend_list,
                ignore_identity=getattr(backend, "identity", ""),
            )
        else:
            alias_value = ""
        color_value = _normalize_hex_color(
            prefs_color
            or (record.accent_color if record is not None else "")
            or (
                _displayed_backend_color(backend, backend_index)
                if backend is not None
                else _auto_account_color(backend_list)
            ),
            fallback="#4c7fff",
        )
        enabled_value = (
            prefs.enabled
            if prefs is not None
            else (record.enabled if record is not None else True)
        )
        alias_entry = add_entry(
            "Alias", "Used in the sidebar and compose picker", alias_value, "Work email"
        )
        color_row = field_row("Accent color", "Used throughout Hermod for this account")
        color_picker = Gtk.ColorButton.new_with_rgba(_rgba_from_hex(color_value))
        color_picker.set_valign(Gtk.Align.CENTER)
        color_picker.set_hexpand(False)
        color_picker.set_size_request(140, -1)
        color_row.add_suffix(color_picker)

        preview_row = field_row(
            "Preview", "This updates when you change the accent color"
        )
        preview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        preview.add_css_class("account-color-preview")
        preview.add_css_class("account-color-chip")
        preview.set_size_request(-1, 24)
        preview_label = Gtk.Label(label="Aa", halign=Gtk.Align.CENTER, xalign=0.5)
        preview.append(preview_label)
        preview_row.add_suffix(preview)

        def sync_preview(*_args):
            color = _hex_from_rgba(color_picker.get_rgba())
            apply_accent_css_class(preview, color, backend_index)

        color_picker.connect("notify::rgba", sync_preview)
        sync_preview()

        form = {
            "alias_entry": alias_entry,
            "color_picker": color_picker,
            "enabled": enabled_value,
        }

        if is_native_gmail:
            info_label = Gtk.Label(
                label="Hermod opens Google sign-in in your browser and stores the account securely.",
                halign=Gtk.Align.START,
                xalign=0,
            )
            info_label.add_css_class("dim-label")
            info_label.set_wrap(True)
            info_label.set_margin_top(6)
            editor_group.add(info_label)
            google_status_label = Gtk.Label(
                label="",
                halign=Gtk.Align.START,
                xalign=0,
            )
            google_status_label.add_css_class("caption")
            google_status_label.add_css_class("dim-label")
            google_status_label.set_wrap(True)
            google_status_label.set_margin_top(4)
            google_status_label.set_visible(False)
            editor_group.add(google_status_label)
            form["google_status_label"] = google_status_label
        else:
            field_row("IMAP / SMTP", "Connection settings for manual accounts")
            form["email_entry"] = add_entry(
                "Email address",
                "Primary identity for the account",
                (record.identity if record is not None else identity) or "",
                "user@example.com",
            )
            form["imap_host"] = add_entry(
                "IMAP host",
                "Server hostname for incoming mail",
                (record.config.get("imap_host") if record is not None else "")
                or profile.get("imap_host", ""),
                profile.get("imap_host", "imap.example.com"),
            )
            form["imap_port"] = add_entry(
                "IMAP port",
                "Usually 993 for SSL or 143 for STARTTLS",
                str(
                    (record.config.get("imap_port") if record is not None else "")
                    or profile.get("imap_port", "")
                ),
                "993",
            )
            form["imap_user"] = add_entry(
                "IMAP username",
                "Usually your email address",
                (record.config.get("imap_user_name") if record is not None else "")
                or "",
                "user@example.com",
            )
            form["imap_ssl"] = add_switch(
                "IMAP SSL",
                "Connect with implicit TLS on port 993",
                bool(
                    record.config.get("imap_use_ssl", True)
                    if record is not None
                    else True
                ),
            )
            form["imap_tls"] = add_switch(
                "IMAP STARTTLS",
                "Upgrade a plain IMAP connection after connect",
                bool(
                    record.config.get("imap_use_tls", False)
                    if record is not None
                    else False
                ),
            )
            form["imap_accept"] = add_switch(
                "Allow invalid IMAP certificates",
                "Only use this for local or misconfigured servers",
                bool(
                    record.config.get("imap_accept_ssl_errors", False)
                    if record is not None
                    else False
                ),
            )
            form["smtp_host"] = add_entry(
                "SMTP host",
                "Server hostname for outgoing mail",
                (record.config.get("smtp_host") if record is not None else "")
                or profile.get("smtp_host", ""),
                profile.get("smtp_host", "smtp.example.com"),
            )
            form["smtp_port"] = add_entry(
                "SMTP port",
                "Usually 465 for SSL or 587 for STARTTLS",
                str(
                    (record.config.get("smtp_port") if record is not None else "")
                    or profile.get("smtp_port", "")
                ),
                "587",
            )
            form["smtp_user"] = add_entry(
                "SMTP username",
                "Often the same as your email address",
                (record.config.get("smtp_user_name") if record is not None else "")
                or "",
                "user@example.com",
            )
            form["smtp_ssl"] = add_switch(
                "SMTP SSL",
                "Connect with implicit TLS on port 465",
                bool(
                    record.config.get("smtp_use_ssl", True)
                    if record is not None
                    else True
                ),
            )
            form["smtp_tls"] = add_switch(
                "SMTP STARTTLS",
                "Upgrade a plain SMTP connection after connect",
                bool(
                    record.config.get("smtp_use_tls", False)
                    if record is not None
                    else False
                ),
            )
            form["smtp_accept"] = add_switch(
                "Allow invalid SMTP certificates",
                "Only use this for local or misconfigured servers",
                bool(
                    record.config.get("smtp_accept_ssl_errors", False)
                    if record is not None
                    else False
                ),
            )
            form["smtp_auth"] = add_switch(
                "SMTP auth",
                "Require authentication before sending",
                bool(
                    record.config.get("smtp_use_auth", True)
                    if record is not None
                    else True
                ),
            )
            test_row = field_row(
                "Connection test", "Verify IMAP and SMTP before adding the account"
            )
            test_status = Gtk.Label(
                label="Ready to test", halign=Gtk.Align.START, xalign=0
            )
            test_status.add_css_class("dim-label")
            test_status.set_wrap(True)
            test_row.add_suffix(test_status)
            form["test_status"] = test_status
            test_btn = Gtk.Button(label="Test Connection")
            test_btn.add_css_class("flat")
            test_row.add_suffix(test_btn)
            password_row = field_row("Password", "Stored securely in your keyring")
            password_entry = Gtk.Entry()
            password_entry.set_visibility(False)
            password_entry.set_invisible_char("•")
            password_entry.set_hexpand(True)
            password_entry.set_placeholder_text(
                "Leave blank to keep the current password"
            )
            password_row.add_suffix(password_entry)
            form["password_entry"] = password_entry

            def _test_connection(*_args):
                self._test_imap_account(form)

            test_btn.connect("clicked", _test_connection)

        button_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END
        )
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        save_btn = Gtk.Button(
            label="Connect"
            if backend is None and is_native_gmail
            else ("Add Account" if backend is None else "Save")
        )
        save_btn.add_css_class("suggested-action")
        button_row.append(cancel_btn)
        button_row.append(save_btn)
        self.editor_page.append(button_row)
        form["cancel_btn"] = cancel_btn
        form["save_btn"] = save_btn

        context = {
            "backend": backend,
            "identity": identity,
            "record": record,
            "native_account_id": self.editor_state.get("native_account_id") or "",
            "source": source,
            "provider": provider,
            "enabled": enabled_value,
        }

        def save_account(_btn=None):
            if is_native_gmail:
                self._save_gmail_account(context, form)
            else:
                self._save_imap_account(context, form)

        cancel_btn.connect("clicked", lambda *_: self._go_back())
        save_btn.connect("clicked", save_account)
        self.stack.set_visible_child_name("editor")


def build_account_setup_dialog(parent, provider_key="imap-smtp", on_saved=None):
    provider_key = str(provider_key or "imap-smtp").strip().lower() or "imap-smtp"
    profile = _provider_profile(provider_key)
    try:
        from .settings import get_settings as _get_settings
    except ImportError:
        from settings import get_settings as _get_settings
    try:
        dialog = Gtk.Dialog(transient_for=parent, modal=True)
    except TypeError:
        dialog = Gtk.Dialog(modal=True)
    dialog.set_title(profile.get("title") or "Add Account")
    dialog.set_default_size(720, 640)
    content = dialog.get_content_area()
    content.set_spacing(18)
    content.add_css_class("onboarding-modal-content")
    content.set_margin_top(16)
    content.set_margin_bottom(16)
    content.set_margin_start(16)
    content.set_margin_end(16)

    topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    topbar.add_css_class("onboarding-modal-header")
    topbar.set_hexpand(True)
    title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    title_box.set_hexpand(True)
    title_lbl = Gtk.Label(
        label=profile.get("title") or "Add Account",
        halign=Gtk.Align.START,
        xalign=0,
    )
    title_lbl.add_css_class("onboarding-modal-title")
    subtitle_lbl = Gtk.Label(
        label=profile.get("subtitle") or "",
        halign=Gtk.Align.START,
        xalign=0,
    )
    subtitle_lbl.add_css_class("onboarding-modal-subtitle")
    subtitle_lbl.set_wrap(True)
    title_box.append(title_lbl)
    if subtitle_lbl.get_label().strip():
        title_box.append(subtitle_lbl)
    topbar.append(title_box)
    content.append(topbar)

    frame = Gtk.Frame(hexpand=True, vexpand=True)
    frame.add_css_class("onboarding-modal-frame")
    scroller = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        hexpand=True,
        vexpand=True,
    )
    scroller.add_css_class("onboarding-modal-scroller")
    frame.set_child(scroller)
    content.append(frame)

    stack = Gtk.Stack(
        transition_type=Gtk.StackTransitionType.CROSSFADE,
        hexpand=True,
        vexpand=True,
    )
    scroller.set_child(stack)
    main_page = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=0,
        margin_top=6,
        margin_bottom=6,
        margin_start=6,
        margin_end=6,
    )
    editor_page = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        hexpand=True,
        vexpand=True,
        margin_top=6,
        margin_bottom=6,
        margin_start=6,
        margin_end=6,
    )
    stack.add_named(main_page, "main")
    stack.add_named(editor_page, "editor")
    controller = AccountSettingsController(
        parent, stack, main_page, editor_page, _get_settings(), on_back=dialog.close
    )

    def finish_and_close():
        if callable(on_saved):
            on_saved()
        dialog.close()

    controller.editor_done_callback = finish_and_close
    controller._open_account_editor(
        None,
        profile.get("provider_kind", provider_key),
        profile=profile,
        show_header=False,
    )
    dialog.present()
    return dialog
