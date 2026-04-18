# Hermod

A native Linux email client built with Python, GTK 4, Libadwaita, and
WebKitGTK. Local-first, fast, quiet, and oriented around a single focused
reading surface.

## Status

Hermod is in active development. The current live app ships:

- A GTK 4 / Libadwaita desktop shell (dark theme, design-token driven)
- Welcome / onboarding surface with two-column layout and per-provider tiles
- Sidebar + message list + reading pane + inline compose
- Full thread rendering with a thread drawer and in-place quick reply
- Gmail provider (API-first) and generic IMAP/SMTP provider
- Local disk body cache, inbox snapshot cache, and lazy backend construction
- Appearance preferences (theme mode, day variant, accent, density, AI toggle)
- Diagnostics export and redaction helpers

The shipped Gmail auth path is Hermod-owned: browser + loopback callback +
PKCE with a Hermod-owned Desktop app OAuth client. Custom Google
credentials are treated as a developer override only.

## Run locally

From the project root:

```bash
python3 hermod.py
```

To render a headless PNG of the current window (useful on Wayland where
screenshot permissions are awkward):

```bash
python3 hermod.py --dump-ui /tmp/hermod.png
```

## Key modules

- `__main__.py` — application entrypoint and background poll loop
- `window.py` — main window shell and cross-surface coordination
- `window_welcome.py` — welcome / onboarding screens and the More
  Providers modal
- `window_mailbox_controller.py` — mailbox refresh, unread counts, startup
  reconciliation
- `window_message_cache.py` — snapshot loading and body prefetch
- `window_message_list.py` — list model, selection, paging
- `window_reader_controller.py` — body and thread fetch orchestration
- `window_reader.py` — reader rendering, thread view, original-message
- `compose.py` — inline compose / reply flow
- `settings.py` — settings storage, appearance section, preferences UI
- `settings_accounts.py` — account setup dialog, add/remove, IMAP test
- `backends.py` — provider registry and startup reconciliation façade
- `accounts/` — descriptors, registry, native store, preferences, auth
- `providers/gmail.py` — active Gmail provider (API-first)
- `providers/imap_smtp.py` — active IMAP/SMTP provider
- `styles.py` — design tokens, CSS, accent and theme override helpers
- `diagnostics/` — redaction, logging, export, health snapshots

## Project structure

- `accounts/` — account descriptors, auth helpers, native store,
  per-account preferences
- `providers/` — provider implementations and shared helpers
- `assets/` — welcome scene, provider art, Lucide icon set
- `diagnostics/` — redaction, logging, export
- `icons/` — app icon (`io.github.hermod.Hermod`)
- `tests/` — focused regression and behavior tests
- `tools/` — architecture graph generation, contract checks, project
  context exporter

## Where things live

- User settings: `~/.config/hermod/settings.json`
- Native account credentials: system keyring via `accounts/native_store.py`
- Body cache + snapshot cache: under XDG cache dir
- Architecture graph: `ARCHITECTURE.json` (regenerate, never hand-edit)

## Companion docs

- `CHANGES.md` — fixed behaviors to preserve (not a changelog)
- `VERIFICATION.md` — manual runtime verification checklist
- `ROADMAP.md` — phased roadmap and pending features
- `POTENTIAL_FEATURES.md` — intelligent-feature backlog (local AI)
- `AGENTS.md` — repo-local guidance for AI coding assistants

## Notes

- Hermod ships third-party provider art for onboarding. Provider logos
  remain the property of their respective owners.
- The repo moves quickly; onboarding, provider coverage, and auth UX are
  still being refined.
