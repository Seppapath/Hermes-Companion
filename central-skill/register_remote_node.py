from __future__ import annotations

import json
import urllib.request

from hermes.skills import Skill


class RegisterRemoteNode(Skill):
    name = "register_remote_node"
    description = (
        "Create a one-time enrollment invite for Hermes Companion. "
        "This replaces the older direct public-key registration flow."
    )

    async def execute(
        self,
        central_api_url: str,
        admin_secret: str,
        central_ssh_public_key: str,
        hostname: str | None = None,
        requested_user: str | None = None,
        expires_in_minutes: int = 60,
        note: str | None = None,
        central_name: str = "Central Hermes",
        chat_model: str = "gpt-4.1-mini",
    ):
        resolved_note = note or "Manual enrollment invite"
        if hostname:
            resolved_note += f" for {hostname}"
        if requested_user:
            resolved_note += f" ({requested_user})"

        payload = {
            "note": resolved_note,
            "expires_in_minutes": expires_in_minutes,
            "central_name": central_name,
            "chat_model": chat_model,
            "central_ssh_public_key": central_ssh_public_key,
            "ssh_authorized_user": requested_user or "",
        }

        request = urllib.request.Request(
            central_api_url.rstrip("/") + "/api/device-invites",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Hermes-Admin-Secret": admin_secret,
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            invite = json.loads(response.read().decode("utf-8"))

        await self.memory.save(
            f"Issued Hermes Companion invite {invite['inviteId']} for {hostname or 'an unassigned node'}."
        )
        return invite
