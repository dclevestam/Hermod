CSS = """
/* ═════════════════════════════════════════════════════════════
   Hermod design tokens — ported from design_handoff_hermod/tokens.css.
   Night theme is the default; day theme + variants are layered below.
   Kept as @define-color so Adwaita widgets (buttons, entries, popovers)
   inherit the palette automatically.
   ═════════════════════════════════════════════════════════════ */
@define-color hermod_bg              #0B0F12;
@define-color hermod_bg_elevated     #0F1417;
@define-color hermod_bg_hover        #141A1E;
@define-color hermod_surface_card    #11171B;
@define-color hermod_surface_sunken  #090C0F;
@define-color hermod_surface_reader  #0B0F12;

@define-color hermod_fg              #F2F1ED;
@define-color hermod_fg_muted        #A6ADB3;
@define-color hermod_fg_dim          alpha(#A6ADB3, 0.56);
@define-color hermod_fg_faint        alpha(#A6ADB3, 0.34);

@define-color hermod_border          alpha(#A6ADB3, 0.08);
@define-color hermod_border_strong   alpha(#A6ADB3, 0.14);
@define-color hermod_border_faint    alpha(#A6ADB3, 0.04);

@define-color hermod_accent          #2E6A70;
@define-color hermod_accent_weak     alpha(#2E6A70, 0.16);
@define-color hermod_accent_fg       #F2F1ED;

@define-color hermod_success         #6F9B73;
@define-color hermod_warning         #C9A869;
@define-color hermod_danger          #C76D63;

/* Override Adwaita's own accent so buttons/entries pick up Hermod's teal. */
@define-color accent_color           #2E6A70;
@define-color accent_bg_color        #2E6A70;
@define-color accent_fg_color        #F2F1ED;

/* Base typography — Geist (body) + Geist Mono (metadata), bundled in
   assets/fonts/ and registered via fonts.py. */
window, dialog, popover {
    font-family: "Geist", "Inter", -apple-system, system-ui, "Cantarell", sans-serif;
    font-size: 13px;
    letter-spacing: -0.005em;
}

.mono,
.eyebrow,
.reader-meta {
    font-family: "Geist Mono", ui-monospace, "SF Mono", "Menlo", "Consolas", monospace;
    letter-spacing: 0;
}

@keyframes hermod-spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
}

@keyframes hermod-status-pulse {
    0% { opacity: 0.50; }
    50% { opacity: 1.00; }
    100% { opacity: 0.50; }
}

@keyframes hermod-welcome-breathe {
    0% { opacity: 0.18; }
    50% { opacity: 0.42; }
    100% { opacity: 0.18; }
}

@keyframes hermod-aurora-drift {
    0%   { transform: translate3d(0, 0, 0) rotate(0deg) scale(1.00); }
    50%  { transform: translate3d(24px, -18px, 0) rotate(6deg) scale(1.05); }
    100% { transform: translate3d(0, 0, 0) rotate(0deg) scale(1.00); }
}

.email-row {
    border-bottom: 1px solid alpha(@borders, 0.4);
    transition: background-color 0.2s ease;
}
.message-list-view .email-row.unread {
    box-shadow: none;
}
/* Suppress GTK4/Adwaita native row-level hover and selection — we style the child instead. */
.message-list-view row,
.message-list-view row:hover,
.message-list-view row:selected,
.message-list-view row:selected:hover {
    background: none;
    box-shadow: none;
}
/* Hover: barely-there background shift, only on unselected rows */
.message-list-view row:hover .email-row:not(.selected) {
    background-color: alpha(@hermod_fg, 0.03);
}
/* Selected: 2px accent left bar, subtle bg tint */
.message-list-view .email-row.selected {
    background-image: none;
    background-color: rgba(46, 106, 112, 0.10);
    box-shadow: inset 2px 0 0 0 #2e6a70;
    border-bottom-color: rgba(46, 106, 112, 0.18);
}
.message-list-view row:hover .email-row.selected {
    background-image: none;
    background-color: rgba(46, 106, 112, 0.14);
}
.thread-indicator {
    background-color: alpha(@hermod_fg, 0.06);
    border-radius: 999px;
    padding: 0px 6px;
    min-height: 18px;
}
.thread-indicator image {
    color: alpha(@hermod_fg_muted, 0.74);
}
.thread-badge {
    color: alpha(@hermod_fg_muted, 0.80);
    font-size: 0.68em;
    font-weight: 700;
    margin-left: 2px;
}
.thread-badge-threaded {
    color: @accent_fg_color;
}
.folder-count {
    color: @hermod_fg_faint;
    font-size: 11px;
    font-weight: 500;
    min-width: 10px;
    font-variant-numeric: tabular-nums;
    margin-right: 4px;
}
.folder-count-dim {
    color: @hermod_fg_faint;
}
.all-inboxes-row {
    font-weight: 600;
}
.all-inboxes-row .folder-count {
    color: @hermod_fg_faint;
    font-weight: 500;
}
.navigation-sidebar row:selected .folder-count,
.navigation-sidebar row.selected .folder-count {
    color: @hermod_fg_muted;
}
.navigation-sidebar row:selected .folder-count-dim,
.navigation-sidebar row.selected .folder-count-dim {
    color: @hermod_fg_dim;
}
.account-header {
    font-size: 0.84em;
    font-weight: 500;
    letter-spacing: 0.00em;
    color: alpha(@window_fg_color, 0.68);
}
.account-health-icon {
    color: alpha(@warning_color, 0.94);
    margin-right: 2px;
}
.account-health-icon.state-error {
    color: alpha(@error_color, 0.95);
}
.more-folders-label {
    font-size: 0.84em;
    color: alpha(@window_fg_color, 0.55);
}
.folder-row {
    min-height: 30px;
}
.account-header-row {
    min-height: 32px;
}
.folder-connector,
.folder-connector-last {
    box-shadow: inset 1px 0 0 0 rgba(166, 173, 179, 0.14);
    min-width: 14px;
    min-height: 30px;
}
.folder-connector-last {
    background-image: linear-gradient(to bottom, rgba(166, 173, 179, 0.14) 50%, transparent 50%);
    background-size: 1px 100%;
    background-repeat: no-repeat;
    background-position: 0 0;
    box-shadow: none;
}
.email-actions {
    margin-left: 14px;
    background-color: #e53935;
    border-radius: 10px 0 0 10px;
    padding: 0 6px 0 10px;
    min-height: 38px;
    box-shadow: -2px 0 8px alpha(black, 0.12);
    opacity: 0;
    transition: opacity 0.18s ease;
}
.email-actions button {
    color: white;
}
.email-row.selected .email-actions,
.message-list-view row:hover .email-actions {
    opacity: 1;
}
.load-more-row {
    background-color: transparent;
    padding: 6px 0px 8px;
}
.load-more-row.selected {
    background-color: transparent;
}
.load-more-row button {
    min-height: 26px;
    font-size: 0.78em;
    font-weight: 600;
    padding: 3px 16px;
    border-radius: 999px;
    color: alpha(@hermod_fg_muted, 0.78);
    background-color: alpha(#11171b, 0.88);
    border: 1px solid alpha(@hermod_fg, 0.10);
}
.load-more-row button:hover {
    background-color: alpha(#141a1e, 0.96);
    color: @hermod_fg;
    border-color: alpha(@hermod_fg, 0.18);
}
.account-accent-strip {
    border-radius: 999px;
    min-width: 4px;
    min-height: 18px;
}
.account-status-dot {
    border-radius: 999px;
    min-width: 8px;
    min-height: 8px;
    background-color: alpha(@accent_bg_color, 0.95);
    box-shadow: 0 0 0 2px alpha(@accent_bg_color, 0.18);
    margin-right: 4px;
    margin-left: 2px;
}
.account-header-chevron {
    color: alpha(@hermod_fg_muted, 0.56);
    -gtk-icon-size: 10px;
    margin-left: 2px;
}
.account-header-row:hover .account-header-chevron {
    color: alpha(@hermod_fg, 0.80);
}
.account-accent-label {
    font-size: 0.88em;
    font-weight: 500;
}
/* Sidebar row base: muted text, dim icon, tight radius. */
.navigation-sidebar > row {
    margin: 0 8px;
    border-radius: 6px;
    color: @hermod_fg_muted;
    transition: background-color 120ms ease, color 120ms ease;
}
.navigation-sidebar > row .account-accent-label {
    color: @hermod_fg_muted;
    font-weight: 400;
}
.navigation-sidebar > row image {
    color: @hermod_fg_dim;
}
/* Suppress Adwaita's native sidebar row selected/hover backgrounds */
.navigation-sidebar row:selected,
.navigation-sidebar row:selected:hover,
.navigation-sidebar row.selected,
.navigation-sidebar row.selected:hover {
    background: none;
    box-shadow: none;
}
/* Hover (exclude label-only rows) */
.navigation-sidebar > row:hover {
    background-color: @hermod_bg_hover;
    color: @hermod_fg;
}
.navigation-sidebar > row:hover .account-accent-label {
    color: @hermod_fg;
}
.navigation-sidebar > row:hover image {
    color: @hermod_fg_muted;
}
/* Selected — bg + accent icon + fg text + 500 weight; no left-bar per design */
.navigation-sidebar > row:selected,
.navigation-sidebar > row.selected {
    background-color: @hermod_bg_hover;
    color: @hermod_fg;
}
.navigation-sidebar > row:selected:hover,
.navigation-sidebar > row.selected:hover {
    background-color: @hermod_bg_hover;
}
.navigation-sidebar > row:selected .account-accent-label,
.navigation-sidebar > row.selected .account-accent-label {
    color: @hermod_fg;
    font-weight: 500;
}
.navigation-sidebar > row:selected image,
.navigation-sidebar > row.selected image {
    color: @hermod_accent;
}
/* Section labels (MAILBOXES / ACCOUNTS): fully inert, no hover/selected */
.navigation-sidebar > row.sidebar-section,
.navigation-sidebar > row.sidebar-section:hover,
.navigation-sidebar > row.sidebar-section:selected,
.navigation-sidebar > row.sidebar-section:active,
.navigation-sidebar > row.sidebar-section.selected {
    background: transparent;
    box-shadow: none;
}
.navigation-sidebar > row.sidebar-section:hover image,
.navigation-sidebar > row.sidebar-section:hover .account-accent-label,
.navigation-sidebar > row.sidebar-section:hover .sidebar-section-label {
    color: @hermod_fg_faint;
}
/* Account header rows (identity label): inert, no hover/selected surface */
.navigation-sidebar > row.account-header-row,
.navigation-sidebar > row.account-header-row:hover,
.navigation-sidebar > row.account-header-row:selected,
.navigation-sidebar > row.account-header-row:active,
.navigation-sidebar > row.account-header-row.selected {
    background: transparent;
    box-shadow: none;
    color: @hermod_fg_muted;
}
.navigation-sidebar > row.account-header-row:hover .account-accent-label,
.navigation-sidebar > row.account-header-row:hover image {
    color: @hermod_fg_muted;
}
.message-column-header {
    border-bottom: 1px solid alpha(@hermod_fg, 0.06);
    padding: 18px 20px 14px;
}
.message-column-eyebrow {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.72em;
    font-weight: 500;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: alpha(@window_fg_color, 0.58);
}
.message-column-meta {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 0.86em;
    color: alpha(@window_fg_color, 0.52);
}
.message-filter-segmented {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}
.message-filter-segmented .message-filter-chip {
    min-height: 24px;
    padding: 0 10px;
    border: 1px solid alpha(@hermod_fg, 0.08);
    border-radius: 999px;
    background: transparent;
    color: @hermod_fg_muted;
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 0.82em;
    font-weight: 500;
    box-shadow: none;
    margin: 0 2px;
}
.message-filter-segmented .message-filter-chip:hover {
    color: alpha(@window_fg_color, 0.92);
    background-color: alpha(@hermod_fg, 0.04);
}
.message-filter-segmented .message-filter-chip.selected,
.message-filter-segmented .message-filter-chip:checked {
    color: @hermod_fg;
    background-color: @hermod_accent_weak;
    border-color: alpha(@accent_color, 0.28);
    box-shadow: none;
}
.day-group-row {
    background: transparent;
}
.day-group-row:hover,
.day-group-row:selected,
.day-group-row:active {
    background: transparent;
    box-shadow: none;
}
.day-group-label {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.72em;
    font-weight: 500;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: alpha(@window_fg_color, 0.48);
}
.sorting-toggle {
    min-width: 26px;
    min-height: 26px;
    padding: 3px;
    border-radius: 8px;
    color: alpha(@hermod_fg_muted, 0.80);
    background-color: alpha(#11171b, 0.90);
    border: 1px solid alpha(@hermod_fg, 0.06);
}
.sorting-toggle image {
    color: inherit;
}
.sorting-toggle:hover {
    background-color: alpha(#141a1e, 0.96);
    color: @hermod_fg;
}
.sorting-toggle.active {
    background-color: alpha(#141a1e, 0.98);
    color: @hermod_fg;
    border-color: alpha(#2e6a70, 0.26);
}
.load-older-toolbar {
    min-height: 26px;
    padding: 0px 10px;
    border-radius: 999px;
    color: alpha(@hermod_fg_muted, 0.82);
    background-color: alpha(#11171b, 0.88);
    border: 1px solid alpha(@hermod_fg, 0.10);
    font-size: 0.82em;
    font-weight: 700;
}
.load-older-toolbar:hover {
    background-color: alpha(#141a1e, 0.96);
    color: @hermod_fg;
    border-color: alpha(@hermod_fg, 0.18);
}
.load-older-toolbar:disabled {
    opacity: 0.88;
}
.startup-status-panel {
    background-color: transparent;
}
.startup-status-card {
    border-radius: 14px;
    border: 1px solid alpha(@hermod_fg, 0.08);
    background-color: alpha(#11171b, 0.95);
    box-shadow: 0 18px 34px alpha(@window_fg_color, 0.12);
}
.startup-status-hero {
    padding: 22px 22px 16px;
    border-bottom: 1px solid alpha(@hermod_fg, 0.08);
}
.startup-status-orb {
    min-width: 78px;
    min-height: 78px;
    border-radius: 14px;
    background-image: linear-gradient(180deg, alpha(@accent_color, 0.22), alpha(@accent_color, 0.08));
    box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.24);
}
.startup-status-orb image {
    color: @accent_color;
}
.startup-status-heading {
    font-size: 1.38em;
    font-weight: 800;
    letter-spacing: -0.03em;
}
.startup-status-subtitle {
    color: alpha(@hermod_fg_muted, 0.80);
    font-size: 0.90em;
    line-height: 1.35;
}
.startup-status-mood {
    color: alpha(@hermod_fg_muted, 0.70);
    font-size: 0.83em;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.startup-status-progress {
    min-height: 10px;
    margin-top: 4px;
}
.startup-status-progress trough {
    border-radius: 999px;
    background-color: alpha(@hermod_fg, 0.08);
}
.startup-status-progress progress {
    border-radius: 999px;
    background-image: linear-gradient(90deg, alpha(#2e6a70, 0.95), alpha(#24362e, 0.82));
}
.startup-status-summary {
    color: @hermod_fg;
    font-size: 0.82em;
    font-weight: 800;
    padding: 5px 11px;
    border-radius: 999px;
    background-color: alpha(#11171b, 0.92);
    border: 1px solid alpha(@hermod_fg, 0.08);
}
.startup-status-list {
    background: transparent;
    padding: 8px 4px 4px;
}
.startup-status-issues {
    padding: 4px 8px 2px;
}
.startup-status-issues-title {
    color: alpha(@hermod_fg_muted, 0.68);
    font-size: 0.72em;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.startup-status-issue-row {
    padding: 8px 10px;
    border-radius: 14px;
    background-color: alpha(#11171b, 0.88);
    border: 1px solid alpha(@hermod_fg, 0.08);
}
.startup-status-issue-icon {
    margin-top: 2px;
}
.startup-status-issue-title {
    font-size: 0.82em;
    font-weight: 800;
}
.startup-status-issue-detail {
    color: alpha(@hermod_fg_muted, 0.78);
    font-size: 0.78em;
    line-height: 1.25;
}
.startup-status-issue-row.state-warning .startup-status-issue-icon,
.startup-status-issue-row.state-warning .startup-status-issue-title {
    color: alpha(@warning_color, 0.98);
}
.startup-status-issue-row.state-warning .startup-status-issue-detail {
    color: alpha(#c9a869, 0.84);
}
.startup-status-issue-row.state-warning .startup-status-strip {
    background-color: alpha(@warning_color, 0.92);
}
.startup-status-issue-row.state-error .startup-status-issue-icon,
.startup-status-issue-row.state-error .startup-status-issue-title {
    color: alpha(@error_color, 0.98);
}
.startup-status-issue-row.state-error .startup-status-issue-detail {
    color: alpha(#c76d63, 0.84);
}
.startup-status-issue-row.state-error .startup-status-strip {
    background-color: alpha(@error_color, 0.92);
}
.startup-status-close {
    padding: 0px 12px;
    border-radius: 999px;
    min-height: 28px;
    font-weight: 700;
}
.startup-status-row.state-pending .startup-status-strip {
    background-color: alpha(@hermod_fg_muted, 0.18);
}
.startup-status-row.state-checking .startup-status-strip {
    background-color: alpha(#2e6a70, 0.86);
}
.startup-status-row.state-ready .startup-status-strip {
    background-color: alpha(#6f9b73, 0.92);
}
.startup-status-row.state-warning .startup-status-strip {
    background-color: alpha(#c9a869, 0.92);
}
.startup-status-row.state-error .startup-status-strip {
    background-color: alpha(#c76d63, 0.92);
}
.startup-status-row.state-warning .startup-status-title,
.startup-status-row.state-warning .startup-status-detail,
.startup-status-row.state-warning .startup-status-indicator,
.startup-status-row.state-warning .startup-status-indicator image {
    color: alpha(#c9a869, 0.98);
}
.startup-status-row.state-error .startup-status-title,
.startup-status-row.state-error .startup-status-detail,
.startup-status-row.state-error .startup-status-indicator,
.startup-status-row.state-error .startup-status-indicator image {
    color: alpha(#c76d63, 0.98);
}
.welcome-screen,
.welcome-settings-shell {
    background-color: #0b0f12;
}
.welcome-photo {
    background-color: #0c1613;
    border-right: 1px solid alpha(@hermod_fg_muted, 0.08);
}
.welcome-header-bar {
    background: transparent;
    border-bottom: 1px solid alpha(@hermod_fg_muted, 0.08);
    box-shadow: none;
    min-height: 40px;
    padding: 0 16px;
}
.hermod-header {
    background: linear-gradient(180deg, #131A1E 0%, #0E1418 100%);
    border-bottom: 1px solid @hermod_border;
    box-shadow: none;
    min-height: 46px;
    padding: 0 10px 0 14px;
}
.hermod-header windowhandle,
.hermod-header windowhandle > box {
    background: transparent;
}
.hermod-header-brand-row {
    padding: 0 4px;
    min-width: 0;
}
.hermod-header-mark {
    -gtk-icon-size: 18px;
    color: @hermod_fg;
    opacity: 0.92;
}
.hermod-header-brand-label {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: @hermod_fg;
}
.hermod-header-separator {
    background: rgba(166, 173, 179, 0.14);
    min-width: 1px;
}
.hermod-header-crumb-title {
    font-family: "Geist", "Inter", sans-serif;
    font-size: 12px;
    font-weight: 400;
    color: @hermod_fg_muted;
}
.hermod-header-crumb-subtitle {
    font-family: "Geist", "Inter", sans-serif;
    font-size: 12px;
    font-weight: 400;
    color: alpha(@hermod_fg_muted, 0.62);
    margin-left: 2px;
}
.hermod-header-sync,
.hermod-header-settings {
    -gtk-icon-size: 14px;
    min-height: 26px;
    min-width: 26px;
    margin: 0 -2px;
    padding: 0 4px;
    border-radius: 8px;
    color: @hermod_fg_muted;
    background: transparent;
    border: 1px solid transparent;
    box-shadow: none;
}
.hermod-header-sync image,
.hermod-header-settings image {
    -gtk-icon-style: symbolic;
    opacity: 0.82;
}
.hermod-header-sync:hover,
.hermod-header-settings:hover {
    background: @hermod_bg_hover;
    border-color: @hermod_border;
    color: @hermod_fg;
}
.welcome-header-mark {
    -gtk-icon-size: 18px;
    color: @hermod_fg;
    opacity: 0.92;
}
.welcome-header-brand {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: alpha(@hermod_fg, 0.78);
}
.welcome-photo-caption {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: alpha(@hermod_fg, 0.38);
}
.welcome-right,
.welcome-right-scroll,
.welcome-right-scroll viewport {
    background: transparent;
}
.welcome-inner {
    min-width: 0;
}
.welcome-providers-eyebrow {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: alpha(@hermod_fg_muted, 0.72);
}
.welcome-provider-grid {
    min-width: 0;
}
.welcome-more {
    background: transparent;
    border: none;
    padding: 2px 0;
    font-size: 12.5px;
    color: #2e6a70;
    box-shadow: none;
}
.welcome-more:hover {
    background: transparent;
    color: shade(#2e6a70, 1.18);
}
.provider-row-tile {
    padding: 0;
    border-radius: 10px;
    background: #11171b;
    border: 1px solid alpha(@hermod_fg_muted, 0.08);
    color: @hermod_fg;
    box-shadow: none;
    transition: background-color 0.15s ease, border-color 0.15s ease;
}
.provider-row-tile:hover {
    background: #141a1e;
    border-color: alpha(@hermod_fg_muted, 0.14);
}
.provider-row-tile:focus {
    outline: none;
    box-shadow: 0 0 0 2px alpha(#2e6a70, 0.40);
}
.provider-glyph {
    min-width: 34px;
    min-height: 34px;
    border-radius: 8px;
    font-family: "Geist", sans-serif;
    font-weight: 500;
    font-size: 15px;
    padding: 0;
}
.provider-glyph.glyph-gmail   { background: alpha(#ea4335, 0.14); color: #ea4335; border: 1px solid alpha(#ea4335, 0.28); }
.provider-glyph.glyph_gmail   { background: alpha(#ea4335, 0.14); color: #ea4335; border: 1px solid alpha(#ea4335, 0.28); }
.provider-glyph.glyph-microsoft,
.provider-glyph.glyph_microsoft { background: alpha(#0078d4, 0.14); color: #0078d4; border: 1px solid alpha(#0078d4, 0.28); }
.provider-glyph.glyph-proton,
.provider-glyph.glyph_proton  { background: alpha(#7c4dff, 0.14); color: #7c4dff; border: 1px solid alpha(#7c4dff, 0.28); }
.provider-glyph.glyph-imap_smtp,
.provider-glyph.glyph_imap_smtp { background: alpha(@hermod_fg_muted, 0.14); color: @hermod_fg_muted; border: 1px solid alpha(@hermod_fg_muted, 0.28); }
.provider-glyph.glyph_icloud    { background: alpha(#7b8794, 0.14); color: #b0bac4; border: 1px solid alpha(#7b8794, 0.28); }
.provider-glyph.glyph_fastmail  { background: alpha(#4a90e2, 0.14); color: #4a90e2; border: 1px solid alpha(#4a90e2, 0.28); }
.provider-glyph.glyph_yahoo     { background: alpha(#6001d2, 0.18); color: #a77bf0; border: 1px solid alpha(#6001d2, 0.32); }
.provider-glyph.glyph_zoho      { background: alpha(#e42527, 0.14); color: #e42527; border: 1px solid alpha(#e42527, 0.28); }
.provider-glyph.glyph_exchange  { background: alpha(#0078d4, 0.14); color: #0078d4; border: 1px solid alpha(#0078d4, 0.28); }
.provider-name {
    font-size: 13px;
    font-weight: 600;
    color: @hermod_fg;
}
.provider-sub {
    font-size: 11px;
    color: alpha(@hermod_fg_muted, 0.72);
}
.lock-pill {
    padding: 6px 10px;
    border-radius: 999px;
    background: #11171b;
    border: 1px solid alpha(@hermod_fg_muted, 0.10);
}
.lock-pill-icon {
    min-width: 11px;
    min-height: 11px;
    color: alpha(@hermod_fg_muted, 0.72);
}
.lock-pill-text {
    font-size: 11px;
    color: alpha(@hermod_fg_muted, 0.72);
}
.welcome-scene {
    background: transparent;
    opacity: 0.64;
}
.welcome-window-close {
    min-width: 32px;
    min-height: 32px;
    margin-top: 1px;
    border-radius: 999px;
    color: #ffffff;
    background: transparent;
    background-color: transparent;
    border: 1px solid transparent;
    box-shadow: none;
}
.welcome-window-close:hover {
    color: #ffffff;
    background: transparent;
    background-color: transparent;
    border-color: transparent;
}
.welcome-wash {
    background: transparent;
}
.welcome-mark {
    min-width: 64px;
    min-height: 64px;
    border-radius: 14px;
    border: 1px solid alpha(@hermod_fg_muted, 0.14);
    background: #11171b;
    padding: 12px;
    opacity: 1.0;
}
.welcome-eyebrow {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.22em;
    color: alpha(@hermod_fg_muted, 0.74);
}
.welcome-title {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 44px;
    font-weight: 400;
    letter-spacing: 0.01em;
    line-height: 1.10;
    color: @hermod_fg;
}
.welcome-summary {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 15px;
    font-weight: 400;
    line-height: 1.50;
    color: alpha(@hermod_fg_muted, 0.88);
}
.provider-tile {
    min-width: 84px;
    min-height: 84px;
    border-radius: 14px;
    border: 1px solid transparent;
    background: transparent;
    box-shadow: none;
    padding: 0;
    color: #ffffff;
    transition: background-color 0.22s ease, border-color 0.22s ease, transform 0.22s ease;
}
.provider-tile:focus,
.provider-tile:focus-within {
    box-shadow: none;
    outline: none;
}
.provider-tile:hover {
    background-color: alpha(#ffffff, 0.06);
    border-color: alpha(@hermod_fg, 0.12);
    transform: scale(1.04);
}
.provider-tile-gmail:hover { background-color: alpha(#ea4335, 0.10); border-color: alpha(#ea4335, 0.20); }
.provider-tile-proton:hover { background-color: alpha(#7c4dff, 0.10); border-color: alpha(#7c4dff, 0.20); }
.provider-tile-microsoft:hover { background-color: alpha(#0078d4, 0.10); border-color: alpha(#0078d4, 0.20); }
.provider-tile-imap-smtp:hover { background-color: alpha(#ff6a3d, 0.10); border-color: alpha(#ff6a3d, 0.20); }
.provider-tile-icon {
    min-width: 28px;
    min-height: 28px;
    color: currentColor;
}
.provider-logo-badge {
    min-width: 28px;
    min-height: 28px;
    border-radius: 10px;
    background: transparent;
    border: none;
    color: currentColor;
}
.provider-logo-badge-text {
    font-size: 0.84em;
    font-weight: 800;
}
.welcome-close-icon {
    min-width: 18px;
    min-height: 18px;
    color: #ffffff;
}
.onboarding-accounts {
    margin-top: 10px;
}
.onboarding-section-title {
    font-size: 0.74em;
    font-weight: 800;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: alpha(@hermod_fg_muted, 0.72);
}
.onboarding-accounts-list {
    padding: 2px 0 0;
}
.onboarding-account-row {
    padding: 12px 14px;
    border-radius: 10px;
    background: alpha(#f5f0e6, 0.04);
    border: 1px solid alpha(#f5f0e6, 0.08);
}
.onboarding-account-bullet {
    min-width: 14px;
    min-height: 14px;
    color: #ffffff;
}
.onboarding-account-accent {
    min-width: 3px;
    min-height: 30px;
    border-radius: 999px;
    margin-right: 4px;
}
.onboarding-account-title {
    font-size: 0.92em;
    font-weight: 600;
}
.onboarding-account-subtitle {
    font-size: 0.80em;
    color: alpha(@hermod_fg_muted, 0.72);
    margin-top: 2px;
}
.onboarding-account-health {
    min-width: 8px;
    min-height: 8px;
    border-radius: 999px;
    background: #6f9b73;
    box-shadow: 0 0 6px alpha(#6f9b73, 0.55);
}
.onboarding-open-btn {
    margin-top: 22px;
    min-height: 36px;
    min-width: 196px;
    border-radius: 999px;
    font-weight: 800;
}
.onboarding-modal-content {
    background: transparent;
}
.onboarding-modal-header {
    margin-bottom: 2px;
}
.onboarding-modal-title {
    font-size: 1.28em;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: @hermod_fg;
}
.onboarding-modal-subtitle {
    font-size: 0.92em;
    color: alpha(@hermod_fg_muted, 0.76);
}
.onboarding-modal-window {
    background: transparent;
}
.onboarding-modal-frame {
    border-radius: 14px;
    border: 1px solid alpha(@hermod_fg_muted, 0.14);
    background: #0f1417;
    box-shadow: 0 18px 46px alpha(black, 0.35);
}
.onboarding-modal-head {
    min-height: 56px;
}
.onboarding-modal-divider {
    background: alpha(@hermod_fg_muted, 0.08);
    min-height: 1px;
}
.onboarding-modal-close {
    min-width: 28px;
    min-height: 28px;
    padding: 0;
    border-radius: 999px;
    background: transparent;
    border: none;
    color: alpha(@hermod_fg, 0.72);
}
.onboarding-modal-close:hover {
    background: alpha(@hermod_fg, 0.06);
    color: @hermod_fg;
}
.onboarding-modal-foot {
    padding-top: 12px;
    border-top: 1px solid alpha(@hermod_fg_muted, 0.08);
    background: #090c0f;
}
.onboarding-modal-scroller,
.onboarding-modal-scroller viewport {
    background: transparent;
}
.welcome-settings-stage {
    min-height: 760px;
}
.welcome-settings-header {
    min-height: 68px;
}
.welcome-settings-back {
    margin-top: 8px;
}
.welcome-settings-title {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 1.74em;
    font-weight: 600;
    letter-spacing: -0.025em;
    color: @hermod_fg;
}
.welcome-settings-subtitle {
    font-size: 0.94em;
    line-height: 1.38;
    color: alpha(@hermod_fg_muted, 0.78);
}
.sidebar-actions {
    padding: 4px 12px 10px;
}
.sidebar-action-btn {
    padding-top: 0px;
    padding-bottom: 0px;
}
.sidebar-compose-btn {
    background-color: @hermod_accent;
    background-image: none;
    border: none;
    border-radius: 10px;
    color: @hermod_accent_fg;
    box-shadow: none;
    min-height: 34px;
    padding: 0 12px;
}
.sidebar-compose-btn:hover {
    background-color: shade(@hermod_accent, 1.10);
}
.sidebar-compose-btn:active {
    background-color: shade(@hermod_accent, 0.92);
}
.sidebar-compose-btn .sidebar-compose-label {
    color: @hermod_accent_fg;
}
.sidebar-compose-btn .sidebar-compose-chip {
    color: rgba(242, 241, 237, 0.70);
    background-color: rgba(0, 0, 0, 0.20);
    border: none;
}
.sidebar-compose-btn image {
    color: @hermod_accent_fg;
}
.sidebar-action-btn.action-feedback {
    box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.34);
}
.sync-control.action-feedback {
    background-color: alpha(@accent_color, 0.12);
}
.sidebar-compose-label {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: -0.005em;
}
.sidebar-compose-chip {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.06em;
    color: rgba(242, 241, 237, 0.70);
    background-color: rgba(0, 0, 0, 0.20);
    border: none;
    border-radius: 4px;
    padding: 2px 5px;
    margin-left: 6px;
}
.sidebar-search {
    margin: 0 12px 12px;
    padding: 0 10px;
    background: transparent;
    border: 1px solid @hermod_border;
    border-radius: 10px;
    min-height: 30px;
}
.sidebar-search:hover {
    background-color: @hermod_bg_hover;
}
.sidebar-search-icon {
    color: @hermod_fg_dim;
}
.sidebar-search-entry {
    background-color: transparent;
    border: none;
    box-shadow: none;
    outline: none;
    color: @hermod_fg;
    font-size: 12px;
    padding: 0;
    min-height: 22px;
}
.sidebar-search-entry:focus,
.sidebar-search-entry:focus-within {
    background-color: transparent;
    outline: none;
    box-shadow: none;
}
.sidebar-search-kbd {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.06em;
    color: rgba(242, 241, 237, 0.70);
    background-color: rgba(0, 0, 0, 0.20);
    border: none;
    border-radius: 4px;
    padding: 2px 5px;
}
.sidebar-status {
    padding: 8px 12px 2px;
    border-top: none;
}
.sidebar-status > box {
    min-height: 28px;
    padding: 0 10px;
    background-color: @hermod_surface_card;
    border: 1px solid @hermod_border;
    border-radius: 10px;
    margin-bottom: 6px;
}
.sidebar-status-dot {
    border-radius: 999px;
    min-width: 6px;
    min-height: 6px;
}
.sidebar-status-dot-online {
    background-color: @hermod_success;
    box-shadow: 0 0 6px alpha(@hermod_success, 0.5);
}
.sidebar-status-label {
    font-size: 11px;
    color: @hermod_fg_muted;
}
.sidebar-status-age {
    font-size: 11px;
    color: @hermod_fg_faint;
    font-variant-numeric: tabular-nums;
}
.sidebar-local-icon {
    color: @hermod_fg_muted;
    -gtk-icon-size: 12px;
}
.thread-summary-banner {
    margin: 8px 18px 0;
    padding: 10px 14px;
    background-color: alpha(@hermod_fg, 0.05);
    border: 1px solid alpha(@hermod_fg, 0.08);
    border-radius: 10px;
}
.thread-summary-title {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.68em;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: alpha(@hermod_fg_muted, 0.72);
}
.thread-summary-chip {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.62em;
    font-weight: 600;
    letter-spacing: 0.10em;
    color: #9ED7DC;
    background-color: rgba(46, 106, 112, 0.24);
    border-radius: 5px;
    padding: 1px 6px;
}
.thread-summary-text {
    font-size: 0.88em;
    color: alpha(@hermod_fg, 0.85);
    line-height: 1.4;
}
.smart-reply-bar {
    margin: 6px 18px 0;
    padding: 8px 14px;
    background-color: alpha(@hermod_fg, 0.04);
    border: 1px solid alpha(@hermod_fg, 0.08);
    border-radius: 10px;
}
.smart-reply-title {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.66em;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: alpha(@hermod_fg_muted, 0.72);
}
.smart-reply-chip {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.60em;
    font-weight: 600;
    letter-spacing: 0.10em;
    color: #9ED7DC;
    background-color: rgba(46, 106, 112, 0.24);
    border-radius: 5px;
    padding: 1px 6px;
}
.smart-reply-write {
    font-size: 0.80em;
    padding: 4px 10px;
    border-radius: 999px;
    color: alpha(@hermod_fg_muted, 0.82);
}
.smart-reply-write:hover {
    color: @hermod_fg;
    background-color: alpha(@hermod_fg, 0.06);
}
.smart-reply-chip-btn {
    font-size: 0.82em;
    font-weight: 500;
    padding: 4px 12px;
    border-radius: 999px;
    background-color: @hermod_surface_card;
    border: 1px solid alpha(@hermod_fg, 0.10);
    color: alpha(@hermod_fg, 0.88);
    min-height: 24px;
    box-shadow: none;
}
.smart-reply-chip-btn:hover {
    background-color: @hermod_bg_hover;
    border-color: alpha(@hermod_accent, 0.40);
    color: @hermod_fg;
}
.smart-reply-chip-btn:active {
    background-color: alpha(@hermod_accent, 0.18);
}
.thread-summary-close {
    padding: 2px;
    min-width: 20px;
    min-height: 20px;
    border-radius: 6px;
    color: alpha(@hermod_fg_muted, 0.70);
}
.thread-summary-close:hover {
    color: @hermod_fg;
    background-color: alpha(@hermod_fg, 0.06);
}
.reader-thread-btn {
    padding: 4px 8px;
}
.thread-msg-count {
    font-family: "Geist Mono", ui-monospace, monospace;
    font-size: 0.72em;
    font-weight: 600;
    color: alpha(@hermod_fg, 0.75);
}
.message-row-avatar {
    min-width: 28px;
    min-height: 28px;
    border-radius: 999px;
    background-color: #2A323A;
    color: #F2F1ED;
    font-size: 0.72em;
    font-weight: 600;
    letter-spacing: 0.01em;
    box-shadow: none;
}
.email-row.selected .message-row-avatar {
    background-color: alpha(@hermod_accent, 0.40);
    color: @hermod_fg;
}
.message-row-sender {
    font-size: 0.96em;
    font-weight: 600;
    color: alpha(@hermod_fg, 0.92);
    letter-spacing: -0.005em;
}
.email-row.unread .message-row-sender {
    font-weight: 700;
    color: @hermod_fg;
}
.message-row-subject {
    font-size: 0.86em;
    font-weight: 400;
    color: alpha(@hermod_fg_muted, 0.80);
    letter-spacing: 0;
}
.email-row.unread .message-row-subject {
    color: alpha(@hermod_fg, 0.88);
    font-weight: 500;
}
.message-row-date {
    font-size: 0.76em;
    color: alpha(@hermod_fg_muted, 0.70);
    font-variant-numeric: tabular-nums;
}
.sidebar-section {
    background: transparent;
}
.sidebar-section:hover,
.sidebar-section:selected,
.sidebar-section:active {
    background: transparent;
    box-shadow: none;
}
.sidebar-section-label {
    font-family: "Geist", "Inter", -apple-system, system-ui, sans-serif;
    font-size: 0.68em;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: alpha(@hermod_fg_muted, 0.62);
    margin: 14px 0 4px 0;
}
.account-header {
    font-size: 0.92em;
    font-weight: 500;
    letter-spacing: 0.00em;
    color: alpha(@window_fg_color, 0.82);
}
.sync-control {
    padding: 0px;
    border-radius: 10px;
    border: 1px solid alpha(@hermod_fg, 0.10);
    background-color: alpha(#11171b, 0.88);
}
.sync-control box,
.sync-control label,
.sync-control image,
.sync-control separator {
    background: transparent;
}
.sync-control.sync-online {
    background-color: rgba(111, 155, 115, 0.16);
    color: rgba(242, 239, 232, 0.96);
}
.sync-control.sync-offline {
    background-color: rgba(218, 110, 99, 0.16);
    color: rgba(242, 239, 232, 0.96);
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
    background-color: alpha(@hermod_fg, 0.28);
    border-radius: 999px;
}
.sync-auto-value {
    font-size: 0.76em;
    font-weight: 800;
    letter-spacing: 0.06em;
}
.sync-control.sync-online .sync-auto-value {
    color: rgba(159, 201, 166, 0.96);
}
.sync-control.sync-offline .sync-auto-value {
    color: rgba(242, 239, 232, 0.98);
}
.sync-control.sync-syncing .sync-auto-value {
    color: rgba(159, 201, 166, 0.86);
}
.sync-control.sync-syncing .sync-online-icon {
    animation: hermod-spin 1s linear infinite;
}
.sync-control.sync-online .sync-divider {
    background-color: alpha(@borders, 0.52);
}
.sync-control.sync-offline .sync-divider {
    background-color: alpha(@borders, 0.52);
}
.sync-online-icon {
    color: rgba(159, 201, 166, 0.98);
    padding: 0;
}
.sync-control.sync-offline .sync-online-icon {
    color: rgba(218, 110, 99, 0.98);
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
    border-top: 1px solid alpha(@hermod_fg, 0.08);
    background-color: alpha(#11171b, 0.95);
    padding: 6px 10px 8px;
}
.thread-reply-bar {
    border-top: 1px solid alpha(@hermod_fg, 0.08);
    background-color: alpha(#0f1417, 0.96);
    padding: 8px 10px 10px;
}
.thread-reply-editor {
    min-height: 62px;
    background-color: alpha(#11171b, 0.92);
    border: 1px solid alpha(@hermod_fg, 0.10);
    border-radius: 10px;
    padding: 8px 10px;
}
.thread-reply-send {
    min-width: 84px;
    min-height: 30px;
    font-weight: 700;
}
.thread-reply-pencil {
    color: alpha(@hermod_fg_muted, 0.64);
    -gtk-icon-size: 14px;
    margin-left: 4px;
    margin-right: 2px;
}
.message-info-bar {
    border-bottom: 1px solid alpha(@hermod_fg, 0.06);
    background-color: @hermod_surface_reader;
    padding: 20px 32px 16px;
    min-height: 72px;
}
.message-info-top {
    min-height: 32px;
}
.message-info-actions {
    min-width: 118px;
}
.reader-header {
    /* Composition handled on .message-info-bar; kept as a marker selector
       so theme overrides can target the redesigned reader chrome. */
}
.reader-subject {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 1.9em;
    font-weight: 600;
    color: @hermod_fg;
    letter-spacing: -0.02em;
    line-height: 1.16;
}
.reader-meta {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 0.88em;
    color: alpha(@window_fg_color, 0.58);
    margin-top: 6px;
}
.reader-actions {
    margin-left: 8px;
}
.reader-action-btn {
    min-height: 30px;
    min-width: 30px;
    padding: 4px;
    border-radius: 999px;
    color: alpha(@window_fg_color, 0.72);
    background: transparent;
}
.reader-action-btn:hover {
    background-color: alpha(@window_fg_color, 0.06);
    color: @hermod_fg;
}
.reader-action-btn:disabled,
.reader-action-btn:insensitive {
    color: alpha(@window_fg_color, 0.28);
    background: transparent;
}
.thread-info-button {
    min-height: 26px;
    padding: 0px 10px;
    font-size: 0.82em;
    font-weight: 700;
    border-radius: 999px;
    background-color: alpha(#11171b, 0.90);
    border: 1px solid alpha(@hermod_fg, 0.10);
    color: alpha(@hermod_fg, 0.82);
}
.thread-msg-count {
    font-size: 0.76em;
    font-weight: 800;
    opacity: 0.90;
    font-variant-numeric: tabular-nums;
}
.thread-info-button.active {
    background-color: rgba(46, 106, 112, 0.18);
    color: @hermod_fg;
    border-color: rgba(46, 106, 112, 0.28);
    box-shadow: none;
}
.message-info-sender-line {
    color: alpha(@hermod_fg_muted, 0.82);
}
.message-info-subject {
    font-family: "Geist", -apple-system, system-ui, sans-serif;
    font-size: 1.9em;
    font-weight: 600;
    color: @hermod_fg;
    letter-spacing: -0.02em;
    line-height: 1.16;
}
.message-info-sender {
    font-size: 0.80em;
    font-weight: 400;
    color: alpha(@hermod_fg_muted, 0.84);
    line-height: 1.10;
}
.message-info-date {
    font-size: 0.80em;
    font-weight: 400;
    color: alpha(@hermod_fg_muted, 0.80);
    line-height: 1.10;
}
.message-info-meta {
    font-size: 0.78em;
    color: alpha(@hermod_fg_muted, 0.72);
}
.reading-pane-shell {
    background-color: @hermod_surface_reader;
    border: none;
    border-radius: 0;
}
.hermod-sidebar-column {
    background-image: linear-gradient(180deg, #0D1215 0%, #0A0E11 100%);
    border-right: 1px solid @hermod_border;
    padding: 10px 0 12px;
}
.thread-sidebar {
    border-left: 1px solid alpha(@hermod_fg, 0.08);
    background-color: alpha(#0b0f12, 0.98);
    min-width: 330px;
}
.thread-sidebar-list {
    padding: 6px 0px 8px;
}
.thread-sidebar-row {
    border-radius: 0;
    margin: 0;
    padding: 10px 14px;
    border-bottom: 1px solid @hermod_border_faint;
}
.thread-sidebar-row:selected {
    background-color: rgba(46, 106, 112, 0.14);
}
.thread-sidebar-row:hover {
    background-color: alpha(@hermod_fg, 0.04);
}
.thread-sidebar-avatar {
    min-width: 28px;
    min-height: 28px;
    border-radius: 999px;
    color: #ffffff;
    font-size: 0.70em;
    font-weight: 800;
    letter-spacing: 0.02em;
}
.thread-sidebar-avatar.generic {
    background-color: alpha(@hermod_fg, 0.10);
    color: alpha(@hermod_fg, 0.84);
}
.thread-sidebar-sender {
    font-size: 0.84em;
    font-weight: 700;
}
.thread-sidebar-snippet {
    font-size: 0.75em;
    color: alpha(@hermod_fg_muted, 0.72);
}
.thread-sidebar-time {
    font-size: 0.72em;
    color: alpha(@hermod_fg_muted, 0.72);
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
    background-color: @hermod_bg;
    border-radius: 0;
    border: none;
    border-right: 1px solid @hermod_border;
}
.attachment-chip {
    border-radius: 8px;
    border: 1px solid alpha(@hermod_fg, 0.10);
}
.command-palette {
    background-color: transparent;
}
.command-palette-shell {
    background-color: @hermod_bg_elevated;
    border: 1px solid alpha(@hermod_fg, 0.12);
    border-radius: 14px;
    box-shadow: 0 24px 56px rgba(0, 0, 0, 0.45);
    padding: 0;
}
.command-palette-header {
    padding: 14px 16px;
    border-bottom: 1px solid alpha(@hermod_fg, 0.08);
}
.command-palette-icon {
    color: alpha(@hermod_fg_muted, 0.72);
}
.command-palette-entry {
    background-color: transparent;
    border: none;
    box-shadow: none;
    color: @hermod_fg;
    font-size: 1.05em;
    padding: 2px 0;
    min-height: 26px;
}
.command-palette-entry:focus,
.command-palette-entry:focus-within {
    background-color: transparent;
    outline: none;
    box-shadow: none;
}
.command-palette-chips {
    margin: 0 4px;
}
.command-palette-chip {
    background-color: alpha(@hermod_fg, 0.08);
    color: alpha(@hermod_fg, 0.75);
    border-radius: 999px;
    padding: 2px 9px;
    font-size: 0.70em;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.command-palette-chip-local {
    background-color: rgba(46, 106, 112, 0.22);
    color: #9ED7DC;
}
.command-palette-kbd {
    background-color: alpha(@hermod_fg, 0.08);
    color: alpha(@hermod_fg_muted, 0.85);
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 0.70em;
    font-family: monospace;
}
.command-palette-list {
    background-color: transparent;
    padding: 6px 6px 8px;
}
.command-palette-list > row {
    background-color: transparent;
    border-radius: 8px;
    padding: 8px 12px;
    margin: 1px 4px;
}
.command-palette-list > row:selected,
.command-palette-list > row:hover {
    background-color: alpha(@hermod_fg, 0.08);
}
.command-palette-row-title {
    font-size: 0.92em;
    font-weight: 700;
    color: @hermod_fg;
}
.command-palette-row-subtitle {
    font-size: 0.78em;
    color: alpha(@hermod_fg_muted, 0.72);
}
.command-palette-footer {
    padding: 10px 16px 12px;
    border-top: 1px solid alpha(@hermod_fg, 0.08);
    font-size: 0.74em;
    color: alpha(@hermod_fg_muted, 0.72);
}
"""

