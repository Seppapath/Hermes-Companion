#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import keyring


REQUESTS = {"register": []}
EXPECTED_TOKEN = ""


class MockHermesHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/register-node":
            self.send_response(404)
            self.end_headers()
            return

        auth_header = self.headers.get("Authorization", "")
        if auth_header != f"Bearer {EXPECTED_TOKEN}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail":"missing or invalid bearer token"}')
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        REQUESTS["register"].append(json.loads(body or "{}"))
        encoded = b'{"node_id":"secure-store-node"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


async def wait_for(predicate, timeout, description):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {description}")


async def main():
    global EXPECTED_TOKEN
    python = os.environ.get("PYTHON", sys.executable)
    EXPECTED_TOKEN = "secure-store-token"
    service = "com.hermes.companion.api-token"
    account = "secure-store-client"

    keyring.set_password(service, account, EXPECTED_TOKEN)

    try:
        with tempfile.TemporaryDirectory(prefix="hermes-companion-secure-store-") as tempdir:
            temp = Path(tempdir)

            status_path = temp / "status" / "node-status.json"
            state_path = temp / "daemon" / "daemon-state.json"
            log_path = temp / "logs" / "daemon.log"
            private_key_path = temp / "keys" / "hermes_node_ed25519"
            public_key_path = temp / "keys" / "hermes_node_ed25519.pub"
            config_path = temp / "daemon" / "daemon-config.json"

            http_server = ThreadingHTTPServer(("127.0.0.1", 0), MockHermesHandler)
            http_port = http_server.server_address[1]
            http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            http_thread.start()

            config = {
                "version": "0.1.0",
                "client_id": account,
                "central_name": "Secure Store Hermes",
                "registration_url": f"http://127.0.0.1:{http_port}/register-node",
                "chat_http_url": "",
                "chat_ws_url": "",
                "status_ws_url": "",
                "heartbeat_url": "",
                "api_token": "",
                "api_token_keyring_service": service,
                "api_token_keyring_account": account,
                "chat_model": "gpt-4.1-mini",
                "node_name": "secure-store-node",
                "central_ssh_public_key": "",
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

            python_dir = str(Path(python).resolve().parent)
            path_env = f"{python_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            process = subprocess.Popen(
                [python, "daemon/hermes-node-daemon.py", "--config", str(config_path), "--once"],
                cwd=Path(__file__).resolve().parents[1],
                env={
                    **os.environ,
                    "VIRTUAL_ENV": str(Path(python).resolve().parents[1]),
                    "PATH": path_env,
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                await wait_for(lambda: len(REQUESTS["register"]) == 1, 20, "secure-store registration")
                await wait_for(lambda: status_path.exists(), 20, "status file creation")

                status = json.loads(status_path.read_text(encoding="utf-8"))
                assert status["registered"] is True, status
                assert REQUESTS["register"][0]["client_id"] == account, REQUESTS
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()

                stderr = process.stderr.read() if process.stderr else ""
                stdout = process.stdout.read() if process.stdout else ""
                if process.returncode not in (0, -signal.SIGTERM):
                    raise RuntimeError(
                        f"Daemon exited unexpectedly with {process.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                    )

                http_server.shutdown()
                http_thread.join(timeout=5)
    finally:
        try:
            keyring.delete_password(service, account)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
