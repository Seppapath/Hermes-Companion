#!/usr/bin/env python3

import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient


MEM0_REQUESTS = {"search": [], "memories": [], "headers": []}
PROXY_REQUESTS = []


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


class MockMem0Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
        MEM0_REQUESTS["headers"].append(dict(self.headers))

        if self.path == "/search":
            MEM0_REQUESTS["search"].append(body)
            payload = {
                "results": [
                    {
                        "id": "memory-1",
                        "memory": "Johnson kitchen remodel is waiting on countertop fabrication.",
                    }
                ]
            }
        elif self.path == "/memories":
            MEM0_REQUESTS["memories"].append(body)
            payload = {"results": [{"id": "memory-2", "event": "ADD"}]}
        else:
            self.send_response(404)
            self.end_headers()
            return

        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        payload = {"status": "ok"}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


class MockProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
        PROXY_REQUESTS.append(body)

        payload = {
            "output_text": "proxy ok",
            "output": [
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "proxy ok"}],
                }
            ],
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def load_server_module(root: Path):
    module_path = root / "central-api" / "server.py"
    spec = importlib.util.spec_from_file_location("hermes_companion_central_api", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    mem0_port = reserve_port()
    proxy_port = reserve_port()

    mem0_server = ThreadingHTTPServer(("127.0.0.1", mem0_port), MockMem0Handler)
    mem0_thread = threading.Thread(target=mem0_server.serve_forever, daemon=True)
    mem0_thread.start()

    proxy_server = ThreadingHTTPServer(("127.0.0.1", proxy_port), MockProxyHandler)
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        with tempfile.TemporaryDirectory(prefix="hermes-central-api-smoke-") as tempdir:
            MEM0_REQUESTS["search"].clear()
            MEM0_REQUESTS["memories"].clear()
            MEM0_REQUESTS["headers"].clear()
            PROXY_REQUESTS.clear()

            os.environ["HERMES_COMPANION_DATA_DIR"] = tempdir
            os.environ["HERMES_ADMIN_SECRET"] = "smoke-admin-secret"
            os.environ["HERMES_PROXY_RESPONSES_URL"] = f"http://127.0.0.1:{proxy_port}/v1/responses"
            os.environ.pop("HERMES_PROXY_BEARER_TOKEN", None)
            os.environ["HERMES_MEM0_API_URL"] = f"http://127.0.0.1:{mem0_port}"
            os.environ["HERMES_MEM0_API_KEY"] = "smoke-mem0-key"
            os.environ["HERMES_MEMORY_USER_ID"] = "gabe"
            os.environ["HERMES_MEMORY_AGENT_ID"] = "central-hermes"

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
                    "model": "gpt-5",
                    "metadata": {
                        "client_id": "smoke-client",
                        "project_id": "johnson-kitchen",
                        "project_name": "Johnson Kitchen Remodel",
                    },
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "What should I tell the Johnsons?"}],
                        }
                    ],
                },
            )
            assert responses_response.status_code == 200, responses_response.text
            assert responses_response.json()["output_text"] == "proxy ok"
            deadline = time.time() + 5
            while time.time() < deadline and not MEM0_REQUESTS["memories"]:
                time.sleep(0.05)
            assert MEM0_REQUESTS["search"], "Mem0 search was not called"
            assert MEM0_REQUESTS["memories"], "Mem0 writeback was not called"
            assert PROXY_REQUESTS, "Upstream proxy was not called"
            assert MEM0_REQUESTS["search"][0]["user_id"] == "gabe"
            assert MEM0_REQUESTS["search"][0]["agent_id"] == "central-hermes"
            assert MEM0_REQUESTS["headers"][0]["X-API-Key"] == "smoke-mem0-key"
            proxy_input = PROXY_REQUESTS[0]["input"]
            assert proxy_input[0]["role"] == "system"
            assert "Johnson kitchen remodel is waiting on countertop fabrication." in proxy_input[0]["content"][0]["text"]
            write_payload = MEM0_REQUESTS["memories"][0]
            assert write_payload["user_id"] == "gabe"
            assert write_payload["agent_id"] == "central-hermes"
            assert write_payload["metadata"]["project_id"] == "johnson-kitchen"
            assert write_payload["messages"][0]["content"] == "What should I tell the Johnsons?"
            assert write_payload["messages"][1]["content"] == "proxy ok"

            memory_status_response = client.get(
                "/api/memory/status",
                headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
            )
            assert memory_status_response.status_code == 200, memory_status_response.text
            assert memory_status_response.json()["reachable"] is True

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
                json={"model": "gpt-5", "input": []},
            )
            assert rejected_response.status_code == 401, rejected_response.text

        return 0
    finally:
        mem0_server.shutdown()
        mem0_thread.join(timeout=5)
        proxy_server.shutdown()
        proxy_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