ACCENT_PALETTE = {
    "teal":   {"base": "#2E6A70", "weak": "rgba(46,106,112,0.16)"},
    "forest": {"base": "#3B6B4E", "weak": "rgba(59,107,78,0.16)"},
    "gold":   {"base": "#B08A3E", "weak": "rgba(176,138,62,0.16)"},
    "stone":  {"base": "#6F7B82", "weak": "rgba(111,123,130,0.16)"},
}

DAY_PALETTES = {
    "paper": {
        "bg":       "#F2F1ED",
        "elevated": "#FAF9F5",
        "hover":    "#EDECE6",
        "card":     "#FFFFFF",
        "sunken":   "#E8E7E1",
        "reader":   "#FDFCF8",
        "fg":       "#14181B",
        "fg_muted": "#5A636A",
    },
    "mist": {
        "bg":       "#E9EEF1",
        "elevated": "#F2F6F8",
        "hover":    "#E1E7EB",
        "card":     "#FFFFFF",
        "sunken":   "#D9E0E5",
        "reader":   "#F8FBFD",
        "fg":       "#0E1418",
        "fg_muted": "#506068",
    },
    "linen": {
        "bg":       "#EFEAE0",
        "elevated": "#F6F2E9",
        "hover":    "#E7E1D4",
        "card":     "#FFFFFF",
        "sunken":   "#DFD9CB",
        "reader":   "#FBF8F1",
        "fg":       "#1A1714",
        "fg_muted": "#635A4E",
    },
}

