# Simple Onboarding Plan

This document describes the simplest public onboarding story for Hermes
Companion.

## What Hermes Companion Is

Hermes Companion has two parts:

1. A central control plane
   - runs on a server you control
   - issues one-time invites
   - registers and tracks nodes
   - handles revocation and chat routing

2. A local desktop app
   - runs on each machine you want to enroll
   - installs a per-user background daemon
   - generates a local SSH keypair
   - installs the central SSH public key for the approved local account
   - maintains the outbound connection back to central

## The Three Supported Setup Paths

1. Join an existing Hermes
   - install the desktop app
   - paste a one-time invite
   - click `Connect`

2. I run central Hermes
   - install the desktop app
   - enter the public central URL
   - enter the admin secret
   - paste the central SSH public key
   - click `Create Invite + Connect`

3. Create my Hermes server
   - open the setup wizard in the desktop app
   - generate a server setup pack
   - follow the generated checklist on a Linux server
   - return to the desktop app and connect the machine

## Operator Experience We Want

The happy path should feel like this:

1. Open Hermes Companion.
2. Pick the setup path that matches your situation.
3. Follow the next button or checklist.
4. End with a machine that appears online in central Hermes.

## Security Model In Plain English

The good news:

- enrollment is invite-based
- tokens are revocable
- secrets are minted at runtime instead of being baked into the installer
- the node private key stays on the node
- the public repo can stay public

The important truth:

- the central server is still a real server that must be operated carefully
- a powerful local account gives Hermes broader reach on that machine
- production use still needs HTTPS, strong secrets, and a dedicated central SSH key

## What “Done” Looks Like

Hermes Companion is in a good onboarding state when:

- a beginner can join an existing Hermes with only an invite
- an owner-operator can connect their own machine with only a URL, admin secret, and SSH public key
- the app can generate a server setup pack for a fresh Linux deployment
- advanced settings are optional, not required for normal onboarding
