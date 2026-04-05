#!/usr/bin/env python3

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websockets


REQUESTS = {"register": [], "heartbeat": []}
WS_MESSAGES = []
REREGISTER_SENT = asyncio.Event()
REVOKE_SENT = asyncio.Event()


class MockHermesHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(body or "{}")

        if self.path == "/register-node":
            REQUESTS["register"].append(payload)
            response = {"node_id": f"node-{len(REQUESTS['register'])}"}
        elif self.path == "/heartbeat":
            REQUESTS["heartbeat"].append(payload)
            response = {"ok": True}
        else:
            self.send_response(404)
            self.end_headers()
            return

        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


async def websocket_handler(connection):
    try:
        async for raw_message in connection:
            payload = json.loads(raw_message)
            WS_MESSAGES.append(payload)
            if not REREGISTER_SENT.is_set():
                await connection.send(json.dumps({"action": "reregister"}))
                REREGISTER_SENT.set()
            elif not REVOKE_SENT.is_set() and payload.get("node_id") == "node-2":
                await connection.send(json.dumps({"action": "revoke"}))
                REVOKE_SENT.set()
    except websockets.ConnectionClosed:
        return


async def wait_for(predicate, timeout, description):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {description}")


async def main():
    python = os.environ.get("PYTHON", sys.executable)

    with tempfile.TemporaryDirectory(prefix="hermes-companion-smoke-") as tempdir:
        temp = Path(tempdir)
        fake_home = temp / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        status_path = temp / "status" / "node-status.json"
        state_path = temp / "daemon" / "daemon-state.json"
        log_path = temp / "logs" / "daemon.log"
        private_key_path = temp / "keys" / "hermes_node_ed25519"
        public_key_path = temp / "keys" / "hermes_node_ed25519.pub"
        authorized_keys_path = fake_home / ".ssh" / "authorized_keys"
        config_path = temp / "daemon" / "daemon-config.json"

        http_server = ThreadingHTTPServer(("127.0.0.1", 0), MockHermesHandler)
        http_port = http_server.server_address[1]
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()

        ws_server = await websockets.serve(websocket_handler, "127.0.0.1", 0)
        ws_port = ws_server.sockets[0].getsockname()[1]

        config = {
            "version": "0.1.0",
            "client_id": "smoke-client",
            "central_name": "Smoke Hermes",
            "registration_url": f"http://127.0.0.1:{http_port}/register-node",
            "chat_http_url": "",
            "chat_ws_url": "",
            "status_ws_url": f"ws://127.0.0.1:{ws_port}",
            "heartbeat_url": f"http://127.0.0.1:{http_port}/heartbeat",
            "api_token": "",
            "chat_model": "gpt-4.1-mini",
            "node_name": "smoke-node",
            "central_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKhermescentraltestkey hermes-central",
            "ssh_authorized_user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
            "heartbeat_interval_seconds": 1,
            "retry_interval_seconds": 1,
            "status_file": str(status_path),
            "state_file": str(state_path),
            "log_file": str(log_path),
            "private_key_path": str(private_key_path),
            "public_key_path": str(public_key_path),
        }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        process_env = {
            **os.environ,
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
        }
        if os.name == "nt" and fake_home.drive:
            process_env["HOMEDRIVE"] = fake_home.drive
            process_env["HOMEPATH"] = fake_home.as_posix().replace(fake_home.drive, "", 1)

        process = subprocess.Popen(
            [python, "daemon/hermes-node-daemon.py", "--config", str(config_path)],
            cwd=Path(__file__).resolve().parents[1],
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            await wait_for(lambda: len(REQUESTS["register"]) >= 1, 20, "initial registration")
            await wait_for(lambda: status_path.exists(), 20, "status file creation")
            await wait_for(lambda: len(REQUESTS["heartbeat"]) >= 1, 20, "heartbeat")
            await wait_for(lambda: len(WS_MESSAGES) >= 1, 20, "websocket status messages")
            await wait_for(
                lambda: len(REQUESTS["register"]) >= 2,
                20,
                "re-registration after websocket request",
            )
            await wait_for(lambda: REVOKE_SENT.is_set(), 20, "revoke instruction")
            await wait_for(
                lambda: process.poll() is not None,
                20,
                "daemon shutdown after revoke",
            )

            status = json.loads(status_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            authorized_keys = (
                authorized_keys_path.read_text(encoding="utf-8")
                if authorized_keys_path.exists()
                else ""
            )

            assert private_key_path.exists(), "private key was not generated"
            assert public_key_path.exists(), "public key was not generated"
            assert REQUESTS["register"][0]["public_key"].startswith("ssh-ed25519 "), "public key payload was not OpenSSH ed25519"
            assert status["state"] == "revoked", "status file did not show a revoked node"
            assert status["registered"] is False, "revoked node still appeared registered"
            assert status["sshAccessConfigured"] is False, "revoked node kept SSH access configured"
            assert state.get("revoked_at"), "daemon did not persist revocation state"
            assert state.get("node_id") is None, "daemon kept the revoked node id"
            assert state["ssh_authorized_user"] is None, "daemon did not clear the authorized SSH user"
            assert "hermes-central:smoke-client" not in authorized_keys, "managed SSH authorization was not removed on revoke"
            assert any(message["type"] == "node-status" for message in WS_MESSAGES), "websocket payloads missing node-status frames"
        finally:
            forced_shutdown = False
            if process.poll() is None:
                forced_shutdown = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

            stderr = process.stderr.read() if process.stderr else ""
            stdout = process.stdout.read() if process.stdout else ""
            if not forced_shutdown and process.returncode != 0:
                raise RuntimeError(
                    f"Daemon exited unexpectedly with {process.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                )

            ws_server.close()
            await ws_server.wait_closed()
            http_server.shutdown()
            http_thread.join(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
