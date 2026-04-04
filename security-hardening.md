# Security Hardening

- Use Ed25519 keys only
- During registration: send only public key + hostname + OS info
- On central: add key with `command="restricted-shell"` or specific allowed commands
- Daemon runs as dedicated user (`hermes-node`)
- No root required after install
- Optional mTLS for WebSocket
- Rate limiting on registration endpoint
- Auto-revoke capability from central
