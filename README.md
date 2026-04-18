# Hermod

Hermod is a Linux desktop email client built with Python, GTK 4, Libadwaita, and WebKitGTK.

It is aimed at a native desktop feel, fast mailbox and reader workflows, and a more modern onboarding experience than traditional mail clients.

## Status

Hermod is in active development.

Current live app shape:

- GTK 4 / Libadwaita desktop application
- main shell, mailbox list, reader, thread view, and compose flow
- onboarding hub with provider tiles and welcome screen
- active provider backends for Gmail and IMAP/SMTP

Current product direction for Gmail:

- browser-based desktop OAuth flow
- loopback callback on the local machine
- PKCE-based sign-in
- no end-user client secret entry in the normal shipped flow
- custom Google OAuth credentials are for developer or advanced override use only

## Run locally

From the project root:

```bash
python3 hermod.py
```

## Key modules

- `window.py` — main window shell and surface coordination
- `window_welcome.py` — onboarding and welcome screen
- `window_message_list.py` — mailbox list behavior
- `window_reader.py` — reader and thread presentation
- `compose.py` — compose and reply flow
- `settings_accounts.py` — account setup, editing, and auth flows
- `providers/gmail.py` — Gmail provider
- `providers/imap_smtp.py` — IMAP/SMTP provider

## Project structure

- `accounts/` — account descriptors, auth helpers, native store, preferences
- `providers/` — provider implementations and shared provider helpers
- `diagnostics/` — logging, export, and health helpers
- `assets/` — app art and provider assets
- `tests/` — focused regression and behavior tests

## Notes

- Hermod currently ships project assets for third-party email providers as part of the in-app onboarding UI.
- This repository is moving quickly; onboarding, provider polish, and auth UX are still being refined.
