from __future__ import annotations

import json
import urllib.request

from hermes.skills import Skill


class ListRemoteNodes(Skill):
    name = "list_remote_nodes"
    description = "List enrolled Hermes Companion nodes from the central enrollment API."

    async def execute(self, central_api_url: str, admin_secret: str):
        request = urllib.request.Request(
            central_api_url.rstrip("/") + "/api/nodes",
            headers={"X-Hermes-Admin-Secret": admin_secret},
            method="GET",
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        return payload.get("nodes", [])
