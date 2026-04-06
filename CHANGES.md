# Lark — Fixed Behaviours (do not revert)

## Email reading pane (window.py `_get_email_css`)

**Email content always renders on white background.**
- `body { background-color: #ffffff }` — always white, never dark
- No `* { color: !important }` overrides — emails specify their own colors
- No `a { color: !important }` override
- The dark frame *around* the email area comes from `webview.set_background_color()` in `_update_webview_bg()`
- **Why:** emails are designed for white backgrounds. Forcing dark bg + light text breaks emails that specify dark text without an explicit background (dark-on-dark). Forcing light text breaks emails with explicit light backgrounds like white cards (light-on-light, e.g. Anthropic receipts).

## Gmail localized folder names (backends.py `GmailBackend`)

**Folder names are resolved via `_detect_special_folders` + `_resolve_folder`.**
- `_detect_special_folders(imap)` called on every new IMAP connection (inside `_get_imap`)
- Parses `\Sent`, `\Drafts`, `\Trash`, `\Junk` flags from `imap.list()` → maps to actual IMAP names
- `_resolve_folder(folder)` translates e.g. `[Gmail]/Trash` → `[Gmail]/Papperskorgen` for Swedish accounts
- All imap operations use `_resolve_folder`: `fetch_messages`, `fetch_body`, `mark_as_read`, `mark_as_unread`, `delete_message`, `get_unread_count`
- **Why:** Swedish (and other localized) Gmail accounts use localized folder names. Hardcoded English names like `[Gmail]/Sent Mail` cause `EXAMINE` to fail with "illegal state AUTH".

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
  keep editing, save a draft, or discard changes when the draft is dirty
- Compose rich text supports bold, italic, quote, list, text color, and font size
- **Why:** compose should share the same main surface and navigation model as
  settings, while protecting draft state when the user clicks elsewhere.

## Sidebar width and badges (window.py)

**The left sidebar stays at the current minimum width and always reserves badge space.**
- Sidebar width is clamped to the current minimum width instead of being user-expandable
- Account/folder labels ellipsize against a fixed trailing badge slot
- Unread badges appearing or disappearing should no longer shift row text width
- The middle message list can still be dragged wider than before
- **Why:** the sidebar should feel stable and predictable instead of changing
  label width whenever counts change.