DENSITY_ROW_HEIGHT = {
    "comfortable": 72,
    "balanced":    54,
    "compact":     42,
}


def build_theme_override_css(theme="night", day_variant="paper", accent="teal", density="balanced"):
    accent_info = ACCENT_PALETTE.get(accent, ACCENT_PALETTE["teal"])
    base = accent_info["base"]
    weak = accent_info["weak"]

    parts = []
    parts.append(f"@define-color hermod_accent {base};")
    parts.append(f"@define-color hermod_accent_weak {weak};")
    parts.append(f"@define-color accent_color {base};")
    parts.append(f"@define-color accent_bg_color {base};")

    if theme == "day":
        day = DAY_PALETTES.get(day_variant, DAY_PALETTES["paper"])
        bg      = day["bg"]
        elev    = day["elevated"]
        hover   = day["hover"]
        card    = day["card"]
        sunken  = day["sunken"]
        reader  = day["reader"]
        fg      = day["fg"]
        muted   = day["fg_muted"]

        parts.append(f"@define-color hermod_bg {bg};")
        parts.append(f"@define-color hermod_bg_elevated {elev};")
        parts.append(f"@define-color hermod_bg_hover {hover};")
        parts.append(f"@define-color hermod_surface_card {card};")
        parts.append(f"@define-color hermod_surface_sunken {sunken};")
        parts.append(f"@define-color hermod_surface_reader {reader};")
        parts.append(f"@define-color hermod_fg {fg};")
        parts.append(f"@define-color hermod_fg_muted {muted};")
        parts.append(f"@define-color hermod_accent_fg #FFFFFF;")
        parts.append(f"@define-color accent_fg_color #FFFFFF;")

        # Explicit class overrides — the base CSS uses hardcoded night hex values,
        # so we override every affected selector here for day mode.
        parts.append(f"""
/* ── Day-mode class overrides ── */
.message-list-view row:hover .email-row:not(.selected) {{
    background-color: alpha({fg}, 0.04);
}}
.thread-indicator {{
    background-color: alpha({fg}, 0.06);
}}
.thread-indicator image {{
    color: alpha({muted}, 0.74);
}}
.thread-badge {{
    color: alpha({muted}, 0.80);
}}
.all-inboxes-row {{
    background-color: alpha({fg}, 0.04);
}}
.all-inboxes-row .folder-count {{
    color: alpha({muted}, 0.74);
}}
.load-more-row button {{
    color: alpha({muted}, 0.78);
    background-color: alpha({card}, 0.88);
    border: 1px solid alpha({fg}, 0.10);
}}
.load-more-row button:hover {{
    background-color: {hover};
    color: {fg};
    border-color: alpha({fg}, 0.18);
}}
.startup-status-card {{
    background-color: {card};
    border: 1px solid alpha({fg}, 0.08);
    box-shadow: 0 18px 34px alpha({fg}, 0.06);
}}
.startup-status-hero {{
    border-bottom: 1px solid alpha({fg}, 0.08);
}}
.startup-status-progress trough {{
    background-color: alpha({fg}, 0.08);
}}
.startup-status-summary {{
    color: {fg};
    background-color: alpha({card}, 0.92);
    border: 1px solid alpha({fg}, 0.08);
}}
.startup-status-issue-row {{
    background-color: {card};
    border: 1px solid alpha({fg}, 0.08);
}}
.startup-status-issues-title {{
    color: alpha({muted}, 0.68);
}}
.sorting-toggle {{
    color: alpha({muted}, 0.80);
    background-color: alpha({card}, 0.90);
    border: 1px solid alpha({fg}, 0.06);
}}
.sorting-toggle:hover {{
    background-color: {hover};
    color: {fg};
}}
.sorting-toggle.active {{
    background-color: {hover};
    color: {fg};
    border-color: alpha({base}, 0.26);
}}
.load-older-toolbar {{
    color: alpha({muted}, 0.82);
    background-color: alpha({card}, 0.88);
    border: 1px solid alpha({fg}, 0.10);
}}
.load-older-toolbar:hover {{
    background-color: {hover};
    color: {fg};
    border-color: alpha({fg}, 0.18);
}}
.sidebar-actions {{
    border-bottom: 1px solid alpha({fg}, 0.10);
}}
.sync-control {{
    border: 1px solid alpha({fg}, 0.10);
    background-color: alpha({card}, 0.88);
}}
.sync-divider {{
    background-color: alpha({fg}, 0.28);
}}
.sync-control.sync-online .sync-divider,
.sync-control.sync-offline .sync-divider {{
    background-color: alpha({fg}, 0.18);
}}
.attachment-bar {{
    border-top: 1px solid alpha({fg}, 0.08);
    background-color: alpha({card}, 0.95);
}}
.thread-reply-bar {{
    border-top: 1px solid alpha({fg}, 0.08);
    background-color: alpha({elev}, 0.96);
}}
.thread-reply-editor {{
    background-color: {card};
    border: 1px solid alpha({fg}, 0.10);
}}
.message-info-bar {{
    border-bottom: 1px solid alpha({fg}, 0.08);
    background-color: {reader};
}}
.message-info-subject {{
    color: {fg};
}}
.message-info-sender-line {{
    color: alpha({muted}, 0.82);
}}
.message-info-sender {{
    color: alpha({muted}, 0.84);
}}
.message-info-date {{
    color: alpha({muted}, 0.80);
}}
.message-info-meta {{
    color: alpha({muted}, 0.72);
}}
.reading-pane-shell {{
    background-color: {reader};
}}
.thread-sidebar {{
    border-left: 1px solid alpha({fg}, 0.08);
    background-color: {bg};
}}
.thread-sidebar-row:hover {{
    background-color: alpha({fg}, 0.04);
}}
.thread-sidebar-avatar.generic {{
    background-color: alpha({fg}, 0.10);
    color: alpha({fg}, 0.84);
}}
.thread-sidebar-snippet {{
    color: alpha({muted}, 0.72);
}}
.thread-sidebar-time {{
    color: alpha({muted}, 0.72);
}}
.content-split separator {{
    background-image: linear-gradient(
        to right,
        transparent 0,
        transparent 3px,
        alpha({fg}, 0.10) 3px,
        alpha({fg}, 0.10) 4px,
        transparent 4px,
        transparent 100%
    );
}}
.message-column {{
    background-color: {card};
    border: 1px solid alpha({fg}, 0.08);
}}
.attachment-chip {{
    border: 1px solid alpha({fg}, 0.10);
}}
.thread-info-button {{
    background-color: alpha({card}, 0.90);
    border: 1px solid alpha({fg}, 0.10);
    color: alpha({fg}, 0.82);
}}
.thread-info-button.active {{
    background-color: alpha({base}, 0.12);
    border-color: alpha({base}, 0.22);
}}
/* Settings panel */
.settings-section-title {{
    color: alpha({muted}, 0.82);
}}
.account-tile {{
    border: 1px solid alpha({fg}, 0.08);
    background: linear-gradient(180deg, alpha({fg}, 0.02), alpha({fg}, 0.01)),
                alpha({card}, 0.92);
}}
.account-tile:hover {{
    background: linear-gradient(180deg, alpha({fg}, 0.04), alpha({fg}, 0.02)),
                {hover};
}}
.account-tile-icon {{
    color: alpha({fg}, 0.88);
}}
.account-row-subtitle {{
    color: alpha({muted}, 0.70);
}}
.account-color-chip {{
    background: alpha({card}, 0.90);
    border: 1px solid alpha({fg}, 0.08);
}}
.account-color-preview {{
    border: 1px solid alpha({fg}, 0.10);
}}
.account-editor-header {{
    color: {fg};
}}
.appearance-segment {{
    background: alpha({card}, 0.85);
    border: 1px solid alpha({fg}, 0.08);
}}
.appearance-segment > button {{
    color: alpha({fg}, 0.66);
}}
.appearance-segment > button.selected {{
    background: {base};
    color: #ffffff;
}}
.appearance-segment > button:hover:not(.selected) {{
    background: alpha({fg}, 0.06);
    color: {fg};
}}
.appearance-swatch {{
    border: 1px solid alpha({fg}, 0.08);
}}
.appearance-swatch.selected {{
    border: 2px solid {fg};
}}
""")

    row_height = DENSITY_ROW_HEIGHT.get(density, DENSITY_ROW_HEIGHT["balanced"])
    parts.append(f".email-row {{ min-height: {row_height}px; }}")

    return "\n".join(parts) + "\n"


ACCOUNT_SAFE_PALETTE = [
    "#6f7f79",
    "#4f6b65",
    "#55735a",
    "#6b7f93",
    "#85745f",
    "#7c675f",
    "#586b62",
    "#6d7784",
]

ACCOUNT_PALETTE = ACCOUNT_SAFE_PALETTE


def account_class_for_index(idx):
    return f"account-accent-{idx % len(ACCOUNT_PALETTE)}"


def _hex_rgb_tuple(hex_color, fallback=(120, 120, 120)):
    value = str(hex_color or "").strip().lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def nearest_account_palette_index(hex_color, fallback_index=0):
    target = _hex_rgb_tuple(hex_color)
    best_index = fallback_index % len(ACCOUNT_PALETTE)
    best_distance = None
    for index, color in enumerate(ACCOUNT_PALETTE):
        candidate = _hex_rgb_tuple(color)
        distance = sum((target[i] - candidate[i]) ** 2 for i in range(3))
        if best_distance is None or distance < best_distance:
            best_index = index
            best_distance = distance
    return best_index


def account_class_for_color(hex_color, fallback_index=0):
    return account_class_for_index(
        nearest_account_palette_index(hex_color, fallback_index)
    )


def apply_accent_css_class(
    widget,
    hex_color="",
    fallback_index=0,
    *,
    attr_name="_hermod_accent_css_class",
):
    css_class = account_class_for_color(hex_color, fallback_index)
    current = getattr(widget, attr_name, "")
    if current and current != css_class:
        try:
            widget.remove_css_class(current)
        except Exception:
            pass
    if css_class and current != css_class:
        try:
            widget.add_css_class(css_class)
        except Exception:
            pass
    setattr(widget, attr_name, css_class)
    return css_class


def _hex_to_rgba(hex_color, alpha=1.0):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return f"rgba(120,120,120,{alpha})"
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def contrasting_foreground(hex_color, light="#ffffff", dark="#111111"):
    hex_color = str(hex_color or "").strip().lstrip("#")
    if len(hex_color) != 6:
        return dark
    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
    except Exception:
        return dark

    def _linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)
    return dark if luminance > 0.46 else light


def build_compose_account_css(backends=None):
    parts = []
    parts.append(
        """
.compose-account-popover {
    border-radius: 10px;
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
    return "".join(parts)


def build_window_account_css(backends=None):
    parts = []
    for i, color in enumerate(ACCOUNT_PALETTE):
        cls = account_class_for_index(i)
        glow = _hex_to_rgba(color, 0.14)
        connector = "rgba(166, 173, 179, 0.14)"
        parts.append(
            f"""
.email-row.{cls} {{
    background-image: linear-gradient(to left, {glow}, rgba(0,0,0,0));
}}
.navigation-sidebar row.{cls}.folder-row .account-accent-label {{
    color: @hermod_fg_dim;
}}
.navigation-sidebar row.{cls}.folder-row:hover .account-accent-label {{
    color: @hermod_fg_muted;
}}
.navigation-sidebar row.{cls}.folder-row:selected .account-accent-label,
.navigation-sidebar row.{cls}.folder-row.selected .account-accent-label {{
    color: @hermod_fg;
}}
.navigation-sidebar row.{cls}.account-section-header {{
    background-color: transparent;
    border-radius: 6px;
}}
.navigation-sidebar row.{cls}.account-section-header .account-accent-label {{
    color: @hermod_fg_muted;
    font-weight: 500;
}}
.navigation-sidebar row.{cls}.account-section-header .folder-count {{
    color: @hermod_fg_faint;
}}
.navigation-sidebar row.{cls}.account-section-header .account-health-icon {{
    color: alpha(@hermod_fg_muted, 0.88);
}}
.navigation-sidebar row.{cls}.account-section-header .folder-count-dim {{
    color: @hermod_fg_dim;
}}
.navigation-sidebar row.{cls} .folder-connector {{
    box-shadow: inset 1px 0 0 0 {connector};
    background-image: none;
}}
.navigation-sidebar row.{cls} .folder-connector-last {{
    box-shadow: none;
    background-image: linear-gradient(to bottom, {connector} 50%, transparent 50%);
    background-size: 1px 100%;
    background-repeat: no-repeat;
    background-position: 0 0;
}}
"""
        )
    parts.append(
        """
.navigation-sidebar row .folder-count.folder-count-dim {
    color: alpha(@window_fg_color, 0.42);
}
/* Selected state overrides per-account gradient — must come last */
.message-list-view .email-row.selected {
    background-image: none;
    background-color: alpha(@accent_color, 0.08);
    box-shadow: inset 3px 0 0 0 @accent_color;
}
.message-list-view row:hover .email-row.selected {
    background-color: alpha(@accent_color, 0.12);
}
"""
    )
    return "".join(parts)


def _build_shared_account_accent_css():
    parts = []
    for i, color in enumerate(ACCOUNT_PALETTE):
        cls = account_class_for_index(i)
        fg = contrasting_foreground(color)
        muted = _hex_to_rgba(color, 0.74)
        strong = _hex_to_rgba(color, 0.92)
        parts.append(
            f"""
.account-color-preview.{cls},
.account-color-chip.{cls},
.onboarding-account-row.{cls} .onboarding-account-accent,
.thread-sidebar-row.{cls} .thread-sidebar-strip {{
    background-color: {strong};
}}
.account-color-chip.{cls},
.thread-sidebar-row.{cls} .thread-sidebar-avatar {{
    color: {fg};
}}
.thread-sidebar-row.{cls} .thread-sidebar-avatar {{
    background-color: {strong};
}}
.onboarding-account-row.{cls} .onboarding-account-title,
.thread-sidebar-row.{cls} .thread-sidebar-sender,
.message-info-sender.{cls} {{
    color: {strong};
}}
.onboarding-account-row.{cls} .onboarding-account-subtitle {{
    color: {muted};
}}
.account-color-chip.{cls} label {{
    color: {fg};
}}
"""
        )
    return "".join(parts)


CSS += _build_shared_account_accent_css()
