# Hermod Runtime Verification Checklist

Use this checklist in a live GTK session.

## Preflight
- [ ] From `/home/david/Projects/Hermod`, run `python3 -m py_compile *.py`
- [ ] Launch the app with `python3 hermod.py`
- [ ] Confirm debug logging does not hide startup errors
- [ ] On GNOME Wayland, prefer the app-native live dump path when you need a visual capture:
  `python3 hermod.py --dump-ui /tmp/hermod-dump-ui.png`
- [ ] Force the welcome screen with `HERMOD_FORCE_WELCOME=1` before `--dump-ui` when verifying onboarding surfaces
- [ ] Use the shared Wayland screenshot helper to preview that PNG in-terminal when needed
  instead of relying on X11-only screenshot tooling
- [ ] If the app is already running, use `launch-fresh` for code/UI changes and `launch-or-focus`
  only when you want to inspect the existing live instance
- [ ] Use OCR only when the accessibility tree is not enough to verify visible text

## One-account flow
- [ ] App opens cleanly with a single account configured
- [ ] Initial mailbox selection is sensible
- [ ] Cached inbox snapshot appears before live refresh when available
- [ ] Live refresh does not replace the wrong folder or message content
- [ ] `Sync` and `New` appear above `All Inboxes`
- [ ] Search lives in the middle column and spans the row area visually
- [ ] Sidebar width remains fixed
- [ ] Reading pane uses the adaptive but conservative email surface hint
- [ ] Attachments display correctly and `Load images` applies live
- [ ] Compose opens inline in the reading pane
- [ ] Dirty compose navigation prompt appears on folder/message/window changes
- [ ] Rich-text controls visibly affect bold, italic, quote, list, color, and size

## Multi-account flow
- [ ] App opens cleanly with multiple accounts configured
- [ ] `All Inboxes` selection is correct on startup
- [ ] Account expansion and per-account folder navigation work
- [ ] Startup status shows warning or error states when a backend falls back or fails
- [ ] Add Account tiles open the expected flow for Gmail and manual IMAP/SMTP
- [ ] Account alias, accent color, and hidden/enabled state persist correctly
- [ ] Removing an account clears its local record and keyring-backed credentials
- [ ] Unread counts refresh without cross-folder pollution
- [ ] Unread-only toolbar mode hides read mail and shows friendly empty states
- [ ] Startup unread counts do not flash a cached estimate before reconciliation
- [ ] Startup status stays open when an account reports a warning or error and can be dismissed manually
- [ ] Trash and spam counts stay dim and separate from inbox counts
- [ ] Snapshot invalidation works when the account set changes
- [ ] Manual sync updates all relevant accounts
- [ ] Background check interval changes are reflected in background update timing
- [ ] The header control shows `Connected`, `Checking`, and `Offline` at the right times
- [ ] New Inbox arrivals show notifications without relying on unread-count deltas

## Message-list and reader correctness
- [ ] Startup autoselect populates the reading pane
- [ ] Rapid message changes do not show stale body content
- [ ] Body cache reuse works on reopen
- [ ] Disk cache reuse works on reopen
- [ ] Attachment bar visibility is correct
- [ ] Inline images render when images are allowed
- [ ] Same-day message times show local `HH:MM`
- [ ] Older-than-today message times use abbreviated month/day/time
- [ ] Older-than-a-year message times use numeric date/time
- [ ] Hover styling is visually weaker than actual selected-row styling
- [ ] Clicking a message leaves that message visibly selected in the middle column
- [ ] Keyboard selection movement keeps visible selected-row state in sync with the reader
- [ ] Reader pane uses the available width without a large dead side column for normal single-message view
- [ ] `Original` is not shown as a normal-message header action
- [ ] Thread-only affordances appear only when a real thread is open
- [ ] Thread drawer closes and reopens cleanly while shrinking content instead of overlaying it
- [ ] Thread open/close does not corrupt sender/account info shown in the reader header
- [ ] Thread bubbles expose an Original button that opens that message’s source
- [ ] Email links open through the desktop default handler

## Compose and send flow
- [ ] New compose, reply, and reply-all open in the reading pane
- [ ] Sender switching works
- [ ] BCC toggle works inline
- [ ] Close prompt offers keep-editing or discard only
- [ ] Discard clears the dirty compose buffer
- [ ] Plain-text send works
- [ ] Rich-text send preserves visible formatting

## Startup flow
- [ ] Startup status screen closes on its own after boot
- [ ] Sidebar unread counts appear only after the startup screen closes

## Welcome / onboarding
- [ ] With `HERMOD_FORCE_WELCOME=1`, the welcome screen renders with its own titlebar ("H HERMOD" brand) and flat left photo panel
- [ ] No banding visible on the photo panel, search shell, reading pane, or sidebar
- [ ] Provider tile grid shows Gmail, Proton, Outlook, Other (IMAP/SMTP) as row tiles with colored letter glyphs
- [ ] "Show all 8 providers" link opens the More Providers modal without a double titlebar
- [ ] More Providers modal lists all eight providers and has an ADD ACCOUNT eyebrow + single close button
- [ ] Connect <provider> dialog opens with stripped OS chrome and shows eyebrow + title + subtitle + close
- [ ] Completing setup returns to the welcome screen and shows the account in the "Accounts added" list
- [ ] "Continue to Hermod" appears only after at least one account exists

## Appearance settings
- [ ] Settings opens on the Appearance section containing theme / day variant / accent / density / AI toggle
- [ ] Switching theme mode updates the app live (no restart needed)
- [ ] Switching accent updates tinted surfaces live (provider rows, send button, badges)
- [ ] Switching density adjusts row heights immediately
- [ ] AI toggle persists across restarts

## Result log
- Date:
- Build:
- Pass/Fail:
- Notes:
- Follow-up bugs:
