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
.email-row.{cls}:selected {{
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
