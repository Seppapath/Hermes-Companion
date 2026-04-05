#!/usr/bin/env python3

import importlib.util
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient


def load_server_module(root: Path):
    module_path = root / "central-api" / "server.py"
    spec = importlib.util.spec_from_file_location("hermes_companion_central_api_webui", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class StubState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.session_new_calls = []
        self.chat_calls = []
        self.next_session_id = "stub-session-1"


class StubHandler(BaseHTTPRequestHandler):
    state: StubState

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        return

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/auth/status":
            return self._json({"auth_enabled": False, "logged_in": False})
        return self._json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8") or "{}")

        if self.path == "/api/session/new":
            with self.state.lock:
                self.state.session_new_calls.append(body)
            return self._json(
                {
                    "session": {
                        "session_id": self.state.next_session_id,
                        "messages": [],
                    }
                }
            )

        if self.path == "/api/chat":
            with self.state.lock:
                self.state.chat_calls.append(body)
            return self._json(
                {
                    "answer": f"stub-answer:{body['session_id']}",
                    "status": "done",
                    "result": {"final_response": f"stub-answer:{body['session_id']}"},
                }
            )

        return self._json({"error": "not found"}, status=404)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    state = StubState()
    StubHandler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with tempfile.TemporaryDirectory(prefix="hermes-central-api-webui-smoke-") as tempdir:
            os.environ["HERMES_COMPANION_DATA_DIR"] = tempdir
            os.environ["HERMES_ADMIN_SECRET"] = "smoke-admin-secret"
            os.environ["HERMES_WEBUI_PROXY_URL"] = f"http://127.0.0.1:{server.server_port}"
            os.environ["HERMES_WEBUI_WORKSPACE"] = "/tmp"
            os.environ["HERMES_DEFAULT_CHAT_MODEL"] = "gpt-5.4-mini"
            os.environ["HERMES_ALLOW_UNSAFE_WEBUI_PROXY"] = "1"
            os.environ.pop("HERMES_PROXY_RESPONSES_URL", None)
            os.environ.pop("HERMES_PROXY_BEARER_TOKEN", None)

            module = load_server_module(root)
            client = TestClient(module.app)

            invite_response = client.post(
                "/api/device-invites",
                headers={"X-Hermes-Admin-Secret": "smoke-admin-secret"},
                json={
                    "central_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKstub hermes-central",
                },
            )
            assert invite_response.status_code == 200, invite_response.text
            invite = invite_response.json()

            redeem_response = client.post(
                "/api/device-invites/redeem",
                json={
                    "inviteCode": invite["inviteCode"],
                    "machine": {
                        "hostname": "bridge-node",
                        "osType": "linux",
                        "arch": "x86_64",
                        "currentUser": "bridge-user",
                    },
                },
            )
            assert redeem_response.status_code == 200, redeem_response.text
            bundle = redeem_response.json()
            token = bundle["apiToken"]
            assert bundle["chatModel"] == "gpt-5.4-mini", bundle

            for _ in range(2):
                response = client.post(
                    "/v1/responses",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": "openai/gpt-5.4-mini",
                        "input": [
                            {
                                "role": "system",
                                "content": [{"type": "input_text", "text": "You are central Hermes."}],
                            },
                            {
                                "role": "user",
                                "content": [{"type": "input_text", "text": "Say hello."}],
                            },
                        ],
                        "metadata": {"client_id": "bridge-client"},
                    },
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["output_text"] == "stub-answer:stub-session-1", payload

            assert len(state.session_new_calls) == 1, state.session_new_calls
            assert state.session_new_calls[0]["model"] == "gpt-5.4-mini", state.session_new_calls
            assert Path(state.session_new_calls[0]["workspace"]).name == "tmp", state.session_new_calls
            assert len(state.chat_calls) == 2, state.chat_calls
            assert all(call["session_id"] == "stub-session-1" for call in state.chat_calls), state.chat_calls
            assert "System context:" in state.chat_calls[0]["message"], state.chat_calls
            assert "User request:" in state.chat_calls[0]["message"], state.chat_calls
    finally:
        server.shutdown()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
