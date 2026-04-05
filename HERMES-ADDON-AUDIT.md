# Hermes Add-on Audit

## Goal

Identify the highest-value Hermes capabilities to adopt next without creating
overlapping memory systems or an unstable operator stack.

This audit is optimized for:

- one central Hermes identity
- multiple devices
- multiple client projects
- communication follow-up
- task orchestration
- production-safe rollout

## Decision Rule

Do not install add-ons just because they exist.

Adopt only the capabilities that:

- strengthen the central-server architecture
- reduce real operator work
- do not duplicate memory responsibilities
- can be audited and controlled

## Tier 1: Adopt Soon

### 1. Mem0 OSS

Why:

- becomes the canonical long-term memory layer
- centralizes identity across Windows and macOS
- supports user, agent, session, and graph-style memory

Status:

- approved as the memory foundation

### 2. Context Files

Why:

- gives Hermes durable per-project instructions
- works well for project-specific conventions without polluting long-term memory
- should be used for workspace rules, not personal memory

Use for:

- project conventions
- file locations
- operating instructions
- client-specific workflow rules inside each project folder

### 3. Scheduled Tasks (Cron)

Why:

- high leverage for reminders, reports, follow-ups, and recurring checks
- useful for running unattended operational routines
- natural fit for contractor/client workflows

Use for:

- morning briefings
- overdue follow-up reminders
- invoice reminders
- daily project status summaries

### 4. Webhooks

Why:

- lets external systems trigger Hermes automatically
- ideal bridge from GitHub, GitLab, JIRA, Stripe, and similar systems
- better than polling for many event-driven workflows

Use for:

- new issue or PR events
- payment/invoice events
- future CRM or project system triggers

### 5. MCP Integrations

Why:

- clean way to add tools without rewriting Hermes
- broad ecosystem
- can be added selectively per need

Best initial MCP targets:

- GitHub
- filesystem
- git
- calendar
- email-related connectors if we choose a supported provider later

## Tier 2: Add After Memory Is Stable

### 6. Messaging Gateway

Why:

- Hermes can live in Telegram, Signal, Slack, WhatsApp, Email, and CLI
- gives you access from anywhere
- useful after central memory is in place

Recommended first platform:

- Telegram or Signal

Reason:

- easy remote access
- strong operator convenience
- useful for reminders and quick client/project questions

### 7. API Server

Why:

- exposes Hermes as an OpenAI-compatible endpoint
- useful if we later want custom UIs or other frontends

Reason to wait:

- you already have a central control plane
- adding more API surfaces before memory is stable adds risk

### 8. Skills System Expansion

Why:

- great for repeatable workflows
- lets Hermes load domain-specific procedures on demand

Use for:

- estimate drafting
- client update generation
- permit follow-up workflow
- material ordering checklist

## Tier 3: Defer For Now

### 9. Honcho Memory

Why defer:

- it is another memory provider
- it overlaps with the Mem0 decision
- running multiple cross-session memory systems risks conflicting identities

Decision:

- do not add Honcho while Mem0 becomes the system of record

### 10. Browser Automation

Why defer:

- powerful, but not foundational
- introduces more operational and security complexity
- best added after core memory, automations, and messaging are solid

Use later for:

- web portals without APIs
- supplier lookups
- form automation

### 11. Voice Mode

Why defer:

- not critical for project and client operations
- nice-to-have after core workflow reliability is proven

## Recommended Rollout

### Phase A

- Mem0 OSS
- Context Files
- Cron

### Phase B

- Webhooks
- selected MCP servers

### Phase C

- Messaging gateway
- custom workflow skills

### Phase D

- browser automation
- voice

## Key Rule

There should be one canonical Hermes memory system.

For this stack, that means:

- Mem0 for central long-term memory
- Context Files for project-local instructions
- Skills for repeatable procedures
- Cron for recurring work
- Webhooks and MCP for external connectivity

Everything else should be measured against that architecture.
