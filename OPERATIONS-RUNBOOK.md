# Hermes Companion Operations Runbook

This runbook covers the production operating assumptions for Hermes Companion v1.

## Central API Deployment Baseline

The reference control plane in `central-api/server.py` should be deployed as a
dedicated internal service behind HTTPS.

Recommended baseline:

- run `uvicorn` bound to `127.0.0.1` only
- front it with nginx for TLS and WebSocket upgrade handling
- use a dedicated hostname instead of sharing a root IP or unrelated site root
- store JSON state in `/var/lib/hermes-companion`
- protect admin routes with a long random `HERMES_ADMIN_SECRET`; the reference service refuses the default `change-me` secret unless explicitly overridden for disposable local testing
- configure a real `/v1/responses` backend for production chat
- prefer `Authorization: Bearer ...` for websocket authentication instead of query-string tokens so reverse-proxy logs do not capture bearer secrets

Recommended production chat shape:

- preferred: point `HERMES_PROXY_RESPONSES_URL` at a dedicated localhost chat bridge that runs Hermes with `enabled_toolsets=[]`
- acceptable: point `HERMES_PROXY_RESPONSES_URL` at an external OpenAI-compatible gateway
- not recommended by default: proxy directly to a general-purpose Hermes web UI instance, because that instance may expose shell or filesystem tools to enrolled nodes

The checked-in chat bridge assets are:

- `chat-bridge/server.py`
- `deploy/chat-bridge.env.example`
- `deploy/systemd/hermes-companion-chat-bridge.service`
- `scripts/provision-chat-bridge-home.py`

The checked-in deployment templates live in:

- `deploy/central-api.env.example`
- `deploy/systemd/hermes-companion-central.service`
- `deploy/systemd/hermes-companion-chat-bridge.service`
- `deploy/nginx/hermes-companion-central.bootstrap.conf`
- `deploy/nginx/hermes-companion-central.conf`

After deployment, validate the public endpoint with:

- `python scripts/validate-central-api-deploy.py --base-url https://your-hostname --admin-secret ...`

## Supported Reachability Model

Hermes Companion v1 is production-ready only when the operator is explicit about how central Hermes reaches enrolled machines.

Current model:

- Enrollment, chat, heartbeat, and presence use outbound HTTPS and WebSocket connections from the node to central Hermes.
- Ongoing remote shell/file access still depends on SSH reachability to the enrolled machine and authorized local account.

Recommended production policy:

- For laptops or machines that roam across networks, use a managed connectivity layer such as Tailscale.
- Do not rely on raw public inbound SSH as the only reachability path for off-network devices.
- Treat Hermes Companion as the enrollment and control-plane client, not as a full NAT-traversal solution by itself.
- For expensive or sensitive hosts, place the node behind stronger network and account boundaries than the default signed-in-user v1 flow.

## Windows Auto-Start Model

Windows runs the daemon as a per-user background process that starts at login through the user's Startup folder.

- Startup launcher: hidden `wscript` VBS launcher
- Install target: `%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup`
- Legacy scheduled task installs are removed during install migration if present

This replaced the older Task Scheduler approach after live validation showed scheduled-task creation was not reliable for the current user context on the target Windows environment.

## Lost Or Stolen Device Response

If a device is lost, stolen, or no longer trusted:

1. Revoke the node from central Hermes immediately.
2. Confirm the node token is revoked and the node no longer appears connected.
3. Treat cached local access on the physical machine as compromised until the device is recovered or wiped.
4. Re-issue a fresh invite for any replacement machine instead of attempting to reuse prior enrollment state.

Effects of revoke in the current design:

- Future API requests using the node token are rejected.
- Future websocket connections using the node token are rejected.
- A connected daemon receives a `revoke` action, removes the managed SSH authorization entry, clears local registration state, and exits as `revoked`.
- The reference control plane stores bearer token digests at rest, not plaintext tokens.

## Central SSH Key Policy

Use a dedicated central SSH key for Companion-managed nodes.

- Do not reuse the same SSH private key you use for droplet administration or personal workstation login.
- Keep the Companion node-access key separate from the key used to log into the central server itself.
- If a node-access key is ever exposed, rotate it and re-issue node authorization entries.

On the current deployment, the dedicated central node-access public key lives separately from the Codex access key used for server administration.

## High-Value Host Guidance

For machines that can control expensive accelerators, proprietary model artifacts, or broad internal networks:

- avoid targeting `root` or a broadly privileged user for Companion SSH access
- prefer a dedicated restricted account with explicit filesystem and process permissions
- keep the machine on a restricted network segment
- prefer audited outbound reachability over broad public inbound SSH
- review node enrollment manually before issuing invites
- treat direct chat access to operator-side tools as out of scope for Companion nodes unless you intentionally design and audit that boundary

## Recovery Path For A Replaced Device

If a legitimate user replaces a machine:

1. Revoke the old node from central Hermes.
2. Verify the old node no longer shows as connected.
3. Issue a new one-time invite.
4. Install Hermes Companion on the replacement device.
5. Enroll the new device using the new invite.

Do not copy prior daemon state, node tokens, or SSH keys from the old machine to the new one.

## Release Checklist

Before public release:

- Installers are signed for each platform.
- macOS builds are notarized.
- CI passes on all required jobs.
- Windows secure-store, daemon smoke, and live-stack smoke pass.
- The Companion central API is deployed behind HTTPS on its intended hostname.
- Production chat does not rely on a general-purpose Hermes web UI unless the operator has explicitly hardened and accepted that risk.
- The live deployment passes `scripts/validate-central-api-deploy.py`.
- The operator understands the chosen reachability model for off-network devices.
- The operator has a documented revoke-and-reissue recovery path for lost or stolen devices.

## Post-Install Validation

For each release candidate, validate at least once on a clean machine:

1. Install the packaged app.
2. Redeem a one-time invite.
3. Confirm the daemon starts immediately after connect.
4. Confirm the machine appears registered and connected in central Hermes.
5. Confirm revoke removes the managed SSH authorization entry and leaves the daemon in `revoked`.
6. Confirm Windows starts the daemon again on user logon through the Startup-folder launcher.
