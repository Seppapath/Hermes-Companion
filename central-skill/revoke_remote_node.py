from __future__ import annotations

import json
import urllib.request

from hermes.skills import Skill


class RevokeRemoteNode(Skill):
    name = "revoke_remote_node"
    description = "Revoke a Hermes Companion node through the central enrollment API."

    async def execute(self, central_api_url: str, admin_secret: str, node_id: str):
        request = urllib.request.Request(
            central_api_url.rstrip("/") + f"/api/nodes/{node_id}/revoke",
            headers={"X-Hermes-Admin-Secret": admin_secret},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        await self.memory.save(f"Revoked Hermes Companion node {node_id}.")
        return payload
