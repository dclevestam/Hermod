# Hermod — Agent Guide

Compact, repo-local rules for AI coding assistants. Read this after the
hub-level index. Live project docs are in
`/home/david/AI/hub/projects/Hermod/` (`CURRENT.md`, `ARCHITECTURE.md`,
`INDEX.md`).

## Launch

- `python3 hermod.py` launches the app (runs `__main__.py`).
- `python3 hermod.py --dump-ui /tmp/hermod.png` renders the current
  window to a PNG and quits — preferred visual check on Wayland.
- `HERMOD_FORCE_WELCOME=1` forces the welcome screen even when backends
  exist (useful for onboarding work).

## Verification ladder (cheapest first)

1. `python3 -m py_compile *.py` — syntax-only sanity.
2. `python3 -m pytest tests/<focused_test>.py` — run the narrowest
   failing or relevant test.
3. `python3 hermod.py --dump-ui /tmp/hermod.png` — visual artifact.
4. Full `python3 hermod.py` only when interactive behaviour matters.

## Architecture artifacts

- Do **not** hand-edit `ARCHITECTURE.json` or
  `.codex/project_context.json`. Regenerate:
  `python3 tools/update_architecture.sh`
- After architecture changes, run
  `python3 tools/check_architecture_contracts.py`.

## Coding conventions

- UI code should render state; backend truth lives in providers.
- Reach providers through `backends.py` and its controllers, not by
  importing provider modules directly into UI code.
- Design tokens (colors, densities, accents) live in `styles.py`
  (`ACCENT_PALETTE`, `DAY_PALETTES`, `DENSITY_ROW_HEIGHT`,
  `build_theme_override_css`). Settings keys in `settings.py`
  (`THEME_MODES`, `DAY_VARIANTS`, `ACCENTS`, `DENSITIES`,
  `ACCENT_COLORS`).
- Modal windows must strip OS CSD chrome via
  `dialog.set_titlebar(Gtk.Box())` and add the
  `onboarding-modal-window` CSS class. Use the
  `_build_modal_shell` pattern from `window_welcome.py` for head +
  divider + body.
- Welcome screen owns its own header bar (it is a `Gtk.Box`, not the
  main window — the main `_header_bar` lives inside the `app` stack
  child only).
- Avoid broad low-alpha gradients on dark backgrounds: GTK4 CSS has no
  dithering, so gradients spanning hundreds of pixels band badly.
  Prefer flat `background-color` with a tight localized glow only if
  needed.
- `Gtk.Picture` renders at SVG intrinsic size — prefer
  `Gtk.Image.new_from_file` with `set_pixel_size` for size-constrained
  glyphs.

## Common edit points

- `__main__.py`
- `window.py`, `window_welcome.py`
- `window_mailbox_controller.py`, `window_message_cache.py`,
  `window_reader_controller.py`
- `settings.py`, `settings_accounts.py`
- `compose.py`
- `styles.py`
- `providers/gmail.py`, `providers/imap_smtp.py`

## Scope discipline

- Form an implementation map before touching multiple files.
- Prefer exact file lookups (Glob/Grep with offset+limit on large
  files) over broad reads.
- If a helper lookup misses a file, treat it as drift: fix the map
  (`ARCHITECTURE.md`, `CURRENT.md`) first, then continue from the
  corrected source of truth.
- Keep coding passes lean; stop reading once the implementation shape
  is clear.

## Out-of-scope by default

- Renaming modules, reshuffling the window.* split, or adding new
  top-level packages without a specific task.
- Adding new provider backends without first updating
  `backends.py` + `accounts/registry.py` + the onboarding tile list in
  `window_welcome.py`.
- Hand-editing architecture artifacts (see above).
