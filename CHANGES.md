# Hermod — Fixed Behaviours (do not revert)

## Forward auto-includes original attachments (`compose.py`)

**Opening a Forward compose now auto-attaches the original message's attachments in the background; users no longer need to re-save + re-attach files manually.**
- `ComposeView.__init__` calls `_load_forward_attachments(forward_from)` after `_prefill_forward_body`; for each `att` in `msg.get('attachments')` it spawns a daemon thread that calls `backend.fetch_attachment_data(uid, att, folder)` (falling back to `backend.fetch_body(uid, folder)` to recover bytes for providers that don't expose the direct path)
- `_append_forward_attachment(att, data)` runs on the GLib main loop (via `GLib.idle_add`) so it safely appends to `self._attachments` and calls the existing `_add_attachment_chip(name, index)` to render the chip in the compose toolbar — chips appear progressively as each attachment finishes downloading
- Attachment size is read from `att['size']` when present; otherwise falls back to `len(data)` so the send path's 25 MB guard still has a real number
- The backend lookup prefers `msg['backend_obj']` (the object attached during message load) and falls back to `self.backend` so multi-account composes keep pulling bytes from the right provider
- **Why:** sub-phase D forward wiring landed with the caveat that attachments weren't auto-included — doing this last closes the loop on the reader Forward button so the compose matches the source message without user effort

## Welcome "Accounts added" row polish (`window_welcome.py`, `styles.py`)

**The post-setup "Accounts added" list now renders as proper settings-style cards — card background, border, padded layout, and a trailing health dot — matching the `settings-account-row` pattern in the design prototype.**
- `.onboarding-account-row` gained `padding: 12px 14px`, `border-radius: 12px`, and a subtle `alpha(#f5f0e6, 0.04)` fill with a 1px border; each row now reads as a distinct card rather than a sparse inline row
- `.onboarding-account-title` bumped to 0.92em / 600 and the subtitle to 0.80em with a 2px top margin for a legible two-line stack
- `refresh_accounts` composes the row as `[provider-logo] [accent-strip] [label-stack (hexpand)] [health-dot]`; the label column now sets `hexpand=True` so the trailing health dot is pinned to the right edge
- New `.onboarding-account-health` CSS token (8px green dot with a soft glow) matches `settings-account-row .health-dot` in the design prototype; tooltip reads "Connected"
- **Why:** sub-phase B polish left the welcome-screen account list rendering as a bullet + accent strip with no surface treatment; the design prototype presents each connected account as a card-style row with a health indicator, so bringing the welcome list to the same pattern keeps the two "where is my account listed" surfaces visually aligned

## Count badges on flat MAILBOXES rows (`window.py`, `window_mailbox_controller.py`, `window_message_list.py`, `providers/gmail.py`, `providers/imap_smtp.py`)

**The unified Flagged / Drafts / Sent / Archive rows have count-badge plumbing wired end-to-end; Drafts/Sent counts are now sourced from providers, Flagged/Archive remain 0 until their own counters land.**
- `_unread_counts` default dict grew `drafts / sent / archive / flagged` alongside `inbox / trash / spam` (in `window.py` and the defensive fallback in `_on_sync`)
- `_populate_sidebar` stores the new unified rows on the window (`self._flagged_row / _drafts_row / _sent_row / _archive_row`) so the controller can address them directly
- `_count_bucket_for_folder` now maps `drafts / sent mail / sent / sentitems / archive / all mail` to the new buckets; `_background_result_affects_current_view` gained parallel branches for each unified folder so sync results refresh the visible list
- `update_account_counts(…, drafts_count=, sent_count=, archive_count=, flagged_count=)` and `_render_unread_counts` aggregate across accounts and call `set_count` on each unified row (Drafts/Sent/Archive rendered with `dim=True` to de-emphasize non-inbox counts)
- Provider `sync_folders` results include `drafts` and `sent` counts via `get_unread_count('[Gmail]/Drafts' / '[Gmail]/Sent Mail')` (Gmail) and `get_unread_count('Drafts' / 'Sent')` (IMAP); Archive and Flagged have no equivalent provider counter yet, so those buckets stay 0 and render cleanly without a badge
- **Why:** sub-phase B polish left the flat rows rendering correctly but always empty; hooking up the shared `_unread_counts` machinery lets provider sync drive the badges the same way Inbox/Trash/Spam already do, with no new data paths

## Flat MAILBOXES rows (`utils.py`, `window_message_list.py`, `window_mailbox_controller.py`, `window_message_cache.py`)

**MAILBOXES sidebar section now lists All Inboxes, Flagged, Drafts, Sent, Archive, Trash as a flat row set matching the design prototype.**
- New folder sentinels in `utils.py`: `_UNIFIED_FLAGGED`, `_UNIFIED_DRAFTS`, `_UNIFIED_SENT`, `_UNIFIED_ARCHIVE` (alongside the existing `_UNIFIED`, `_UNIFIED_TRASH`, `_UNIFIED_SPAM`)
- `_populate_sidebar` unconditionally appends the four new `UnifiedRow`s (Flagged ★ / Drafts ✎ / Sent ↑ / Archive 📁) between All Inboxes and the settings-gated Trash/Spam rows; the Trash label dropped the `All` prefix to match the design
- `_commit_folder_selection` routes each new folder ID through `_load_unified_folder(…)` and flips `set_filter_mode(…)` so clicking Flagged loads the unified inbox with the flagged filter active (same model as the segmented chip), and the others load the backend folder by name
- `_on_sync` and `_refresh_current_message_list` in `window_mailbox_controller.py` gained parallel branches so a sync or offline refresh stays in whichever unified folder the user was viewing
- `_current_view_uses_backend` and `_message_list_context_key` (in `window_message_cache.py`) extended to treat the new sentinels as unified scopes so per-backend refresh keys stay in sync
- Count badges wired in a follow-up entry (see top of file) — Drafts/Sent badges light from provider sync; Flagged/Archive remain 0 pending their own counters
- **Why:** Phase 0 sub-phase B left the sidebar with a three-row unified trio (All Inboxes / All Trash / All Spam) and parked the flat folder set as a follow-up; the design prototype ships with six always-visible mailbox rows under MAILBOXES, and routing clicks through the existing `_load_unified_folder` path keeps backend churn minimal

## Reader Forward wiring (`compose.py`, `window.py`, `window_message_list.py`)

**Forward is a real compose mode now; the reader Forward icon and the `f` shortcut both open a pre-filled compose.**
- `compose.ComposeView.__init__` gains a `forward_from=` kwarg (mutex with `reply_to`); when set the title becomes `Forward`, the subject is prefixed with `Fwd:` (unless already `Fwd:` / `Fw:`), and `_prefill_forward_body` inserts a standard `---------- Forwarded message ----------` block with `From: / Date: / Subject:` header lines plus a `>`-quoted copy of the original `body_text` / `snippet`
- `window.py` `_reader_forward_btn` is no longer `set_sensitive(False)` — it connects to `_on_current_forward`, which routes through `MessageListMixin._on_forward` (new) and opens `ComposeView(forward_from=msg, ...)`
- Keyboard shortcut `f` in the message list dispatches to `_on_forward` for the selected row
- Attachment auto-include landed as a follow-up (see top of file): `_load_forward_attachments` fetches each original attachment in a background thread and `_append_forward_attachment` marshals the chip append back onto the main loop
- **Why:** Phase 0 sub-phase D left Forward rendered but disabled as a follow-up; wiring it closes the reader action cluster so every icon in the design prototype maps to a live path

## ACCOUNTS row layout (`widgets.py`, `window_message_list.py`, `styles.py`)

**The account row in the sidebar leads with a status dot, ends with a dropdown chevron, and drops the aggressive mono/uppercase styling that was truncating account names.**
- `widgets.AccountHeaderRow` now composes as `[status-dot] [label] [health-icon] [count-badge] [chevron]` instead of the previous `[chevron-start] [label] [health] [count]` — the leading chevron moved to the end as a dropdown affordance (`pan-down-symbolic` collapsed / `pan-up-symbolic` expanded), and a new `.account-status-dot` takes its place on the start edge, tinted with `@accent_bg_color`
- `window_message_list._toggle_account` flips the new chevron icons accordingly
- `.account-header` in `styles.py` is no longer mono + 0.14em letter-spaced + `text-transform: uppercase`; it reads at `0.92em / 500 / normal case / @window_fg_color alpha 0.82` so the presentation name fits without truncating to `GMAI…`
- New CSS tokens: `.account-status-dot`, `.account-header-chevron` (with hover state)
- **Why:** the sub-phase B pass inherited an over-styled `.account-header` from the sidebar-section tokens, which made every account label render as heavy uppercase monospace; the design prototype renders the account row in regular sans-serif with a colored status dot and a subtle trailing chevron

## Mailbox fetcher error handling (`window_mailbox_controller.py`)

**The background mail fetcher no longer crashes when a provider surfaces an OAuth / sign-in error.**
- `MailboxControllerMixin._set_error(message, generation=None)` is a real method now; it was called from the fetcher path but never defined, so every OAuth token failure raised `AttributeError('_set_error')` and left the UI wedged in `Starting mail`
- The new implementation logs via `_log_exception`, clears the loading flag via `_set_message_loading(False, generation)`, and surfaces the error to the user through `_show_toast(text)` (truncated to 160 characters) when the toast channel is available — each step is defensively guarded so a secondary failure never masks the primary error
- **Why:** this was blocking visual verification of the Phase 0 reader sub-phase (the viewer pane never rendered because the fetcher crashed before body load) and would have produced a broken first-run for any real user whose provider token expired

## Main-window reader header alignment (`window.py`, `window_reader.py`, `styles.py`)

**The reader header now leads with a display headline and compact action cluster, matching the prototype.**
- The `_message_info_bar` row stack has been redesigned: the top row carries a large `.reader-subject` headline (wrap, up to 2 lines, ellipsized) on the start side and a `.reader-actions` cluster on the end with reply / reply-all / forward / delete icon buttons, followed by the legacy Original + thread-toggle buttons in `_message_info_actions`
- A new `_reader_meta_lbl` label sits beneath the top row and shows `sender · received-date` for single messages or `N messages · participants` for threads — `_update_message_info_bar` and `_render_thread_view` (both in `window_reader.py`) populate it
- The retired `Received: …` and `Size · N attachments` lines stay in the widget tree as hidden fallbacks so any external code or tests that set their labels keep working
- Forward is rendered but marked insensitive with a `Forward (coming soon)` tooltip — wiring it requires extending `compose.ComposeView` with a forward mode, which is tracked as a follow-up
- New CSS tokens in `styles.py`: `.reader-header`, `.reader-subject`, `.reader-meta`, `.reader-actions`, `.reader-action-btn`, and a bumped `.message-info-subject` size so the legacy class matches the redesigned headline
- **Why:** Phase 0 sub-phase D of the main-window design alignment — the prototype leans on a large subject + `N messages · participants` summary with top-right actions, and the pre-design `Received:` / `Size · N attachments` stack no longer earned its vertical real estate

## Main-window message list alignment (`window.py`, `widgets.py`, `window_message_list.py`, `utils.py`, `styles.py`)

**The message column now opens with an eyebrow + meta + segmented filter strip and day-grouped rows, matching the inbox prototype.**
- A new `_message_column_header` Box sits above the search entry with a mono `_message_col_eyebrow` label (folder crumb uppercased, defaults to `ALL INBOXES`), a `_message_col_meta` line (`N messages · N unread`), and a `Gtk.ToggleButton` radio-group segmented filter with `unified` / `unread` / `flagged` chips — the selected chip carries `.message-filter-chip.selected`
- Filter selection routes through `MessageListMixin.set_filter_mode()` which updates a new `_filter_mode` state, keeps the legacy `_show_unread_only` toggle in sync so existing tests / key-bindings keep working, and invalidates `_message_filter` so the filtered model re-evaluates; the old visible `sorting-toolbar` row inside the search bar (Load older, sort, unread toggle) has been retired from the layout (the stub widgets remain invisible so existing callers do not break)
- Day grouping is produced at build time: `_build_message_items` walks the sorted message list and injects `DayGroupListItem` headers whenever the local date changes, back-referencing the following `MessageListItem`s via a `followers` list; `_email_filter` returns `False` for group headers whose followers are all filtered out so the list never shows orphan eyebrows, and `_move_selection` skips past day-group rows during keyboard navigation
- `utils._day_group_label(dt)` returns short uppercase labels — `TODAY`, `YESTERDAY`, `MON 17 APR`, `3 MAR 2025` — and `utils._day_group_key(dt)` produces the stable grouping key
- New `widgets.DayGroupListItem` (non-selectable marker) + `widgets.DayGroupRow` (mono `.day-group-row` + `.day-group-label`) cooperate with `list_item.set_selectable(False)` / `set_activatable(False)` in the factory
- New CSS tokens in `styles.py`: `.message-column-header`, `.message-column-eyebrow`, `.message-column-meta`, `.message-filter-segmented`, `.message-filter-chip`, `.day-group-row`, `.day-group-label`
- **Why:** Phase 0 sub-phase C of the main-window design alignment — the prototype shows a clean column header with segmented filter and day-grouped messages instead of a sort/unread icon strip stacked under the search bar

## Main-window sidebar alignment (`window.py`, `widgets.py`, `window_message_list.py`, `styles.py`)

**The inbox sidebar now follows the design: full-width Compose with `Ctrl N` chip, then MAILBOXES and ACCOUNTS eyebrow sections.**
- Compose is the only control in the top action strip; the standalone `ONLINE` sync pill has been retired (sync moved to the header under sub-phase A) and Compose carries a right-aligned `Ctrl N` label chip via a new `.sidebar-compose-chip` token
- `widgets.SidebarSectionRow` is a non-selectable, non-activatable `Gtk.ListBoxRow` that renders a mono-eyebrow label (`.sidebar-section-label`) used to group sidebar entries
- `window_message_list._populate_sidebar` inserts a `MAILBOXES` eyebrow before the unified `All Inboxes` / `All Trash` / `All Spam` rows and an `ACCOUNTS` eyebrow before the per-account header + folders + more-row block; the legacy trailing trash/spam block has been removed so rows are never duplicated
- New CSS tokens in `styles.py`: `.sidebar-compose-chip`, `.sidebar-section`, `.sidebar-section-label`
- **Why:** Phase 0 sub-phase B of the main-window design alignment — the previous sidebar mixed a small `ONLINE` pill next to Compose and listed folders flat under a single header, while the prototype groups unified and per-account mailboxes under distinct eyebrow sections

## Main-window header strip (`window.py`, `styles.py`)

**The main window header matches the design: brand + crumb left, controls right, no centered title.**
- New `_HeaderTitleStrip` widget (in `window.py`) carries `H` icon + `HERMOD` wordmark + vertical separator + folder crumb; it is left-packed via `Adw.HeaderBar.pack_start` so the text hugs the start edge instead of centering
- Existing `self.title_widget.set_title()` / `set_subtitle()` call sites in `window_message_list.py` keep working — the strip exposes the same two setters, the title becomes the crumb, and the subtitle renders as a smaller muted suffix
- Settings gear (`emblem-system-symbolic`) and a small refresh icon are right-packed via `pack_end` so the visual order is `[sync] [settings] [CSD]`
- New CSS tokens in `styles.py`: `.hermod-header`, `.hermod-header-brand-row`, `.hermod-header-mark`, `.hermod-header-brand-label`, `.hermod-header-separator`, `.hermod-header-crumb-title`, `.hermod-header-crumb-subtitle`, `.hermod-header-sync`, `.hermod-header-settings`
- **Why:** Phase 0 sub-phase A of the main-window design alignment — the centered `Hermod` title in the previous `Adw.WindowTitle` did not match the prototype, which always shows the wordmark + crumb on the left and the chrome actions on the right

## Welcome photo panel art (`assets/welcome-photo.png`, `tools/generate_welcome_photo.py`, `window_welcome.py`)

**The welcome left column now carries forest/aurora artwork instead of a flat placeholder.**
- `tools/generate_welcome_photo.py` renders `assets/welcome-photo.png` at 1440×1800 — a 3-stop vertical gradient (#0F1A18 → #0B1512 → #08100E) with two radial aurora glows (teal #2E6A70 @ 0.22, forest #3B6B4E @ 0.14) and a per-pixel blue-noise dither to kill banding on large GTK surfaces
- The photo column in `window_welcome.py` is now a `Gtk.Overlay` with a `Gtk.Picture` (CONTENT_FIT_COVER) as the base and the caption riding on top as an overlay child; the window-move gesture is attached to the overlay so drag-to-move works anywhere on the panel
- `HERMOD_FORCE_WELCOME=1` is now honoured by `window.py`, so `--dump-ui` can capture the welcome surface even when real accounts are configured
- **Why:** the Phase 0 design pass promised real artwork instead of a flat placeholder panel, and the generator keeps the asset regeneratable without storing a large binary blob in memory or requiring external tooling

## Account settings and startup refresh (`settings.py`, `window.py`, `accounts/*`)

**Hermod now has a first-class account management section in Settings and can refresh account chrome live.**
- The top of Settings now starts with an Add Account area with service tiles for Gmail and manual IMAP/SMTP setup
- Existing accounts are listed underneath with settings and trash actions
- Alias and accent color are stored locally and flow through the sidebar, compose sender picker, and account chrome
- Manual IMAP/SMTP accounts are persisted locally with secure keyring password storage
- Account add/remove/save now triggers a backend reload so the live UI updates without a full app restart
- **Why:** the app should know which accounts exist, let you manage them in one place, and reflect account presentation changes immediately

## Email reading pane (window.py `ReaderMixin`)

**Email content uses an adaptive but conservative surface hint.**
- Obvious near-white or near-black HTML surfaces are adjusted so the reader remains readable
- The reader still leaves ambiguous or intentionally designed mail alone
- The app theme still controls the outer chrome; only the message surface adapts when the HTML clearly needs help
- **Why:** many HTML emails assume either a white or dark surface and become unreadable when the opposite contrast is used

**The reader webview no longer executes page JavaScript for message HTML.**
- JavaScript is disabled on the mail reading surface
- Per-message "Original" actions now use a native Hermod URI handled by WebKit policy interception
- The original-message dialog still opens for the selected bubble without exposing a script bridge to message HTML
- **Why:** arbitrary email HTML should not get a script execution path just to open Hermod UI

**Thread bodies open in two stages instead of waiting for every message body serially.**
- The selected message body renders first
- The rest of the thread bodies fetch in bounded parallel and then fill in
- **Why:** the reader should show something useful quickly on large or slow threads instead of sitting on a long serial fetch loop

**Startup unread counts now stay hidden until the boot screen closes.**
- The startup status screen gets a short graceful close before the left-column counts appear
- Counts are collected during boot, then rendered once the startup screen is dismissed
- **Why:** cold start should feel deliberate, and the unread numbers should not pop in half-way through boot

**Startup warnings now stay honest about fallback paths and provider errors.**
- The boot card can show warning or error states per account instead of only a generic "ready" message
- Gmail API failures that fall back to IMAP now surface as a warning instead of pretending everything is normal
- Startup unread counts no longer flash a cached estimate before provider reconciliation finishes
- **Why:** if Hermod is using a fallback or if a provider is unhealthy, the user should see that immediately

**Unread-only mode now lives beside sort controls.**
- A separate unread toggle shows only unread messages in the current view
- Empty unread views use friendly copy like "No unread messages" and "All caught up"
- **Why:** unread filtering should be quick to scan without turning the toolbar into a control panel

**Email links now open through the native WebKit policy hook.**
- Hermod custom `hermod://original` links still open the original-message dialog
- Normal links are launched through the desktop default handler
- **Why:** email content should be clickable without giving arbitrary HTML a JavaScript bridge

## Gmail folder names (`providers/gmail.py`)

**Gmail is API-first in the live path, with IMAP handling left as legacy compatibility.**
- Folder browsing, thread/body fetches, flag changes, deletes, and unread counts now prefer the Gmail API
- Labels API drives folder discovery and localized standard folder names when possible
- IMAP helper code still exists for compatibility and edge-case fallback, but the common path no longer depends on it
- **Why:** the Gmail backend is now intended to run without IMAP in normal use, which keeps startup and message reads noticeably faster

## Native accounts and startup reconciliation (`settings.py`, `accounts/*`, `backends.py`)

**Hermod now supports native Gmail and manual IMAP/SMTP accounts and reconciles account state on startup.**
- The Settings page has a top-level Add Account section with tiles for Gmail and manual IMAP/SMTP
- Native IMAP/SMTP accounts are stored locally with keyring-backed passwords
- Alias, accent color, and enabled/hidden state are stored separately from credentials
- Startup prunes stale sync state and stale local account prefs when local account inventory changes
- Provider instances are created lazily so unopened accounts do not cost startup time
- **Why:** the app should know which accounts exist, keep their UI metadata consistent, and avoid paying provider setup cost for accounts the user never touches

## Body cache (window.py)

**Bodies are cached in `self._body_cache` (OrderedDict, max 8 entries) and a budgeted disk cache.**
- RAM cache key: `(backend.identity, folder, uid)`
- Disk cache key: hash of `backend.identity`, `folder`, and `uid`
- `_load_body`: checks RAM cache first, then disk cache, then backend
- `_set_body`: stores result to RAM cache and mirrors the body into the disk cache
- Disk cache persists body text plus image attachment bytes; non-image attachments
  stay on-demand so PDFs and similar files are still downloaded when needed
- Disk cache budget is configurable in Preferences and is clamped by free space
  plus a hard ceiling
- Fresh installs default the disk cache budget to 64 MB
- Sync interval and disk cache budget are staged in Preferences and only commit
  when the Save button is pressed
- **Tiny prefetch only** — a very small warm-up may run after the list is shown,
  but it is intentionally narrow and session-only to avoid lock contention and
  memory growth.
- The inbox-like warm-up now targets the newest messages first so the cache
  fills with the most recent mail instead of trash or spam
- Startup restores a cached inbox/unified-inbox message snapshot first, then
  refreshes live in the background; snapshots are skipped if the account set
  changed since they were written
- `Load images` applies live to the open viewer instead of waiting for a restart
- Settings saves are written atomically so background cache work cannot corrupt
  the settings file
- Disk cache pruning uses cache-file metadata instead of decompressing every
  entry on each pass, so pruning stays cheap

## IMAP folder quoting (backends.py `_imap_folder`)

**All folder names passed to `imap.select()` go through `_imap_folder()`.**
- Wraps names containing spaces in double quotes
- **Why:** `EXAMINE [Gmail]/Sent Mail` fails without quotes (space in name).

## Compose surface (compose.py + window.py)

**Compose lives in the reading pane, not a detached top-level window.**
- New compose, reply, and reply-all now render inside the reading-pane stack
- Leaving compose through folder/message/settings/window navigation prompts to
  keep editing or discard changes when the compose buffer is dirty
- Compose rich text supports bold, italic, quote, list, text color, and font size
- Manual draft saving is removed for now until there is a restore flow
- **Why:** compose should share the same main surface and navigation model as
  settings, while protecting unsaved compose state when the user clicks elsewhere.

## Sidebar width and badges (window.py)

**The left sidebar stays at the current minimum width and always reserves badge space.**
- Sidebar width is clamped to the current minimum width instead of being user-expandable
- Account/folder labels ellipsize against a fixed trailing badge slot
- Unread badges appearing or disappearing should no longer shift row text width
- The middle message list can still be dragged wider than before
- **Why:** the sidebar should feel stable and predictable instead of changing
  label width whenever counts change.

## Live verification path (`__main__.py`)

**Hermod can render its own GTK window to a PNG for Wayland verification.**
- `python3 hermod.py --dump-ui /tmp/hermod-dump-ui.png` waits for the live surface, then writes the rendered window to a PNG and quits
- `--dump-ui-delay-ms` and `--dump-ui-max-attempts` control the capture timing and retry budget
- The shared Wayland tooling includes a terminal preview helper for these captures
- **Why:** GNOME Wayland screenshot capture can be permission-sensitive, so the app-native dump path gives a reliable visual artifact for verification and debugging

## Cached folder loads (providers/gmail.py, providers/imap_smtp.py)

**Cached folder state now shows immediately when the incremental sync index is missing, and the backend bootstrap continues in the background.**
- Gmail returns the stored folder cache when history metadata is not yet available
- IMAP/SMTP returns provider cache before the live refresh path finishes
- Both providers still reconcile shortly after the fast path renders
- **Why:** first-paint mailbox loads should prefer already persisted provider cache over forcing a full remote scan before the UI can render

## Gmail API-first message ops (`providers/gmail.py`)

**Gmail thread fetches, body fetches, flag changes, deletes, and unread counts now prefer Gmail API when the backend can resolve the message safely.**
- Cached sync rows are used to resolve legacy IMAP UIDs to Gmail API message ids
- Gmail API thread reads, body reads, read/unread toggles, trash moves, and unread counts run before falling back to IMAP
- IMAP remains in place as a compatibility fallback for messages that are not yet present in local sync state
- **Why:** this trims the hottest Gmail read/write paths without forcing a big-bang migration away from the hybrid backend

## Gmail API message listing (`providers/gmail.py`)

**Gmail folder reads now prefer Gmail API list and metadata fetches before falling back to IMAP mailbox scans.**
- `messages.list` drives the folder view when the backend can resolve the label
- Per-message metadata fetches build the same row shape the UI already expects
- The fetched rows are persisted into the existing folder cache so later body and flag operations can resolve the same messages
- **Why:** this removes the last heavy IMAP list scan from the common Gmail folder open path while keeping the old path available as a fallback

## Gmail folder discovery (`providers/gmail.py`)

**The "more folders" drawer now prefers Gmail labels API before consulting IMAP folder listings.**
- Gmail user labels are returned directly from the labels API and formatted for the existing folder drawer
- IMAP `LIST` is now a fallback for the small number of cases where the labels API cannot be used
- **Why:** folder browsing no longer needs IMAP just to discover user labels on a healthy Gmail account

## Gmail special-folder seeding (`providers/gmail.py`)

**Gmail system folder mappings are now seeded from the labels API when labels are loaded.**
- The provider records localized actual folder names from the labels API for INBOX, Sent, Drafts, Trash, and Spam
- IMAP folder discovery is still available as a fallback, but the common API path no longer depends on it
- **Why:** API-first Gmail flows should not need an IMAP folder round-trip just to resolve the standard mailbox names

## Gmail cached refresh backfill (`providers/gmail.py`)

**Background refresh and top-up now stay on the Gmail API side before falling back to IMAP scans.**
- Cache refreshes reuse the cached row UID and Gmail message id directly instead of looking the UID up again through IMAP
- Cache top-up tries Gmail API listing first, then only falls back to IMAP if the API path is unavailable
- **Why:** the remaining IMAP usage in Gmail should be fallback-only, not part of the normal refresh path

## Gmail API-only live paths (`providers/gmail.py`)

**The normal Gmail browse and message action paths are now API-only.**
- folder listing uses Gmail labels API
- message list uses Gmail messages API
- thread fetch uses Gmail threads API
- body fetch, read/unread, delete, and unread counts use Gmail API
- IMAP helper code remains in the file as legacy support, but the live flow no longer depends on it
- **Why:** the Gmail backend now boots and runs without needing IMAP as part of the standard user-facing path

## Lazy backend construction (`accounts/registry.py`)

**Accounts are now wrapped in lazy provider proxies so the real backend is created only when the account is actually used.**
- Startup still sees the current account list and can render the sidebar immediately
- Provider instances are deferred until the UI or poll loop first calls into that account
- **Why:** Hermod should not pay provider construction cost for accounts that never get touched in a session

## Lazy polling (`__main__.py`)

**Unopened accounts are no longer polled just because they exist in the account registry.**
- The background poll loop skips lazy backends that have not been instantiated yet
- The currently active account still polls normally once the user uses it
- **Why:** startup and background work should stay focused on accounts the user is actually interacting with

## Sort toggle (`window.py`, `styles.py`)

**The message list sort control is now a single icon toggle.**
- The toolbar shows one clickable icon instead of two text buttons
- `Newest first` uses the descending sort icon
- `Oldest first` uses the ascending sort icon
- The tooltip follows the active order
- **Why:** the control is smaller, clearer, and easier to hit in the header bar

## Design tokens and appearance settings (`styles.py`, `settings.py`)

**Hermod now has a formal design-token layer and a live Appearance section in Settings.**
- `styles.py` exposes `ACCENT_PALETTE`, `DAY_PALETTES`, `DENSITY_ROW_HEIGHT`, and `build_theme_override_css(theme, day_variant, accent, density)` for dynamic theme application
- `settings.py` holds the new keys `theme_mode` (`night`/`day`), `day_variant` (`paper`/`mist`/`linen`), `accent` (`teal`/`forest`/`gold`/`stone`), `density` (`comfortable`/`balanced`/`compact`), and `ai_enabled`
- The Settings page shows an Appearance section with theme/day-variant segments, accent swatches, density segments, and an AI toggle
- Theme changes call `window.apply_theme()` live; the CSS provider is swapped in place
- **Why:** the design pass introduced a multi-axis palette the app has to switch between at runtime; without a token layer, theme changes would require restart and CSS would drift from the design file

## Welcome screen layout and chrome (`window_welcome.py`, `styles.py`, `window.py`)

**The welcome screen is a two-column surface that owns its own titlebar.**
- Left column: flat forest/aurora panel with bottom-left caption (placeholder until real art lands)
- Right column: scrollable content — H mark, HERMOD eyebrow, hero headline, summary, CONNECT AN ACCOUNT grid, "Show all 8 providers" link, zero-cloud lock pill, accounts list once at least one account exists
- `WelcomeScreen` is a `Gtk.Box` (vertical) that packs its own `Adw.HeaderBar` with an "H HERMOD" brand widget; the main window's `_header_bar` is hidden in welcome mode
- Provider tiles use horizontal `.provider-row-tile` rows with a colored letter glyph, name, and subtype text
- The H mark uses `Gtk.Image.new_from_file` with `set_pixel_size(40)` inside a 64×64 box so the SVG stops rendering at its intrinsic size
- **Why:** the design calls for a two-column welcome with its own chrome; the default app header lives inside the `app` stack child and does not appear when welcome is visible

## Onboarding modal chrome (`window_welcome.py`, `settings_accounts.py`, `styles.py`)

**Onboarding modal windows use a single custom header — no OS CSD duplicate.**
- `_strip_dialog_chrome(dialog)` replaces the OS titlebar with an empty `Gtk.Box` so the dialog renders with our internal header only
- `_build_modal_shell(title, subtitle, on_close)` in `window_welcome.py` builds the shared head: ADD ACCOUNT eyebrow, title, subtitle, close button, divider, then body content
- The More Providers dialog is a `Gtk.Window` using the modal shell + `ALL_PROVIDERS_ORDER` of `.provider-row-tile` rows + a lock-pill footer
- The Connect Gmail / Connect <provider> setup dialog uses the same eyebrow + title + subtitle + close pattern
- **Why:** multiple modals had a doubled titlebar (OS + internal), which broke the coherent modal language in the design

## Banding mitigation (`styles.py`)

**Low-alpha gradients on dark surfaces were flattened because GTK4 CSS does not dither 8-bit gradients.**
- Flattened: `.welcome-photo`, `.search-entry-shell`, `.startup-status-panel`, `.startup-status-card`, `.attachment-bar`, `.thread-reply-bar`, `.message-info-bar`, `.reading-pane-shell`, `.thread-sidebar`, `.message-column`
- Where a gradient was intentional (accents, provider glyphs, orb), it stays because the area is small
- **Why:** broad 0.02→0 or 0.03→0 alpha transitions over hundreds of pixels created visible banding steps; on the modern dark palette the visual delta of these gradients was near zero, so flattening them loses almost nothing
