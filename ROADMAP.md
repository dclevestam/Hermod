# Hermod Roadmap

Phased plan for the rest of the build. Each phase has a concrete **goal**,
a **scope** list (what is in / out), **acceptance criteria**, and the
**files** most likely to change. Order is deliberate: finish the phase in
front before starting the next.

The roadmap is the working plan. When a phase lands, promote its
decisions into `CHANGES.md` and retire the phase from this file.

---

## Legend

- Status tags: `NEXT` (work starts here), `QUEUED`, `LATER`, `PARKED`.
- "Ship gate" means the phase is done for external eyes; internal
  polish can still land under a later phase.

---

## Phase 0 — Design pass (IN FLIGHT)

**Status:** mostly done, a handful of polish items open.
**Goal:** make the live GTK app match the design prototype in
`/home/david/Downloads/Hermod Images/Hermod Design Claude.zip`.

Done in this pass:

- Design tokens in `styles.py` (`ACCENT_PALETTE`, `DAY_PALETTES`,
  `DENSITY_ROW_HEIGHT`, `build_theme_override_css`)
- Settings Appearance section (theme / day variant / accent / density /
  AI toggle) with live apply via `window.apply_theme()`
- Welcome screen rewritten to the two-column layout with its own
  Adw.HeaderBar ("H HERMOD" brand)
- H mark sized correctly via `Gtk.Image.new_from_file` + `pixel_size`
- More Providers dialog rewritten as a `Gtk.Window` with stripped OS
  chrome and the shared `_build_modal_shell` head
- Connect Gmail / Connect <provider> setup dialog now uses eyebrow +
  title + subtitle + close pattern
- Banding-prone low-alpha gradients flattened on the photo panel,
  search shell, startup panels, reading pane, sidebar, message column,
  attachment and reply bars

Still open under Phase 0:

1. **Welcome photo asset** — replace the flat placeholder panel with
   the real forest/aurora artwork (AVIF or pre-rendered gradient PNG
   to avoid banding).
2. **Window move on welcome** — confirm drag-to-move on the photo
   column still works through the new Adw.ToolbarView header.
3. **Accounts list polish** — after setup, the "Accounts added"
   section needs a design pass to match the prototype exactly (row
   tokens, spacing, actions).
4. **Compose surface** — verify COMPOSE_CSS tokens flow correctly
   after theme switches (the Appearance section should retint compose
   live).
5. **Reader header** — the thread header bar and info bar still carry
   the old flat styling; make them consume the tokens from
   `build_theme_override_css`.

**Acceptance:** every screenshot in
`/home/david/Downloads/Hermod Images/` has a matching live dump-ui
artifact.

---

## Phase 1 — Provider coverage (QUEUED)

**Goal:** all eight onboarding tiles lead to a working account, not
just Gmail / IMAP.

Scope (in):

- **Microsoft Graph** — activate `providers/microsoft_graph.py` or
  create it under `providers/`, register through `backends.py`, wire
  up the OAuth flow in `accounts/auth/`, and surface it through
  `settings_accounts.py`. Target feature parity with Gmail API path
  (list, body, flags, unread counts, send).
- **Proton Bridge** — IMAP/SMTP through local bridge; detect bridge
  availability and surface a helpful error when it is off.
- **iCloud Mail** — IMAP + app password flow; capture the
  app-password recipe in the setup dialog subtitle.
- **Fastmail, Yahoo, Zoho** — IMAP + app password recipes.
- **Exchange (on-prem)** — EWS via IMAP-compatible gateway; defer
  native EWS.

Scope (out): Exchange Web Services native, Office 365 enterprise
tenants with conditional-access policies.

**Acceptance:**

- Each provider in `ALL_PROVIDERS_ORDER` (see
  `window_welcome.py:56`) leads to a dialog that can reach "connected"
  on a real account.
- `tests/test_provider_*` covers the listing/body/flag path for the
  new providers using recorded fixtures.
- Provider art under `assets/providers/` is normalised (PNG for the
  few that need real logos, SVG letter glyphs as fallback).

**Files:** `providers/*`, `backends.py`, `accounts/registry.py`,
`accounts/auth/*`, `settings_accounts.py`, `window_welcome.py`,
`tests/test_provider_*.py`.

---

## Phase 2 — Reader / thread polish (QUEUED)

**Goal:** the reading pane is the flagship surface; it should feel
settled before intelligent features layer on top.

Scope:

