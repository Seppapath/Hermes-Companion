# Security Hardening

- Use Ed25519 keys only
- Generate the SSH keypair on the client
- Send only the node public key, hostname, OS, architecture, SSH user, and non-secret node metadata
- Use one-time invite codes or signed invite links for enrollment
- Mint per-node bearer tokens during invite redemption instead of embedding a shared secret in the installer
- Keep the daemon per-user and avoid root after install
- Write SSH keys with restrictive filesystem permissions
- Store daemon config under the app data directory, not the working tree
- Install the central Hermes SSH public key locally on the remote machine rather than adding remote keys to central
- Prefer a dedicated restricted local account for SSH in a future hardened release. In v1, default to the signed-in user unless that machine is explicitly provisioned another way.
- Prefer HTTPS and bearer tokens for registration and chat
- Support optional WebSocket TLS and mTLS in front of the central daemon
- Use rate limiting and audit logging on the registration endpoint
- Revoke node tokens centrally and remove or rotate SSH authorization on the remote when access is revoked
- Keep UI capabilities minimal and route privileged work through native Rust commands
- Sign and notarize installers before public distribution
- Treat public repo visibility as acceptable only if enrollment remains private
