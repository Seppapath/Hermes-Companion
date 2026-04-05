# Hermes Central Memory Architecture

## Goal

Give Hermes one persistent identity across all enrolled devices by moving durable
memory to the central server.

Hermes should remember:

- people
- clients
- projects
- active jobs
- commitments
- email and message summaries
- operating preferences
- running context across Windows, macOS, and future devices

The same Hermes should answer consistently no matter which machine is used to
reach the central server.

## Decision

Use `Mem0 OSS` as the first central memory system for Hermes.

Reasons:

- it is self-hostable
- it exposes a server-side API
- it supports shared memory scoped by `user`, `agent`, `session`, and `run`
- it supports graph memory for relationships between people, projects, and events
- it fits our current central API architecture without forcing a full agent-platform rewrite

## Deployment Shape

Hermes memory lives on the central server, not on enrolled nodes.

Recommended first production topology:

1. `central-api`
   - existing FastAPI control plane
   - continues handling invites, registration, node tokens, chat proxying, and node state

2. `chat-bridge`
   - existing safe chat backend
   - remains the controlled runtime that talks to the operator-side model/provider

3. `mem0-api`
   - new internal memory service
   - stores long-term memories and serves retrieval results to central Hermes

4. `neo4j`
   - graph backend for relationship-aware memory
   - stores links such as person -> company, client -> project, project -> status, promise -> due date

5. optional vector backend
   - depending on the chosen Mem0 OSS stack configuration
   - can begin with the simplest supported configuration and evolve later

All of these should run on the DigitalOcean central host or on tightly-coupled
private infrastructure behind the same operator boundary.

## Identity Model

Hermes should use stable identifiers so memory follows the operator, not the machine.

Recommended scopes:

- `user_id = gabe`
  - the human principal Hermes serves
- `agent_id = central-hermes`
  - the canonical central Hermes identity
- `app_id = hermes-companion`
  - the product/application layer
- `session_id`
  - one active chat/thread/session
- `run_id`
  - one tool execution or task burst

This means:

- Windows and Mac both address the same `user_id`
- both devices talk to the same `agent_id`
- local device state stays local, but durable memory stays central

## Memory Classes

Hermes needs more than one kind of memory.

### 1. Profile Memory

Stores durable user facts and preferences:

- name
- preferred working style
- business context
- communication preferences
- recurring constraints

Examples:

- Gabe runs multiple contractor/client jobs at once.
- Gabe prefers direct summaries and concrete next steps.
- Gabe uses Hermes across multiple computers.

### 2. People Memory

Stores information about:

- clients
- vendors
- subcontractors
- teammates
- family or personal contacts if desired

Examples:

- Johnson family is a remodel client.
- Mike handles electrical work.
- Sarah prefers texts instead of long emails.

### 3. Project Memory

Stores project-level facts:

- project names
- active status
- locations
- budgets
- blockers
- materials
- deadlines

Examples:

- Johnson kitchen remodel is waiting on countertop fabrication.
- Elm Street estimate was sent on April 3, 2026.

### 4. Communication Memory

Stores summarized communication events:

- inbound emails
- outbound emails
- call summaries
- meeting notes
- message threads
- promises and follow-ups

Examples:

- On April 5, 2026, Gabe promised the Johnsons an update by Tuesday.
- Electrician said he can start after permit approval.

### 5. Task Memory

Stores operational commitments:

- next actions
- reminders
- open loops
- pending approvals
- handoffs

Examples:

- Follow up with supplier on tile ETA.
- Draft invoice for final payment.

## Retrieval Strategy

Hermes should not dump raw memory into every prompt.

Instead:

1. incoming user message arrives at central Hermes
2. central Hermes queries Mem0 for relevant memories
3. central Hermes injects only the best matching memories into the model context
4. after the response, Hermes writes back any durable facts and communication summaries

This keeps:

- latency controlled
- prompts smaller
- memory selective instead of noisy

## Write Policy

Not every message should become permanent memory.

Write durable memory only for:

- explicit personal preferences
- client or vendor facts
- project state changes
- commitments
- deadlines
- contact details
- repeated stable patterns

Do not write durable memory for:

- transient filler chat
- one-off draft wording that has no lasting value
- sensitive secrets unless deliberately supported and encrypted

## Source of Truth Rules

To avoid Hermes inventing or mutating records:

- structured project systems should win over casual chat memory
- communication logs should keep timestamps
- memory entries should preserve provenance where possible
- when Hermes is unsure, it should cite uncertainty instead of flattening conflicting facts

Recommended provenance fields:

- source type
- timestamp
- project id
- person id
- device id
- message or event reference

## Where This Fits In The Current Stack

Current request path:

1. enrolled node sends request to `central-api /v1/responses`
2. `central-api` proxies to the safe chat backend
3. backend produces an answer

Target request path:

1. enrolled node sends request to `central-api /v1/responses`
2. `central-api` authenticates the node token
3. `central-api` resolves the stable Hermes memory identity
4. `central-api` asks Mem0 for relevant memories
5. `central-api` augments the prompt or request metadata
6. request goes to the safe chat backend
7. response returns
8. central Hermes writes new durable memory back to Mem0 asynchronously

This allows memory to stay central without exposing the memory system directly to each device.

## Recommended Phase Plan

### Phase 1

Stand up Mem0 OSS centrally and integrate retrieval/writeback for chat.

Deliverables:

- internal Mem0 service on the central server
- stable user and agent identifiers
- memory read before response
- memory write after response
- basic profile and project memory categories

### Phase 2

Enable graph memory with Neo4j for relationships.

Deliverables:

- person/project/client linking
- relationship-aware recall
- better follow-up and commitment tracking

### Phase 3

Add communication ingestion and task extraction.

Deliverables:

- email summaries
- client communication timeline
- follow-up reminders
- project activity digest

### Phase 4

Add operator tools for reviewing and correcting memory.

Deliverables:

- memory inspection UI or admin endpoints
- delete/edit/merge memory records
- audit trail for important memory writes

## Security Notes

- keep Mem0 internal to the central host or private network
- never expose admin mutation routes publicly without authentication
- do not store plaintext secrets in memory
- define a separate policy for sensitive financial or personal records
- back up memory state like any other business system

## Recommendation

Build Hermes memory as a central service, not as local per-device memory.

For this stack, the best first implementation is:

- `Mem0 OSS`
- central-only deployment
- stable `user_id` and `agent_id`
- async writeback
- graph memory enabled in the next step with `Neo4j`

That gives Hermes continuity across devices without forcing a full platform rewrite.
