from __future__ import annotations

import json
import urllib.request

from hermes.skills import Skill


class IssueRemoteInvite(Skill):
    name = "issue_remote_invite"
    description = "Issue a one-time Hermes Companion invite from the central enrollment API."

    async def execute(
        self,
        central_api_url: str,
        admin_secret: str,
        central_ssh_public_key: str,
        ssh_authorized_user: str = "",
        expires_in_minutes: int = 60,
        note: str | None = None,
        central_name: str = "Central Hermes",
        chat_model: str = "gpt-4.1-mini",
    ):
        payload = {
            "note": note,
            "expires_in_minutes": expires_in_minutes,
            "central_name": central_name,
            "chat_model": chat_model,
            "central_ssh_public_key": central_ssh_public_key,
            "ssh_authorized_user": ssh_authorized_user,
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
            return json.loads(response.read().decode("utf-8"))
