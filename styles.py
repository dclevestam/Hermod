CSS = """
.unread-dot {
    background-color: @accent_color;
    border-radius: 50%;
    min-width: 8px;
    min-height: 8px;
}
.email-row {
    border-bottom: 1px solid alpha(@borders, 0.5);
}
.message-list-view row:hover .email-row {
    background-color: alpha(@window_fg_color, 0.022);
    box-shadow: none;
}
.message-list-view row:selected:hover .email-row,
.message-list-view row:selected .email-row,
.email-row:selected,
.email-row.selected {
    border-bottom: 1px solid alpha(@accent_color, 0.18);
    background-color: alpha(@accent_color, 0.16);
    box-shadow: inset 4px 0 0 0 alpha(@accent_color, 1.0);
}
.thread-indicator {
    background-color: alpha(@window_fg_color, 0.07);
    border-radius: 999px;
    padding: 0px 6px;
    min-height: 18px;
}
.thread-indicator image {
    color: alpha(@window_fg_color, 0.68);
}
.thread-badge {
    color: alpha(@window_fg_color, 0.74);
    font-size: 0.68em;
    font-weight: 700;
    margin-left: 2px;
}
.thread-badge-threaded {
    color: @accent_fg_color;
}
.folder-count {
    background-color: alpha(@window_fg_color, 0.10);
    color: alpha(@window_fg_color, 0.86);
    border-radius: 9px;
    padding: 0px 5px;
    font-size: 0.64em;
    font-weight: 700;
    min-width: 16px;
}
.folder-count-dim {
    background-color: alpha(@window_fg_color, 0.04);
    color: alpha(@window_fg_color, 0.42);
}
.navigation-sidebar row:selected .folder-count {
    background-color: alpha(@accent_fg_color, 0.16);
    color: @accent_fg_color;
}
.navigation-sidebar row:selected .folder-count-dim {
    background-color: alpha(@accent_fg_color, 0.08);
    color: alpha(@accent_fg_color, 0.70);
}
.account-header {
    font-size: 0.86em;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: alpha(@window_fg_color, 0.72);
}
.more-folders-label {
    font-size: 0.85em;
    color: alpha(@window_fg_color, 0.55);
}
.email-actions {
    background: linear-gradient(to right,
        alpha(@window_bg_color, 0),
        alpha(@window_bg_color, 0.92) 20px,
        @window_bg_color 34px);
    padding-left: 14px;
}
.message-list-view row:selected .email-row .email-actions,
.message-list-view row:selected:hover .email-row .email-actions,
.email-row:selected .email-actions,
.email-row.selected .email-actions {
    background: linear-gradient(to right,
        alpha(@accent_bg_color, 0),
        alpha(@accent_bg_color, 0.92) 20px,
        @accent_bg_color 34px);
}
.load-more-row {
    border-top: 1px solid alpha(@borders, 0.18);
    border-bottom: 1px solid alpha(@borders, 0.18);
    background-color: alpha(@window_fg_color, 0.018);
    padding: 6px 0px 8px;
}
.load-more-row.selected {
    background-color: alpha(@accent_color, 0.06);
}
.load-more-row button {
    min-height: 38px;
    min-width: 168px;
    font-weight: 600;
}
.account-accent-strip {
    border-radius: 999px;
    min-width: 4px;
    min-height: 18px;
}
.account-accent-label {
    font-weight: 600;
}
.search-bar-box {
    border-bottom: 1px solid alpha(@borders, 0.32);
    padding: 4px 6px 4px;
}
.search-entry-shell {
    background-color: alpha(@window_fg_color, 0.065);
    border: 1px solid alpha(@borders, 0.12);
    border-radius: 11px;
    min-height: 32px;
}
.search-entry-shell:focus-within {
    border-color: alpha(@borders, 0.12);
    box-shadow: none;
}
.search-entry-tab {
    background: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    min-height: 0;
    padding: 0px 34px 0px 9px;
}
.search-entry-tab:focus,
.search-entry-tab:focus-within {
    border: none;
    box-shadow: none;
    outline: none;
}
.search-entry-icon {
    color: alpha(@window_fg_color, 0.52);
}
.sidebar-actions {
    padding: 3px 10px 5px;
    border-bottom: 1px solid alpha(@borders, 0.22);
}
.sidebar-action-btn {
    padding-top: 0px;
    padding-bottom: 0px;
}
.sidebar-action-btn.action-feedback {
    box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.34);
}
.sync-control.action-feedback {
    background-color: alpha(@accent_color, 0.12);
}
.sidebar-compose-label {
    font-weight: 600;
}
.sync-control {
    padding: 0px;
    border-radius: 10px;
    border: 1px solid alpha(@borders, 0.16);
    background-color: alpha(@window_fg_color, 0.04);
}
.sync-control box,
.sync-control label,
.sync-control image,
.sync-control separator {
    background: transparent;
}
.sync-control.sync-online {
    background-color: rgba(46, 204, 113, 0.10);
    color: rgba(214, 255, 229, 0.96);
}
.sync-control.sync-offline {
    background-color: rgba(229, 57, 53, 0.12);
    color: rgba(255, 213, 210, 0.96);
}
.sync-left-side {
    padding: 0px;
    min-width: 39px;
}
.sync-right-side {
    padding: 0px;
    min-width: 79px;
}
.sync-divider {
    min-width: 1px;
    min-height: 30px;
    background-color: alpha(@borders, 0.72);
    border-radius: 999px;
}
.sync-auto-label {
    font-size: 0.60em;
    font-weight: 700;
    letter-spacing: 0.07em;
    color: alpha(@window_fg_color, 0.60);
}
.sync-control.sync-online .sync-auto-label {
    color: rgba(49, 187, 112, 0.94);
}
.sync-control.sync-offline .sync-auto-label {
    color: rgba(255, 160, 155, 0.94);
}
.sync-auto-value {
    font-size: 0.74em;
    font-weight: 700;
}
.sync-control.sync-online .sync-auto-value {
    color: rgba(49, 187, 112, 0.96);
}
.sync-control.sync-offline .sync-auto-value {
    color: rgba(255, 213, 210, 0.98);
}
.sync-control.sync-online .sync-divider {
    background-color: alpha(@borders, 0.52);
}
.sync-control.sync-offline .sync-divider {
    background-color: alpha(@borders, 0.52);
}
.sync-online-icon {
    color: rgba(49, 187, 112, 0.98);
    padding: 0;
}
.sync-control.sync-offline .sync-online-icon {
    color: rgba(229, 57, 53, 0.98);
}
.sync-offline-label {
    font-weight: 700;
    letter-spacing: 0.02em;
}
.sync-badge {
    background-color: @accent_color;
    color: @accent_fg_color;
    border-radius: 10px;
    padding: 0px 5px;
    font-size: 0.7em;
    font-weight: bold;
    min-width: 16px;
    margin: 2px;
}
.attachment-bar {
    border-top: 1px solid alpha(@borders, 0.24);
    background-color: alpha(@window_fg_color, 0.03);
    padding: 6px 10px 8px;
}
.thread-reply-bar {
    border-top: 1px solid alpha(@borders, 0.24);
    background-color: alpha(@window_bg_color, 0.92);
    padding: 8px 10px 10px;
}
.thread-reply-editor {
    min-height: 62px;
    background-color: alpha(@window_fg_color, 0.03);
    border: 1px solid alpha(@borders, 0.18);
    border-radius: 12px;
    padding: 8px 10px;
}
.thread-reply-send {
    min-width: 84px;
    min-height: 30px;
    font-weight: 700;
}
.message-info-bar {
    border-bottom: 1px solid alpha(@borders, 0.22);
    background-color: alpha(@window_bg_color, 0.62);
    padding: 8px 12px 7px;
    min-height: 58px;
}
.message-info-top {
    min-height: 20px;
}
.message-info-top-row {
    min-height: 20px;
}
.message-info-actions {
    min-width: 118px;
}
.thread-info-button {
    min-height: 26px;
    padding: 0px 10px;
    font-size: 0.82em;
    font-weight: 700;
}
.thread-tab {
    border-radius: 999px 0px 0px 999px;
    padding: 0px 12px 0px 14px;
    min-height: 30px;
}
.thread-info-button.active {
    background-color: alpha(@accent_color, 0.14);
    color: @accent_fg_color;
    box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.24);
}
.thread-info-senders {
    margin-top: 5px;
}
.message-info-sender-line {
    color: alpha(@window_fg_color, 0.80);
}
.message-info-subject {
    font-size: 0.92em;
    font-weight: 700;
    color: alpha(@window_fg_color, 0.94);
    letter-spacing: 0.01em;
    min-height: 18px;
    line-height: 1.2;
}
.message-info-sender {
    font-size: 0.80em;
    font-weight: 400;
    color: alpha(@window_fg_color, 0.80);
    line-height: 1.10;
}
.message-info-date {
    font-size: 0.80em;
    font-weight: 400;
    color: alpha(@window_fg_color, 0.80);
    line-height: 1.10;
}
.message-info-title {
    font-size: 0.88em;
    font-weight: 700;
    color: alpha(@window_fg_color, 0.94);
    letter-spacing: 0.01em;
}
.message-info-meta {
    font-size: 0.78em;
    color: alpha(@window_fg_color, 0.68);
}
.reading-pane-shell {
    background-color: alpha(@window_bg_color, 0.90);
    border: none;
    border-radius: 0;
}
.thread-sidebar-dim {
    background-color: alpha(@window_bg_color, 0.10);
}
.thread-sidebar {
    border-left: 1px solid alpha(@borders, 0.18);
    background-color: alpha(@window_bg_color, 0.96);
    min-width: 330px;
}
.thread-sidebar-list {
    padding: 8px 0px 10px;
}
.thread-sidebar-row {
    border-radius: 12px;
    margin: 4px 10px;
    padding: 8px 10px;
}
.thread-sidebar-row:selected {
    background-color: alpha(@accent_color, 0.11);
}
.thread-sidebar-row:hover {
    background-color: alpha(@window_fg_color, 0.04);
}
.thread-sidebar-avatar {
    min-width: 30px;
    min-height: 30px;
    border-radius: 999px;
    color: #ffffff;
    font-size: 0.70em;
    font-weight: 800;
    letter-spacing: 0.02em;
}
.thread-sidebar-avatar.generic {
    background-color: alpha(@window_fg_color, 0.22);
    color: alpha(@window_fg_color, 0.86);
}
.thread-sidebar-sender {
    font-size: 0.86em;
    font-weight: 700;
}
.thread-sidebar-snippet {
    font-size: 0.76em;
    color: alpha(@window_fg_color, 0.68);
}
.thread-sidebar-time {
    font-size: 0.74em;
    color: alpha(@window_fg_color, 0.70);
}
.thread-sidebar-strip {
    min-width: 4px;
    min-height: 18px;
    border-radius: 999px;
}
.content-split separator {
    min-width: 7px;
    background-color: transparent;
    background-image: linear-gradient(
        to right,
        transparent 0,
        transparent 3px,
        alpha(@borders, 0.18) 3px,
        alpha(@borders, 0.18) 4px,
        transparent 4px,
        transparent 100%
    );
    background-repeat: no-repeat;
    background-position: center;
    border: none;
    box-shadow: none;
}
.content-split separator:hover,
.content-split separator:focus,
.content-split separator:backdrop {
    background-color: transparent;
    border: none;
    box-shadow: none;
}
.message-column {
    background-color: alpha(@window_fg_color, 0.028);
    border-radius: 14px;
}
.attachment-chip {
    border-radius: 8px;
    border: 1px solid alpha(@borders, 0.24);
}
.countdown-lbl {
    font-family: monospace;
    font-variant-numeric: tabular-nums;
    font-size: 0.72em;
    color: alpha(@window_fg_color, 0.45);
    min-width: 54px;
}
.countdown-hint {
    font-size: 0.62em;
    color: alpha(@window_fg_color, 0.42);
    line-height: 1.0;
}
"""

