# Hermes Companion Architecture

## Goal

Provide a simple self-installer that users can email or run on any computer.
The installer sets up:

- A clean local GUI chat shell
- A background daemon
- Secure invite-based registration with the central Hermes control plane

Once registered, the central Hermes can SSH into the machine, run commands,
access files, start and stop apps, and treat the node like any other Hermes
remote backend.

## Components

1. **Central Hermes**
   - Runs `hermes mcp serve` or an OpenAI-compatible gateway
   - Runs the invite and registration API in `central-api/server.py`
   - Installs the `register_remote_node`, `issue_remote_invite`,
     `list_remote_nodes`, and `revoke_remote_node` skills
   - Stores invite state, node tokens, and node metadata
   - Supplies the central SSH public key to approved clients during invite redemption

2. **Hermes Companion (remote machine)**
   - **GUI**: Tauri 2 desktop app with a minimal Svelte 5 interface
   - **Daemon**: Python service that:
     - Generates an Ed25519 SSH keypair locally
     - Redeems a one-time invite to receive central endpoints and a short-lived node token
     - Installs central Hermes's SSH public key into the authorized local account
     - Registers the node public key, hostname, OS, architecture, and requested SSH user with central Hermes
     - Writes status into a local JSON status file for the GUI
     - Keeps a WebSocket open for liveness reporting and re-registration commands
     - Runs with per-user privileges and auto-starts at login

3. **Packaging**
   - Tauri produces:
     - macOS `.dmg`
     - Windows `.exe`
     - Linux `.AppImage`
   - Build scripts package the Python daemon into a standalone binary using PyInstaller
   - The GUI copies the daemon into the user app data directory during first connect

## Networking

- Enrollment: HTTPS `POST` to a one-time invite redemption endpoint
- Registration: HTTPS `POST` to the configured central registration endpoint
- Chat UI: OpenAI-compatible `Responses` or `chat.completions` compatible HTTP endpoint
- Presence: WebSocket with reconnect and heartbeat support
- Ongoing remote access: central Hermes uses native SSH against the enrolled node account

## Enrollment Flow

1. Central Hermes issues a one-time invite.
2. Hermes Companion redeems the invite and receives:
   - central display name
   - registration, heartbeat, and chat endpoints
   - a short-lived node bearer token
   - the central SSH public key
   - the local username central should connect as
3. Hermes Companion writes the daemon runtime config and starts the per-user service.
4. The daemon installs the central SSH public key into the target account's `authorized_keys`.
5. The daemon registers the machine public key and metadata with central Hermes.
6. The daemon maintains heartbeat and websocket presence until revoked.

## Local Files

Under the user app data directory, Hermes Companion stores:

- `client-config.json`: persisted central endpoint details
- `daemon/daemon-config.json`: runtime config passed to the daemon
- `daemon/bin/`: installed daemon payload
- `keys/hermes_node_ed25519`: local private key
- `keys/hermes_node_ed25519.pub`: local public key
- `status/node-status.json`: daemon health and registration state
- `logs/hermes-node-daemon.log`: rotating daemon log

## Security Model

- No passwords are sent or stored
- The private key never leaves the machine
- Only the node public key and machine metadata are sent during registration
- One-time invites mint per-node bearer tokens instead of reusing a shared installer secret
- The daemon runs as the signed-in user, not as root
- Central Hermes SSH authorization is installed on the remote node, not on central
- Registration and revocation are auditable through central JSON state plus Hermes memory

## Known V1 Constraints

- The per-user install path is optimized for the signed-in desktop user.
- On Windows, the per-user daemon starts through the Startup folder instead of Task Scheduler.
- A dedicated local `hermes` service account is not yet auto-provisioned.
- Raw inbound SSH over the public internet is still network-dependent. For broader real-world reliability, a mesh or outbound connectivity layer such as Tailscale should be the next major step.
