# Security Policy

Hermes Companion is intended to be open-source. The safe model is:

- public source code
- private enrollment
- per-node secrets minted at runtime
- high-value hosts operated with explicit trust boundaries

## Supported Security Posture

For a production deployment, treat these as required:

- run the Companion control plane behind HTTPS only
- set a strong non-default `HERMES_ADMIN_SECRET`
- keep model-provider credentials out of the repo and out of client bundles
- use one-time invites instead of a shared installer secret
- store client API tokens in OS-native secure storage where available
- store invite codes and node bearer tokens as hashes at rest on central
- authenticate websocket clients with `Authorization` headers instead of query-string bearer tokens
- use a dedicated central SSH key for Companion node access
- rotate or revoke node access immediately when a device is lost or no longer trusted
- keep production chat on a no-tools backend unless you have explicitly designed and audited a broader tool boundary

## Recommendations For High-Value Hosts

If Hermes Companion will reach machines that can affect expensive compute, private model weights, or sensitive data, add stronger controls on top of the v1 baseline.

Recommended minimums:

- do not use root as the node login target
- create a dedicated local account for Companion access on Linux boxes when possible
- keep password SSH login disabled
- keep the Companion control plane separate from unrelated public sites, even if they share a host
- prefer a managed reachability layer such as Tailscale over raw public inbound SSH
- segment high-value machines onto restricted networks
- restrict which central operators can issue invites or use the node SSH key
- rotate the dedicated central SSH key on a schedule and after any suspected compromise

For DGX-class or shared research infrastructure, strongly consider:

- separate bastion or access gateway instead of direct broad SSH exposure
- outbound-only reachability plus audited session brokering
- host-based allowlists for source addresses or mesh identities
- filesystem and job-runner permissions that limit blast radius from a single node account

## Safe Chat Backend

Not every `/v1/responses` backend has the same security posture.

- Preferred: a dedicated localhost chat bridge that runs Hermes with `enabled_toolsets=[]`.
- Acceptable: an external OpenAI-compatible gateway with server-side credentials.
- Unsafe by default: pointing `HERMES_WEBUI_PROXY_URL` at a general-purpose Hermes web UI instance that was built for operators, not enrolled nodes.

The repository refuses `HERMES_WEBUI_PROXY_URL` unless `HERMES_ALLOW_UNSAFE_WEBUI_PROXY=1` is set, so an operator has to opt into that risk explicitly.

## Open-Source Repo Hygiene

Do not commit:

- `.env` files with real secrets
- admin secrets
- model-provider API keys
- signing certificates or private keys
- production state files such as `invites.json`, `node_tokens.json`, or `nodes.json`
- SSH private keys

The checked-in examples and deployment templates should always be safe to publish without modification.

## Reporting A Vulnerability

If you discover a security issue, treat it as private until there is a fix and a deployment plan. Avoid posting working exploit details in public issues before operators have time to respond.
