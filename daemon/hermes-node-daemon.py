#!/usr/bin/env python3

import argparse
import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import websockets
try:
    import keyring
except ImportError:  # pragma: no cover - optional at runtime
    keyring = None

try:
    import pwd
except ImportError:  # pragma: no cover - unavailable on Windows
    pwd = None


DAEMON_VERSION = "0.1.0"


class ReregisterRequested(RuntimeError):
    pass


class NodeRevoked(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def chmod_if_possible(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except PermissionError:
        return


@dataclass
class RuntimeConfig:
    version: str
    client_id: str
    central_name: str
    registration_url: str
    chat_http_url: str
    chat_ws_url: str
    status_ws_url: str
    heartbeat_url: str
    api_token: str
    chat_model: str
    node_name: str
    central_ssh_public_key: str
    ssh_authorized_user: str
    heartbeat_interval_seconds: int
    retry_interval_seconds: int
    status_file: str
    state_file: str
    log_file: str
    private_key_path: str
    public_key_path: str
    api_token_keyring_service: str = ""
    api_token_keyring_account: str = ""

    @classmethod
    def load(cls, config_path: Path) -> "RuntimeConfig":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(**payload)


class HermesNodeDaemon:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.status_path = Path(config.status_file)
        self.state_path = Path(config.state_file)
        self.log_path = Path(config.log_file)
        self.private_key_path = Path(config.private_key_path)
        self.public_key_path = Path(config.public_key_path)
        self.hostname = socket.gethostname()
        self.shutdown_event = asyncio.Event()
        self.logger = self._build_logger()
        self.state = self._load_state()

    def _build_logger(self) -> logging.Logger:
        ensure_parent(self.log_path)
        logger = logging.getLogger("hermes-node-daemon")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        handler = RotatingFileHandler(self.log_path, maxBytes=512_000, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        return logger

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.logger.warning("State file was invalid JSON, starting fresh")
        return {}

    def _save_state(self) -> None:
        write_json(self.state_path, self.state)

    def _status_payload(
        self,
        *,
        state: str,
        registered: Optional[bool] = None,
        last_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "state": state,
            "registered": self.state.get("node_id") is not None if registered is None else registered,
            "nodeId": self.state.get("node_id"),
            "lastRegistrationAt": self.state.get("last_registration_at"),
            "lastHeartbeatAt": self.state.get("last_heartbeat_at"),
            "lastError": last_error,
            "daemonVersion": DAEMON_VERSION,
            "publicKeyPath": str(self.public_key_path),
            "serviceMode": self._service_mode(),
            "sshAccessConfigured": bool(self.state.get("ssh_access_configured")),
            "sshAuthorizedUser": self.state.get("ssh_authorized_user"),
        }

    def _write_status(self, **kwargs: Any) -> None:
        payload = self._status_payload(**kwargs)
        write_json(self.status_path, payload)

    def _service_mode(self) -> str:
        if sys.platform == "darwin":
            return "launch-agent"
        if os.name == "nt":
            return "startup-folder"
        if sys.platform.startswith("linux"):
            return "systemd-user"
        return "manual"

    def ensure_keypair(self) -> None:
        if self.private_key_path.exists() and self.public_key_path.exists():
            return

        ensure_parent(self.private_key_path)
        ensure_parent(self.public_key_path)

        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )

        self.private_key_path.write_bytes(private_bytes)
        self.public_key_path.write_bytes(public_bytes + b"\n")
        chmod_if_possible(self.private_key_path, 0o600)
        chmod_if_possible(self.public_key_path, 0o644)
        self.logger.info("Generated local Ed25519 keypair")

    def ensure_central_ssh_access(self) -> None:
        ssh_key = self.config.central_ssh_public_key.strip()
        if not ssh_key:
            self.remove_central_ssh_access()
            return

        target_user, authorized_keys_path = self.authorized_keys_path()
        ssh_dir = authorized_keys_path.parent
        ssh_dir.mkdir(parents=True, exist_ok=True)
        chmod_if_possible(ssh_dir, 0o700)

        existing_lines = []
        if authorized_keys_path.exists():
            existing_lines = authorized_keys_path.read_text(encoding="utf-8").splitlines()

        entry_comment = self.managed_ssh_entry_comment()
        entry = f"{ssh_key} {entry_comment}"
        existing_lines = [line for line in existing_lines if entry_comment not in line]

        existing_lines.append(entry)
        authorized_keys_path.write_text(
            "\n".join(existing_lines) + "\n",
            encoding="utf-8",
        )
        self.logger.info("Installed central SSH authorization for %s", target_user)

        chmod_if_possible(authorized_keys_path, 0o600)
        self.state["ssh_access_configured"] = True
        self.state["ssh_authorized_user"] = target_user
        self._save_state()

    def remove_central_ssh_access(self) -> None:
        target_user = (
            self.config.ssh_authorized_user.strip()
            or str(self.state.get("ssh_authorized_user") or "").strip()
            or self.current_login_user()
        )
        _, authorized_keys_path = self.authorized_keys_path(target_user)
        entry_comment = self.managed_ssh_entry_comment()

        if authorized_keys_path.exists():
            existing_lines = authorized_keys_path.read_text(encoding="utf-8").splitlines()
            filtered_lines = [line for line in existing_lines if entry_comment not in line]

            if filtered_lines != existing_lines:
                if filtered_lines:
                    authorized_keys_path.write_text(
                        "\n".join(filtered_lines) + "\n",
                        encoding="utf-8",
                    )
                    chmod_if_possible(authorized_keys_path, 0o600)
                else:
                    authorized_keys_path.unlink()

                self.logger.info("Removed central SSH authorization for %s", target_user)

        self.state["ssh_access_configured"] = False
        self.state["ssh_authorized_user"] = None
        self._save_state()

    def managed_ssh_entry_comment(self) -> str:
        return f"hermes-central:{self.config.client_id}"

    def authorized_keys_path(self, requested_user: Optional[str] = None) -> tuple[str, Path]:
        target_user = requested_user or self.config.ssh_authorized_user.strip() or self.current_login_user()
        ssh_dir = self.resolve_user_home(target_user) / ".ssh"
        return target_user, ssh_dir / "authorized_keys"

    def current_login_user(self) -> str:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

    def resolve_user_home(self, username: str) -> Path:
        requested = username.strip() or self.current_login_user()
        current = self.current_login_user()

        if os.name == "nt":
            if requested.lower() != current.lower():
                raise RuntimeError(
                    "Windows builds currently support authorizing the signed-in user only."
                )
            return Path.home()

        if requested == current:
            return Path.home()

        if pwd is None:
            raise RuntimeError(
                "This build cannot resolve another local SSH user on this platform."
            )

        try:
            return Path(pwd.getpwnam(requested).pw_dir)
        except KeyError as error:
            raise RuntimeError(
                f"Configured SSH user '{requested}' does not exist on this machine."
            ) from error

    def public_key(self) -> str:
        return self.public_key_path.read_text(encoding="utf-8").strip()

    def registration_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"HermesCompanionDaemon/{DAEMON_VERSION}",
        }
        token = self.api_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def api_token(self) -> str:
        if self.config.api_token.strip():
            return self.config.api_token.strip()

        service = self.config.api_token_keyring_service.strip()
        account = self.config.api_token_keyring_account.strip()
        if not service or not account or keyring is None:
            return ""

        try:
            return keyring.get_password(service, account) or ""
        except Exception as error:  # noqa: BLE001
            self.logger.warning("Failed to load API token from secure storage: %s", error)
            return ""

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.registration_headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8") or "{}"
                return json.loads(body)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {body}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Connection failed: {error.reason}") from error

    def fingerprint(self) -> str:
        return hashlib.sha256(self.public_key().encode("utf-8")).hexdigest()

    def registration_payload(self) -> Dict[str, Any]:
        return {
            "client_id": self.config.client_id,
            "public_key": self.public_key(),
            "hostname": self.config.node_name or self.hostname,
            "os_type": platform.platform(),
            "arch": platform.machine(),
            "fingerprint": self.fingerprint(),
            "requested_user": self.config.ssh_authorized_user.strip() or self.current_login_user(),
            "client_version": DAEMON_VERSION,
        }

    async def register_node(self) -> None:
        self._write_status(state="registering", last_error=None)
        response = await asyncio.to_thread(self.post_json, self.config.registration_url, self.registration_payload())

        self.state["node_id"] = response.get("node_id") or self.state.get("node_id") or self.config.client_id
        self.state["last_registration_at"] = utc_now()
        self.state["registration_response"] = response
        self._save_state()
        self._write_status(state="registered", registered=True, last_error=None)
        self.logger.info("Registration complete for %s", self.state["node_id"])

    async def heartbeat_once(self) -> None:
        self.state["last_heartbeat_at"] = utc_now()
        self._save_state()
        self._write_status(state="online", registered=True, last_error=None)

        if self.config.heartbeat_url.strip():
            await asyncio.to_thread(
                self.post_json,
                self.config.heartbeat_url,
                {
                    "node_id": self.state.get("node_id"),
                    "client_id": self.config.client_id,
                    "hostname": self.config.node_name or self.hostname,
                    "timestamp": self.state["last_heartbeat_at"],
                },
            )

    def ws_payload(self) -> Dict[str, Any]:
        return {
            "type": "node-status",
            "node_id": self.state.get("node_id"),
            "client_id": self.config.client_id,
            "hostname": self.config.node_name or self.hostname,
            "timestamp": utc_now(),
            "platform": platform.platform(),
            "arch": platform.machine(),
        }

    async def websocket_loop(self) -> None:
        headers = {}
        token = self.api_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with websockets.connect(
            self.config.status_ws_url,
            additional_headers=headers or None,
            ping_interval=20,
            ping_timeout=20,
        ) as socket_client:
            await socket_client.send(json.dumps(self.ws_payload()))

            while not self.shutdown_event.is_set():
                await socket_client.send(json.dumps(self.ws_payload()))
                self.state["last_heartbeat_at"] = utc_now()
                self._save_state()
                self._write_status(state="online", registered=True, last_error=None)

                try:
                    raw_message = await asyncio.wait_for(
                        socket_client.recv(),
                        timeout=self.config.heartbeat_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue

                self.handle_ws_message(raw_message)

    def handle_ws_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            self.logger.warning("Ignoring non-JSON websocket message")
            return

        if payload.get("action") == "reregister":
            self.state.pop("node_id", None)
            self._save_state()
            self.logger.info("Central Hermes requested re-registration")
            raise ReregisterRequested("Central Hermes requested re-registration")

        if payload.get("action") == "revoke":
            self.remove_central_ssh_access()
            self.state["revoked_at"] = utc_now()
            self.state.pop("node_id", None)
            self.state.pop("registration_response", None)
            self._save_state()
            self.logger.warning("Central Hermes revoked this node")
            raise NodeRevoked("Central Hermes revoked this node")

    async def run_forever(self, once: bool = False) -> None:
        self.ensure_keypair()
        self.ensure_central_ssh_access()
        backoff = max(self.config.retry_interval_seconds, 5)

        while not self.shutdown_event.is_set():
            try:
                if not self.state.get("node_id"):
                    await self.register_node()

                await self.heartbeat_once()
                backoff = max(self.config.retry_interval_seconds, 5)
                if once:
                    return

                if self.config.status_ws_url.strip():
                    await self.websocket_loop()
                else:
                    try:
                        await asyncio.wait_for(
                            self.shutdown_event.wait(),
                            timeout=self.config.heartbeat_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        continue
            except NodeRevoked as error:
                self._write_status(state="revoked", registered=False, last_error=str(error))
                return
            except Exception as error:  # noqa: BLE001
                if "Invalid node token" in str(error):
                    self.remove_central_ssh_access()
                    self.state["revoked_at"] = utc_now()
                    self.state.pop("node_id", None)
                    self.state.pop("registration_response", None)
                    self._save_state()
                    self._write_status(state="revoked", registered=False, last_error=str(error))
                    return
                self.logger.exception("Daemon loop failed")
                self._write_status(
                    state="error",
                    registered=self.state.get("node_id") is not None,
                    last_error=str(error),
                )
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 300)
                    continue

    def stop(self) -> None:
        self.shutdown_event.set()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Companion background daemon")
    parser.add_argument("--config", required=True, help="Path to daemon-config.json")
    parser.add_argument("--once", action="store_true", help="Run registration once and exit")
    args = parser.parse_args()

    config = RuntimeConfig.load(Path(args.config))
    daemon = HermesNodeDaemon(config)

    try:
        await daemon.run_forever(once=args.once)
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    asyncio.run(main())
