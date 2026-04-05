#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def wait_for(predicate, timeout: float, description: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {description}")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def wait_for_json(path: Path, predicate, timeout: float, description: str):
    result = {}

    def matches() -> bool:
        nonlocal result
        data = read_json(path)
        if predicate(data):
            result = data
            return True
        return False

    wait_for(matches, timeout, description)
    return result


def fetch_json(url: str, *, method: str = "GET", payload=None, headers=None):
    request = urllib.request.Request(
        url,
        data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.getcode(), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") or "{}"
        forced_central_shutdown = False
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"detail": body}
        return error.code, payload
    except urllib.error.URLError as error:
        return 0, {"detail": str(error.reason)}


def fetch_node(base_url: str, node_id: str):
    status_code, payload = fetch_json(
        f"{base_url}/api/nodes",
        headers={"X-Hermes-Admin-Secret": "live-stack-admin-secret"},
    )
    if status_code != 200:
        return None
    return next((item for item in payload["nodes"] if item["node_id"] == node_id), None)


def machine_arch() -> str:
    if hasattr(os, "uname"):
        return os.uname().machine
    return os.environ.get("PROCESSOR_ARCHITECTURE", "unknown")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    python = os.environ.get("PYTHON", sys.executable)
    current_user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

    with tempfile.TemporaryDirectory(prefix="hermes-live-e2e-") as tempdir:
        temp = Path(tempdir)
        fake_home = temp / "home"
        fake_home.mkdir(parents=True, exist_ok=True)

        port = reserve_port()
        data_dir = temp / "central-data"
        env = {
            **os.environ,
            "HERMES_COMPANION_DATA_DIR": str(data_dir),
            "HERMES_ADMIN_SECRET": "live-stack-admin-secret",
        }

        central = subprocess.Popen(
            [
                python,
                "-m",
                "uvicorn",
                "--app-dir",
                "central-api",
                "server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        base_url = f"http://127.0.0.1:{port}"
        try:
            wait_for(
                lambda: fetch_json(f"{base_url}/api/health")[0] == 200,
                30,
                "central API health",
            )

            status_code, invite = fetch_json(
                f"{base_url}/api/device-invites",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Hermes-Admin-Secret": "live-stack-admin-secret",
                },
                payload={
                    "note": "Live stack E2E",
                    "expires_in_minutes": 30,
                    "central_name": "Live Hermes",
                    "central_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKhermescentraltestkey hermes-central",
                    "ssh_authorized_user": current_user,
                },
            )
            assert status_code == 200, invite

            status_code, bundle = fetch_json(
                f"{base_url}/api/device-invites/redeem",
                method="POST",
                headers={"Content-Type": "application/json"},
                payload={
                    "inviteCode": invite["inviteCode"],
                    "machine": {
                        "hostname": "live-stack-node",
                        "osType": sys.platform,
                        "arch": machine_arch(),
                        "currentUser": current_user,
                    },
                },
            )
            assert status_code == 200, bundle
            assert bundle["centralName"] == "Live Hermes"
            assert bundle["statusWsUrl"].startswith("ws://"), bundle
            assert bundle["chatWsUrl"].startswith("ws://"), bundle

            status_path = temp / "status" / "node-status.json"
            state_path = temp / "daemon" / "daemon-state.json"
            log_path = temp / "logs" / "daemon.log"
            private_key_path = temp / "keys" / "hermes_node_ed25519"
            public_key_path = temp / "keys" / "hermes_node_ed25519.pub"
            config_path = temp / "daemon" / "daemon-config.json"

            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "client_id": "live-stack-client",
                        "central_name": bundle["centralName"],
                        "registration_url": bundle["registrationUrl"],
                        "chat_http_url": bundle["chatHttpUrl"],
                        "chat_ws_url": bundle["chatWsUrl"],
                        "status_ws_url": bundle["statusWsUrl"],
                        "heartbeat_url": bundle["heartbeatUrl"],
                        "api_token": bundle["apiToken"],
                        "api_token_keyring_service": "",
                        "api_token_keyring_account": "",
                        "chat_model": bundle["chatModel"],
                        "node_name": "live-stack-node",
                        "central_ssh_public_key": bundle["centralSshPublicKey"],
                        "ssh_authorized_user": bundle["sshAuthorizedUser"] or current_user,
                        "heartbeat_interval_seconds": 1,
                        "retry_interval_seconds": 1,
                        "status_file": str(status_path),
                        "state_file": str(state_path),
                        "log_file": str(log_path),
                        "private_key_path": str(private_key_path),
                        "public_key_path": str(public_key_path),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            daemon_env = {
                **os.environ,
                "HOME": str(fake_home),
                "USERPROFILE": str(fake_home),
                "USER": current_user,
                "USERNAME": current_user,
            }
            if os.name == "nt" and fake_home.drive:
                daemon_env["HOMEDRIVE"] = fake_home.drive
                daemon_env["HOMEPATH"] = fake_home.as_posix().replace(fake_home.drive, "", 1)

            daemon = subprocess.Popen(
                [python, "daemon/hermes-node-daemon.py", "--config", str(config_path)],
                cwd=root,
                env=daemon_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                wait_for(lambda: status_path.exists(), 30, "daemon status file")
                wait_for(lambda: state_path.exists(), 30, "daemon state file")
                wait_for(
                    lambda: read_json(status_path).get("registered") is True,
                    30,
                    "registered status",
                )

                state_before = wait_for_json(
                    state_path,
                    lambda data: bool(data.get("node_id")) and bool(data.get("last_registration_at")),
                    30,
                    "stable daemon registration state",
                )
                status_before = wait_for_json(
                    status_path,
                    lambda data: data.get("registered") is True and bool(data.get("sshAuthorizedUser")),
                    30,
                    "stable daemon status state",
                )
                node_id = state_before["node_id"]
                registration_before = state_before["last_registration_at"]

                wait_for(
                    lambda: read_json(status_path).get("sshAccessConfigured") is True,
                    30,
                    "local SSH authorization",
                )

                authorized_keys = fake_home / ".ssh" / "authorized_keys"
                wait_for(authorized_keys.exists, 30, "authorized_keys creation")
                authorized_text = authorized_keys.read_text(encoding="utf-8")
                assert "hermes-central:live-stack-client" in authorized_text, authorized_text

                wait_for(
                    lambda: (fetch_node(base_url, node_id) or {}).get("connected") is True,
                    30,
                    "node websocket presence",
                )
                node = fetch_node(base_url, node_id)
                assert node is not None, {"node_id": node_id}
                assert node["connected"] is True, node
                assert node["ssh_authorized_user"] == current_user, node

                status_code, response_payload = fetch_json(
                    f"{base_url}/v1/responses",
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {bundle['apiToken']}",
                        "Content-Type": "application/json",
                    },
                    payload={
                        "model": bundle["chatModel"],
                        "input": [
                            {
                                "role": "user",
                                "content": [{"type": "input_text", "text": "live e2e status"}],
                            }
                        ],
                    },
                )
                assert status_code == 200, response_payload
                assert "live e2e status" in response_payload["output_text"], response_payload

                status_code, reregister_payload = fetch_json(
                    f"{base_url}/api/nodes/{node_id}/reregister",
                    method="POST",
                    headers={"X-Hermes-Admin-Secret": "live-stack-admin-secret"},
                )
                assert status_code == 200, reregister_payload

                wait_for(
                    lambda: read_json(state_path).get("last_registration_at") != registration_before,
                    30,
                    "re-registration timestamp",
                )

                status_code, revoke_payload = fetch_json(
                    f"{base_url}/api/nodes/{node_id}/revoke",
                    method="POST",
                    headers={"X-Hermes-Admin-Secret": "live-stack-admin-secret"},
                )
                assert status_code == 200, revoke_payload

                wait_for(
                    lambda: read_json(status_path).get("state") == "revoked"
                    or daemon.poll() is not None,
                    30,
                    "post-revoke daemon state",
                )

                wait_for(
                    lambda: (not authorized_keys.exists())
                    or "hermes-central:live-stack-client"
                    not in authorized_keys.read_text(encoding="utf-8"),
                    30,
                    "authorized_keys cleanup",
                )

                status_after = read_json(status_path)
                assert status_after["state"] == "revoked", status_after
                last_error = (status_after.get("lastError") or "").lower()
                assert "revoked" in last_error or "invalid node token" in last_error, status_after
                assert status_after["registered"] is False, status_after
                assert status_after["sshAccessConfigured"] is False, status_after
                assert status_before["sshAuthorizedUser"] == current_user, status_before
            finally:
                forced_shutdown = False
                if daemon.poll() is None:
                    forced_shutdown = True
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        daemon.kill()
                        daemon.wait(timeout=10)

                stderr = daemon.stderr.read() if daemon.stderr else ""
                stdout = daemon.stdout.read() if daemon.stdout else ""
                if not forced_shutdown and daemon.returncode not in (0, None):
                    raise RuntimeError(
                        f"Daemon exited unexpectedly with {daemon.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                    )
        finally:
            if central.poll() is None:
                forced_central_shutdown = True
                central.terminate()
                try:
                    central.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    central.kill()
                    central.wait(timeout=10)

            stderr = central.stderr.read() if central.stderr else ""
            stdout = central.stdout.read() if central.stdout else ""
            if not forced_central_shutdown and central.returncode not in (0, -15):
                raise RuntimeError(
                    f"Central API exited unexpectedly with {central.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
