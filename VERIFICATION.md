# Lark Runtime Verification Checklist

Use this checklist in a live GTK session.

## Preflight
- [ ] From `/home/david/Projects/Lark`, run `python3 -m py_compile *.py`
- [ ] Launch the app with `python3 lark.py`
- [ ] Confirm debug logging does not hide startup errors

## One-account flow
- [ ] App opens cleanly with a single account configured
- [ ] Initial mailbox selection is sensible
- [ ] Cached inbox snapshot appears before live refresh when available
- [ ] Live refresh does not replace the wrong folder or message content
- [ ] `Sync` and `New` appear above `All Inboxes`
- [ ] Search lives in the middle column and spans the row area visually
- [ ] Sidebar width remains fixed
- [ ] Reading pane keeps the locked white email surface
- [ ] Attachments display correctly and `Load images` applies live
- [ ] Compose opens inline in the reading pane
- [ ] Dirty compose navigation prompt appears on folder/message/window changes
- [ ] Rich-text controls visibly affect bold, italic, quote, list, color, and size

## Multi-account flow
- [ ] App opens cleanly with multiple accounts configured
- [ ] `All Inboxes` selection is correct on startup
- [ ] Account expansion and per-account folder navigation work
- [ ] Unread counts refresh without cross-folder pollution
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
- [ ] Hover styling is visually weaker than actual selected-row styling
- [ ] Clicking a message leaves that message visibly selected in the middle column
- [ ] Keyboard selection movement keeps visible selected-row state in sync with the reader
- [ ] Reader pane uses the available width without a large dead side column for normal single-message view
- [ ] `Original` is not shown as a normal-message header action
- [ ] Thread-only affordances appear only when a real thread is open
- [ ] Thread drawer closes and reopens cleanly while shrinking content instead of overlaying it
- [ ] Thread open/close does not corrupt sender/account info shown in the reader header

## Compose and send flow
- [ ] New compose, reply, and reply-all open in the reading pane
- [ ] Sender switching works
- [ ] BCC toggle works inline
- [ ] Save draft writes a local draft
- [ ] Discard clears the dirty draft
- [ ] Plain-text send works
- [ ] Rich-text send preserves visible formatting

## Result log
- Date:
- Build:
- Pass/Fail:
- Notes:
- Follow-up bugs:
