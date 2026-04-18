# Hermod — Fixed Behaviours (do not revert)

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
