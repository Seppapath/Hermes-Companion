# Hermes Companion Production Plan

## Product Direction

Hermes Companion should be a public desktop client with private enrollment.
The install experience should be:

1. Admin issues an invite from central Hermes.
2. User installs the desktop app.
3. User pastes or opens the invite.
4. The machine enrolls itself and becomes reachable from central Hermes.

## What V1 Must Guarantee

- No shared installer secret
- One-time or short-lived enrollment
- Local Ed25519 key generation
- Central SSH authorization installed on the remote machine
- Signed desktop installers
- Revocation that disables future API and websocket access
- Auditable node registry on central Hermes

## Current V1 Architecture

- Desktop UI: Tauri 2 + Svelte 5
- Local service: Python daemon
- Central control plane: FastAPI reference service in `central-api/`
- Central operator skills: `central-skill/`

## Delivery Phases

### Phase 1: Secure Enrollment Baseline

- Ship one-time invite redemption
- Persist invite-derived endpoints locally
- Install central SSH authorization on the remote machine
- Register node metadata using per-node bearer tokens
- Support node listing, revocation, and forced re-registration

### Phase 2: Production Packaging

- Add macOS Developer ID signing and notarization
- Add Windows code signing
- Add release checksums and signed update metadata
- Add CI to run frontend, Rust, daemon, and central API smoke tests on every change

### Phase 3: Real-World Reachability

- Add a network layer that survives NAT and roaming
- Recommended first implementation: Tailscale-backed reachability
- Alternative: persistent outbound broker channel from node to central
- Do not rely on raw inbound public SSH as the only production path

### Phase 4: Host Hardening

- Offer optional dedicated local `hermes-node` account provisioning
- Store tokens in OS-native secret storage
- Add stronger approval workflows and device trust policy
- Add SSH key rotation and central certificate pinning

### Phase 5: Mobile Operator Experience

- Ship a mobile control app for chat, approvals, alerts, and node status
- Keep mobile as an operator console first, not a managed host runtime

## Engineering Backlog

- Add central dashboard or admin CLI for invite issuance
- Add persistent audit logs and structured event history
- Add upgrade-safe migrations for runtime config and central registry state
- Evaluate rewriting the Python daemon in Rust after the flow is stable

## Current Release Status

As of April 4, 2026, the current workspace now has:

- invite-based enrollment as the default flow
- per-node token revocation with daemon-side SSH authorization cleanup
- OS-native secure token storage with protected file fallback
- signed-installer release scaffolding for Windows and macOS
- CI coverage for frontend, Rust, daemon, central API, Windows secure-store smoke, and live stack smoke
- an explicit operator runbook for off-network reachability and lost/stolen device recovery

The remaining launch-critical validation is one clean-machine packaged installer acceptance pass per target platform using the signed release artifacts.
The deployed Companion control plane must also be reachable on its production hostname and pass the live deployment validator.

## Release Gate For Public Launch

Do not call the project production-ready until all of the following are true:

- Invite-based enrollment is the default path
- Revocation disables tokens immediately
- Signed installers are in place
- CI covers smoke tests across the stack
- The Companion central API is deployed behind HTTPS and validated live
- The networking story for off-network laptops is explicit
- The operator has a documented recovery path for lost or stolen devices