- Reader pane uses available width without a dead side column
  (carryover from BUGS.md #3).
- Inline image rendering confirmed across multi-image messages
  (BUGS.md #4).
- Thread-member edge cases (BUGS.md low-priority #1) handled —
  absent-members, single-message "threads", self-reply chains.
- Thread drawer open/close animation matches design timings.
- Attachment chip hover/active states match tokens.
- Quoted-text collapse has a visible affordance.

**Acceptance:** manual verification against three canonical real
threads (single message, long thread, multi-image marketing mail)
with dump-ui snapshots saved under `diagnostics/`.

**Files:** `window_reader.py`, `window_reader_controller.py`,
`thread_renderer.py`, `styles.py`.

---

## Phase 3 — Intelligent features, local-only (LATER)

Drives the items in `POTENTIAL_FEATURES.md`. This phase is gated on
Phase 0 and Phase 2 finishing because the reading pane is where
intelligence renders.

Infrastructure first:

- **Local model runtime** — settle on LM Studio over `localhost` via
  an OpenAI-compatible endpoint; add `hermod.ai` module with a thin
  client, config keys in `settings.py` (`ai_endpoint`,
  `ai_model_name`, plus existing `ai_enabled`).
- **Embedding store** — decide between sqlite-vec and a flat FAISS
  cache under XDG cache; wrap both behind a `hermod.ai.embeddings`
  interface.
- **Redaction before model calls** — route every prompt through
  `diagnostics.redaction` to strip obvious secrets.

Feature order:

1. **Thread summarisation** — TL;DR, pending actions, status, stamped
   into the thread drawer. Only runs when `ai_enabled`.
2. **Smart reply drafting** — three suggested replies above the
   compose surface; generated on demand, not streamed into the text
   area until the user accepts.
3. **Intent & action detection** — extract dates and action-ish
   phrases, surface a "looks like a task" row above the reader.
4. **Data extraction chips** — tracking numbers, flight codes, meeting
   links pinned above the message body.
5. **Semantic search** — vector index of subjects + bodies; NL query
   box in the sidebar.
6. **Priority ranking** — local classifier bubbles "deep work" vs
   "newsletter"; affects list sort when opted in.

**Acceptance:**

- All features are opt-in behind `ai_enabled`.
- No network egress beyond the configured local endpoint — verified
  in `tests/test_ai_*` by asserting no outbound requests except to the
  configured host.
- Each feature has a visible kill switch in Settings.

**Files (new):** `hermod/ai/`, `tests/test_ai_*`.
**Files (edit):** `settings.py`, `window_reader.py`, `compose.py`,
`window_message_list.py`, `styles.py`.

---

## Phase 4 — Packaging and distribution (LATER)

**Goal:** someone other than David can install Hermod.

Scope:

- Flatpak manifest (`io.github.hermod.Hermod`) under `packaging/`.
- Appstream metadata + `.desktop` file.
- Release pipeline: tag → Flathub submission checklist.
- Optional: AppImage for people who avoid Flatpak.

**Acceptance:** `flatpak-builder` build passes in a clean container
and the installed app launches and signs a real account in.

**Files (new):** `packaging/*`, `.github/workflows/release.yml`.

---

## Phase 5 — Accessibility and keyboard pass (LATER)

Scope:

- Every interactive element reachable via keyboard.
- Screen reader labels on provider tiles, accent swatches, account
  rows.
- High-contrast mode variant under `day_variant`.
- Font scaling respected.
- Shortcut cheatsheet dialog (`Ctrl+?`).

**Files:** `window*.py`, `styles.py`, new `shortcuts.py`.

---

## Parked / open research

- Calendar and contacts integration — not yet in scope; would need a
  separate provider abstraction.
- Real EWS native provider — wait for real demand.
- Encryption (PGP / S/MIME) — only after Phase 1 is settled.
- Mobile / GNOME Mobile shell — parked until Phase 4 ships.

---

## Per-phase exit ritual

1. Promote the phase's "done" bullets into `CHANGES.md` under a new
   top-level section.
2. Retire the phase from this file (delete or mark `LANDED`).
3. Regenerate architecture artifacts:
   ```bash
   python3 tools/update_architecture.sh
   python3 tools/check_architecture_contracts.py
   ```
4. Run the relevant portion of `VERIFICATION.md` and log the result.
5. Update `/home/david/AI/hub/projects/Hermod/CURRENT.md` so the hub
   matches the repo.

---

## Cross-reboot continuity

After reboot, resume from:

- This file (`ROADMAP.md`) for the plan.
- `CHANGES.md` for fixed behaviours that must not regress.
- `VERIFICATION.md` for the smoke ritual.
- `/home/david/.claude/projects/-home-david/memory/handover.md` for
  the short "what was I doing" note.
- `/home/david/AI/hub/projects/Hermod/CURRENT.md` for live hub state.
