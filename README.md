# Hermes Companion

Hermes Companion is a self-installing desktop node for a personal Hermes network.
It packages a minimal Tauri 2 + Svelte 5 chat shell together with a background
Python daemon that registers the machine with a central Hermes deployment,
installs central SSH authorization locally, and provisions a local Ed25519 keypair
without ever sending the private key.

## What It Installs

- A native desktop app for macOS, Windows, and Linux
- A per-user background daemon that auto-starts on login
- A local Ed25519 SSH keypair stored under the app data directory
- A one-time invite enrollment flow
- Local installation of the central Hermes SSH public key for the authorized local user
- A lightweight registration flow that submits only the node public key and machine metadata

## Repository Layout

- `src/`: Svelte 5 desktop UI
- `src-tauri/`: Tauri 2 shell, native commands, daemon bootstrap logic
- `daemon/`: Python daemon, service templates, runtime dependencies
- `central-api/`: reference FastAPI enrollment and control-plane API
- `chat-bridge/`: localhost-only safe chat backend for reusing a central Hermes provider login without exposing Hermes tools to enrolled nodes
- `central-skill/`: Hermes skills for issuing invites, listing nodes, and revoking nodes
- `scripts/`: cross-platform packaging helpers
- `tests/`: daemon and central API smoke tests

## Local Development

1. Install Node.js 20+, Rust 1.77+, and Python 3.9+.
2. Copy `.env.example` if you want defaults for packaging.
3. Install frontend dependencies with `npm install`.
4. Start the desktop app with `npm run tauri:dev`.

The normal `Connect` flow is invite-driven:

1. Central Hermes creates a one-time invite.
2. The desktop user pastes the invite link or code.
3. The app redeems the invite, persists the returned central endpoints, and writes a daemon config file.
4. The daemon installs a per-user background service:

- macOS: `launchd` LaunchAgent
- Windows: Startup folder launcher
- Linux: `systemd --user` service

The daemon then:

- generates a local Ed25519 keypair if needed
- installs central Hermes's SSH public key into the local authorized user's `authorized_keys`
- registers the machine using a short-lived node token minted from the invite

### Owner-Operator Shortcut

If you run the central Hermes control plane yourself, Hermes Companion can now
create the one-time invite from the local machine and connect immediately.

In the desktop app:

1. Open `Guided Setup`.
2. Enter the public central URL.
3. Enter the central admin secret.
4. Paste the dedicated central SSH public key for node access.
5. Press `Create Invite + Connect`.

The admin secret is used only to mint the invite and is not stored in the
client config file.

### Create-My-Server Shortcut

If you do not have a central Hermes server yet, the setup wizard can generate a
server setup pack on your local machine.

That pack includes:

- a central API env file
- a safe chat bridge env file
- nginx templates
- systemd service files
- a bootstrap shell script
- a plain-English checklist
- an optional PowerShell upload/bootstrap script when you know the server SSH target

This keeps the public repo agnostic while still giving a beginner a concrete
path from "I have a Linux server" to "I can issue invites."

## Packaging

Build scripts package the Python daemon with PyInstaller, copy the daemon into
`src-tauri/resources`, and invoke Tauri packaging:

- macOS DMG: `./scripts/build-macos.sh`
- Linux AppImage: `./scripts/build-linux.sh`
- Windows EXE: `pwsh ./scripts/build-windows.ps1`

## Central Hermes Requirements

Install the skills in `central-skill/` on the Hermes node that acts as your
operator control plane.
For the reference implementation, also run the FastAPI service in `central-api/`.
The desktop client expects:

- An HTTPS invite redemption endpoint
- An HTTPS registration endpoint that accepts JSON node metadata
- An OpenAI-compatible chat endpoint for the UI
- A WebSocket endpoint for daemon presence and status streaming

Reference deployment templates for the FastAPI control plane live in `deploy/`.
For a real host, use a dedicated HTTPS hostname, proxy the service through nginx,
and validate the live endpoint with `scripts/validate-central-api-deploy.py`.
The control plane can either proxy `/v1/responses` to an OpenAI-compatible
backend or to the checked-in localhost chat bridge. Direct proxying to a
general-purpose Hermes web UI is intentionally disabled by default because that
can expose operator-side tools to enrolled nodes.

## Security Posture

- The repo can be public.
- Enrollment should stay private through one-time invites or a central approval flow.
- Do not ship a reusable registration secret inside the client.
- API tokens should live in OS-native secure storage when available, with protected file fallback only where needed.
- The reference central API stores invite codes and node bearer tokens as SHA-256 digests at rest instead of plaintext secrets.
- The client installs central SSH authorization locally instead of granting remote machines SSH access into central.
- The default per-user install targets the signed-in user. Using a separate dedicated local account is possible later, but it requires explicit provisioning beyond the current v1 workflow.

## Production Operations

- Off-network laptops should use an explicit reachability layer such as Tailscale; Hermes Companion v1 does not by itself solve inbound SSH reachability across NAT and roaming networks.
- Lost or stolen devices should be handled by revoking the node immediately and re-issuing a new invite for any replacement machine.
- The production runbook lives in `OPERATIONS-RUNBOOK.md`.

## Smoke Tests

- `python tests/smoke_daemon.py`
- `python tests/smoke_central_api.py`
- `python tests/smoke_chat_bridge.py`
- `python tests/smoke_daemon_secure_store.py`
- `python tests/e2e_live_stack.py`

## Architecture Notes

The seed architecture and hardening notes live in:

- `architecture.md`
- `SECURITY.md`
- `security-hardening.md`
- `production-plan.md`
- `OPERATIONS-RUNBOOK.md`
