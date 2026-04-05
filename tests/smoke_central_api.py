#!/usr/bin/env python3

import importlib.util
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def load_server_module(root: Path):
    module_path = root / "central-api" / "server.py"
    spec = importlib.util.spec_from_file_location("hermes_companion_central_api", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory(prefix="hermes-central-api-smoke-") as tempdir:
        os.environ["HERMES_COMPANION_DATA_DIR"] = tempdir
        os.environ["HERMES_ADMIN_SECRET"] = "smoke-admin-secret"
        os.environ.pop("HERMES_PROXY_RESPONSES_URL", None)
        os.environ.pop("HERMES_PROXY_BEARER_TOKEN", None)

        Path(tempdir, "invites.json").write_text(
            json.dumps(
                {
                    "legacy-invite": {
                        "id": "legacy-invite",
                        "code": "legacy-invite-code",
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )
        Path(tempdir, "node_tokens.json").write_text(
            json.dumps(
                {
                    "legacy-token": {
                        "id": "legacy-token",
                        "token": "legacy-node-token",
                        "invite_id": "legacy-invite",
                        "issued_at": "2099-01-01T00:00:00+00:00",
                        "revoked_at": None,
                    }
                }
            ),
            encoding="utf-8",
        )

        server = load_server_module(root)
        client = TestClient(server.app)

        migrated_invites = json.loads(Path(tempdir, "invites.json").read_text(encoding="utf-8"))
        migrated_tokens = json.loads(Path(tempdir, "node_tokens.json").read_text(encoding="utf-8"))
        assert "code" not in migrated_invites["legacy-invite"], migrated_invites
        assert "code_sha256" in migrated_invites["legacy-invite"], migrated_invites
        assert "token" not in migrated_tokens["legacy-token"], migrated_tokens
        assert "token_sha256" in migrated_tokens["legacy-token"], migrated_tokens

        invite_response = client.post(
            "/api/device-invites",
            headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
            json={
                "note": "Smoke invite",
                "expires_in_minutes": 30,
                "central_name": "Smoke Hermes",
                "central_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKhermescentraltestkey hermes-central",
            },
        )
        assert invite_response.status_code == 200, invite_response.text
        invite = invite_response.json()
        invites_path = Path(tempdir) / "invites.json"
        stored_invites = json.loads(invites_path.read_text(encoding="utf-8"))
        invite_record = stored_invites[invite["inviteId"]]
        assert "code_sha256" in invite_record, invite_record
        assert invite["inviteCode"] not in invites_path.read_text(encoding="utf-8")

        redeem_response = client.post(
            "/api/device-invites/redeem",
            json={
                "inviteCode": invite["inviteCode"],
                "machine": {
                    "hostname": "smoke-node",
                    "osType": "macos",
                    "arch": "arm64",
                    "currentUser": "smoke-user",
                },
            },
        )
        assert redeem_response.status_code == 200, redeem_response.text
        bundle = redeem_response.json()
        token = bundle["apiToken"]
        assert bundle["statusWsUrl"].startswith("ws://"), bundle
        assert bundle["chatWsUrl"].startswith("ws://"), bundle
        tokens_path = Path(tempdir) / "node_tokens.json"
        stored_tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
        token_record = next(iter(stored_tokens.values()))
        assert "token_sha256" in token_record, token_record
        assert token not in tokens_path.read_text(encoding="utf-8")

        register_response = client.post(
            "/api/register-node",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "client_id": "smoke-client",
                "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICsmokepublickey smoke-node",
                "hostname": "smoke-node",
                "os_type": "macOS-14",
                "arch": "arm64",
                "fingerprint": "deadbeefcafebabe",
                "requested_user": "smoke-user",
                "client_version": "0.1.0",
            },
        )
        assert register_response.status_code == 200, register_response.text
        node_id = register_response.json()["node_id"]

        heartbeat_response = client.post(
            "/api/node-heartbeat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "node_id": node_id,
                "client_id": "smoke-client",
                "hostname": "smoke-node",
                "timestamp": "2026-04-04T12:00:00+00:00",
            },
        )
        assert heartbeat_response.status_code == 200, heartbeat_response.text

        responses_response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-4.1-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "status check"}],
                    }
                ],
            },
        )
        assert responses_response.status_code == 200, responses_response.text
        assert "status check" in responses_response.json()["output_text"]

        with client.websocket_connect(
            "/ws/nodes",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            websocket.send_json(
                {
                    "type": "node-status",
                    "node_id": node_id,
                    "client_id": "smoke-client",
                    "hostname": "smoke-node",
                }
            )
            ack = websocket.receive_json()
            assert ack["type"] == "ack"

            list_response = client.get(
                "/api/nodes",
                headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
            )
            assert list_response.status_code == 200, list_response.text
            nodes = list_response.json()["nodes"]
            assert any(node["node_id"] == node_id and node["connected"] for node in nodes)

            reregister_response = client.post(
                f"/api/nodes/{node_id}/reregister",
                headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
            )
            assert reregister_response.status_code == 200, reregister_response.text
            instruction = websocket.receive_json()
            assert instruction["action"] == "reregister"

        with client.websocket_connect(
            "/ws/chat",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            greeting = websocket.receive_json()
            assert greeting["type"] == "assistant"

        with client.websocket_connect(
            "/ws/nodes",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            websocket.send_json(
                {
                    "type": "node-status",
                    "node_id": node_id,
                    "client_id": "smoke-client",
                    "hostname": "smoke-node",
                }
            )
            ack = websocket.receive_json()
            assert ack["type"] == "ack"

            revoke_response = client.post(
                f"/api/nodes/{node_id}/revoke",
                headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
            )
            assert revoke_response.status_code == 200, revoke_response.text
            instruction = websocket.receive_json()
            assert instruction["action"] == "revoke"
            assert instruction["nodeId"] == node_id

        rejected_response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-4.1-mini", "input": []},
        )
        assert rejected_response.status_code == 401, rejected_response.text

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
