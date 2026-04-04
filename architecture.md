# Hermes Companion Architecture

## Goal
Provide a simple self-installer that users can email/run on any computer. The installer sets up:
- A clean local GUI (chat shell)
- A background daemon
- Secure registration with the central Hermes Agent on DigitalOcean

Once registered, the central Hermes can SSH into the machine, run commands, access files, start/stop apps, etc.

## Components

1. **Central Hermes (DigitalOcean)**
   - Runs `hermes mcp serve` (OpenAI-compatible API)
   - Has a new skill: `register_remote_node`
   - Uses native SSH backend for all registered machines

2. **Hermes Companion (on each remote machine)**
   - **GUI**: Tauri app (lightweight native window with chat)
   - **Daemon**: Python service that:
     - Generates Ed25519 SSH keypair
     - Registers public key + machine info with central
     - Keeps a WebSocket connection for real-time status
     - Runs as a limited user

3. **Networking**
   - Registration: HTTPS POST to central endpoint
   - Ongoing access: Central uses SSH (key-based, command-restricted)
   - Optional: Tailscale for easier mesh networking

## Security
- No passwords ever sent
- Keys generated client-side
- SSH authorized_keys with restrictions (no shell by default if desired)
- Daemon runs with minimal privileges
- All logs sent to central for audit
