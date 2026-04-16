# Hermod

- `python3 hermod.py` is the app launcher; it immediately runs `__main__.py`.
- Live project docs are in `/home/david/AI/hub/projects/Hermod/`: `CURRENT.md`, `ARCHITECTURE.md`, `INDEX.md`.
- `opencode.json` is the repo-level OpenCode config; `tui.json` is only UI/theme config.
- Root Python modules are the app, `tests/` are unit tests, and `tools/` contains graph/context generation and checks.
- Do not hand-edit `ARCHITECTURE.json` or `.codex/project_context.json`; regenerate them with `python3 tools/update_architecture.sh`.
- After architecture changes, run `python3 tools/check_architecture_contracts.py`.
- Cheap verification first: `python3 -m py_compile *.py`, then `python3 hermod.py`.
- For live GTK visual verification on Wayland, use `python3 hermod.py --dump-ui /tmp/hermod-dump-ui.png`.
- UI code should go through `backends.py` and controllers; provider modules own backend truth.
- Start each coding project by forming a full-project implementation map, then execute the smallest correct slice.
- If a helper lookup misses a file or path, treat that as drift: update the project map/docs first, then continue from the corrected source of truth.
- Keep coding passes lean: prefer exact file lookups, stop reading once the implementation shape is clear, and verify with the smallest useful check.
- Avoid broad repo sweeps unless the map is genuinely stale; use `project-explorer` for exact paths and `project-manager` for sequencing.
- Common edit points: `__main__.py`, `window.py`, `window_mailbox_controller.py`, `window_message_cache.py`, `window_reader_controller.py`, `settings_accounts.py`, `providers/gmail.py`, `providers/imap_smtp.py`.
