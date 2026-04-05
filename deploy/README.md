# Hermes Companion Central API Deployment

These templates are the production deployment baseline for the reference
Hermes Companion control plane in `central-api/server.py`.

Recommended production shape:

- dedicated Linux service user
- dedicated HTTPS hostname such as `companion.example.com`
- `uvicorn` bound to `127.0.0.1` only
- nginx reverse proxy terminating TLS and forwarding WebSocket upgrades
- state stored outside the checkout in `/var/lib/hermes-companion`
- a strong non-default `HERMES_ADMIN_SECRET`
- a separate localhost-only chat bridge for `/v1/responses` if you want to reuse an existing Hermes provider login
- an internal Mem0 OSS service for central long-term memory
- websocket clients authenticating with `Authorization` headers instead of query-string bearer tokens

Artifacts in this directory:

- `central-api.env.example`: environment variables for the FastAPI service
- `chat-bridge.env.example`: environment variables for the localhost safe chat bridge
- `memory-service.env.example`: environment variables for the local Mem0-backed memory service
- `mem0.env.example`: reference Mem0 OSS environment file
- `docker-compose.mem0.yml`: starter Mem0 + Neo4j compose stack for central memory
- `context-files/central-hermes/`: starter context files for the central Hermes workspace
- `systemd/hermes-companion-central.service`: hardened `systemd` unit template
- `systemd/hermes-companion-chat-bridge.service`: localhost-only no-tools chat bridge
- `systemd/hermes-companion-memory.service`: localhost-only central memory service
- `nginx/hermes-companion-central.bootstrap.conf`: HTTP-only bootstrap proxy for first cert issuance
- `nginx/hermes-companion-central.conf`: nginx site template with WebSocket support

Pair these templates with:

- `scripts/validate-central-api-deploy.py` for live endpoint validation
- `scripts/provision-chat-bridge-home.py` to create a minimal Hermes home with copied auth and an empty toolset
- `tests/smoke_central_api.py` and `tests/e2e_live_stack.py` for local repo validation
- `MEMORY-ARCHITECTURE.md` for the central-memory design
- `HERMES-ADDON-AUDIT.md` for the recommended capability rollout order