ACCOUNT_PALETTE = [
    '#4c7fff',
    '#16a085',
    '#e67e22',
    '#c05dff',
    '#e74c3c',
    '#2ecc71',
    '#f1c40f',
    '#3498db',
]


def account_class_for_index(idx):
    return f'account-accent-{idx % len(ACCOUNT_PALETTE)}'


def _hex_to_rgba(hex_color, alpha=1.0):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return f'rgba(120,120,120,{alpha})'
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def build_compose_account_css():
    parts = []
    parts.append(
        """
.compose-account-popover {
    border-radius: 12px;
}
.compose-account-popover listbox {
    background: transparent;
}
.compose-account-row-content {
    padding: 7px 10px 7px 8px;
    border-radius: 10px;
}
.compose-account-label {
    font-size: 0.95em;
    color: alpha(@window_fg_color, 0.92);
}
.compose-account-pill {
    padding: 5px 12px;
    min-height: 30px;
    border-radius: 999px;
}
.compose-account-row {
    min-width: 254px;
    min-height: 34px;
    padding: 0;
    background: transparent;
    border-radius: 10px;
}
.compose-account-row,
.compose-account-row:hover,
.compose-account-row:selected {
    background: transparent;
}
.compose-account-row .compose-account-strip {
    min-width: 4px;
    min-height: 18px;
    border-radius: 999px;
}
.compose-account-row:selected .compose-account-label {
    color: alpha(@window_fg_color, 0.96);
    font-weight: 700;
}
"""
    )
    for i, color in enumerate(ACCOUNT_PALETTE):
        cls = account_class_for_index(i)
        parts.append(
            f"""
.{cls}.compose-account-row .compose-account-strip {{
    background-color: {_hex_to_rgba(color, 0.88)};
}}
"""
        )
    return ''.join(parts)


def build_window_account_css():
    parts = []
    for i, color in enumerate(ACCOUNT_PALETTE):
        cls = account_class_for_index(i)
        glow = _hex_to_rgba(color, 0.14)
        glow_selected = _hex_to_rgba(color, 0.22)
        strip = _hex_to_rgba(color, 0.95)
        label = _hex_to_rgba(color, 0.88)
        parts.append(
            f"""
.email-row.{cls} {{
    background-image: linear-gradient(to left, {glow}, rgba(0,0,0,0));
}}
.email-row.{cls}:selected,
.email-row.{cls}.selected {{
    background-image: linear-gradient(to left, {glow_selected}, rgba(0,0,0,0));
}}
.navigation-sidebar row.{cls} .account-accent-strip {{
    background-color: {strip};
}}
.navigation-sidebar row.{cls} .account-accent-label {{
    color: {label};
}}
"""
        )
    parts.append(
        """
.navigation-sidebar row .folder-count.folder-count-dim {
    background-color: alpha(@window_fg_color, 0.04);
    color: alpha(@window_fg_color, 0.42);
}
"""
    )
    return ''.join(parts)
