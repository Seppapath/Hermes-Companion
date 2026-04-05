from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field


logger = logging.getLogger("hermes-companion-central")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def matches_secret(record: Dict[str, Any], secret: str, *, hash_key: str, legacy_key: str) -> bool:
    hashed = str(record.get(hash_key) or "").strip()
    legacy = str(record.get(legacy_key) or "").strip()
    digest = sha256_text(secret)
    return (bool(hashed) and secrets.compare_digest(hashed, digest)) or (
        bool(legacy) and secrets.compare_digest(legacy, secret)
    )


DEFAULT_CHAT_MODEL = os.environ.get("HERMES_DEFAULT_CHAT_MODEL", "gpt-5").strip() or "gpt-5"


class MachineInfo(BaseModel):
    hostname: str
    os_type: str = Field(alias="osType")
    arch: str
    current_user: str = Field(alias="currentUser")


class CreateInviteRequest(BaseModel):
    note: Optional[str] = None
    expires_in_minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)
    central_name: str = "Central Hermes"
    chat_model: str = DEFAULT_CHAT_MODEL
    registration_url: Optional[str] = None
    chat_http_url: Optional[str] = None
    chat_ws_url: Optional[str] = None
    status_ws_url: Optional[str] = None
    heartbeat_url: Optional[str] = None
    central_ssh_public_key: str
    ssh_authorized_user: str = ""


class InviteRedeemRequest(BaseModel):
    invite_code: str = Field(alias="inviteCode")
    machine: MachineInfo


class RegisterNodeRequest(BaseModel):
    client_id: str
    public_key: str
    hostname: str
    os_type: str
    arch: str
    fingerprint: str
    requested_user: str
    client_version: str


class HeartbeatRequest(BaseModel):
    node_id: Optional[str] = None
    client_id: str
    hostname: str
    timestamp: str


for model in (
    MachineInfo,
    CreateInviteRequest,
    InviteRedeemRequest,
    RegisterNodeRequest,
    HeartbeatRequest,
):
    model.model_rebuild()


def slugify(value: str) -> str:
    allowed = [character.lower() if character.isalnum() else "-" for character in value]
    compact = "".join(allowed).strip("-")
    while "--" in compact:
        compact = compact.replace("--", "-")
    return compact or "remote-node"


class JsonStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def path(self, name: str) -> Path:
        return self.root / name

    def read(self, name: str) -> Dict[str, Any]:
        path = self.path(name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid JSON in {path}: {error}") from error

    def write(self, name: str, payload: Dict[str, Any]) -> None:
        path = self.path(name)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)

    def update(self, name: str, updater):
        with self._lock:
            payload = self.read(name)
            updated = updater(payload)
            self.write(name, updated)
            return updated


def migrate_legacy_secret_fields(store: JsonStore) -> None:
    def migrate_invites(payload: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in list(payload.items()):
            legacy_code = str(value.get("code") or "").strip()
            if not legacy_code:
                continue
            updated = dict(value)
            updated["code_sha256"] = updated.get("code_sha256") or sha256_text(legacy_code)
            updated.pop("code", None)
            payload[key] = updated
        return payload

    def migrate_tokens(payload: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in list(payload.items()):
            legacy_token = str(value.get("token") or "").strip()
            if not legacy_token:
                continue
            updated = dict(value)
            updated["token_sha256"] = updated.get("token_sha256") or sha256_text(legacy_token)
            updated.pop("token", None)
            payload[key] = updated
        return payload

    if store.path("invites.json").exists():
        store.update("invites.json", migrate_invites)
    if store.path("node_tokens.json").exists():
        store.update("node_tokens.json", migrate_tokens)


class NodeSocketHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_node_id: Dict[str, WebSocket] = {}
        self._by_client_id: Dict[str, WebSocket] = {}

    async def bind(self, websocket: WebSocket, payload: Dict[str, Any]) -> None:
        node_id = str(payload.get("node_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        async with self._lock:
            if node_id:
                self._by_node_id[node_id] = websocket
            if client_id:
                self._by_client_id[client_id] = websocket

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._by_node_id = {
                node_id: connection
                for node_id, connection in self._by_node_id.items()
                if connection is not websocket
            }
            self._by_client_id = {
                client_id: connection
                for client_id, connection in self._by_client_id.items()
                if connection is not websocket
            }

    async def send_to_node(self, node_id: str, payload: Dict[str, Any]) -> bool:
        async with self._lock:
            websocket = self._by_node_id.get(node_id)
        if websocket is None:
            return False
        try:
            await asyncio.wait_for(
                websocket.send_json(payload),
                timeout=SOCKET_OPERATION_TIMEOUT_SECONDS,
            )
            return True
        except Exception:
            await self.disconnect(websocket)
            return False

    async def is_connected(self, node_id: str) -> bool:
        async with self._lock:
            return node_id in self._by_node_id

    async def close_node(self, node_id: str, code: int = 4001) -> bool:
        async with self._lock:
            websocket = self._by_node_id.get(node_id)
        if websocket is None:
            return False
        try:
            await asyncio.wait_for(
                websocket.close(code=code),
                timeout=SOCKET_OPERATION_TIMEOUT_SECONDS,
            )
        except Exception:
            await self.disconnect(websocket)
            return False
        await self.disconnect(websocket)
        return True


async def best_effort_revoke_delivery(node_id: str, revoked_at: str) -> None:
    try:
        await socket_hub.send_to_node(
            node_id,
            {
                "action": "revoke",
                "nodeId": node_id,
                "timestamp": revoked_at,
            },
        )
        await socket_hub.close_node(node_id)
    except Exception:
        return


DATA_ROOT = Path(os.environ.get("HERMES_COMPANION_DATA_DIR", "~/.hermes/companion")).expanduser()
DEFAULT_ADMIN_SECRET = "change-me"
ADMIN_SECRET = os.environ.get("HERMES_ADMIN_SECRET", DEFAULT_ADMIN_SECRET)
ALLOW_INSECURE_DEFAULT_ADMIN_SECRET = (
    os.environ.get("HERMES_ALLOW_INSECURE_DEFAULT_ADMIN_SECRET", "").strip() == "1"
)
RESPONSES_PROXY_URL = os.environ.get("HERMES_PROXY_RESPONSES_URL", "").strip()
RESPONSES_PROXY_TOKEN = os.environ.get("HERMES_PROXY_BEARER_TOKEN", "").strip()
HERMES_WEBUI_PROXY_URL = os.environ.get("HERMES_WEBUI_PROXY_URL", "").strip()
HERMES_WEBUI_PASSWORD = os.environ.get("HERMES_WEBUI_PASSWORD", "").strip()
HERMES_WEBUI_WORKSPACE = os.environ.get("HERMES_WEBUI_WORKSPACE", "~").strip() or "~"
ALLOW_UNSAFE_WEBUI_PROXY = os.environ.get("HERMES_ALLOW_UNSAFE_WEBUI_PROXY", "").strip() == "1"
MEM0_API_URL = os.environ.get("HERMES_MEM0_API_URL", "").strip().rstrip("/")
MEM0_API_KEY = os.environ.get("HERMES_MEM0_API_KEY", "").strip()
MEM0_MEMORY_USER_ID = os.environ.get("HERMES_MEMORY_USER_ID", "").strip()
MEM0_MEMORY_AGENT_ID = os.environ.get("HERMES_MEMORY_AGENT_ID", "central-hermes").strip() or "central-hermes"
MEM0_MEMORY_APP_ID = os.environ.get("HERMES_MEMORY_APP_ID", "hermes-companion").strip() or "hermes-companion"
MEM0_SEARCH_LIMIT = max(1, int(os.environ.get("HERMES_MEMORY_SEARCH_LIMIT", "5")))
MEM0_TIMEOUT_SECONDS = float(os.environ.get("HERMES_MEM0_TIMEOUT_SECONDS", "10"))
MEM0_WRITE_ENABLED = os.environ.get("HERMES_MEMORY_WRITE_ENABLED", "1").strip() != "0"
SOCKET_OPERATION_TIMEOUT_SECONDS = float(
    os.environ.get("HERMES_SOCKET_OPERATION_TIMEOUT_SECONDS", "5")
)

if ADMIN_SECRET == DEFAULT_ADMIN_SECRET and not ALLOW_INSECURE_DEFAULT_ADMIN_SECRET:
    raise RuntimeError(
        "HERMES_ADMIN_SECRET must be set to a non-default value. "
        "Set HERMES_ALLOW_INSECURE_DEFAULT_ADMIN_SECRET=1 only for disposable local testing."
    )
if HERMES_WEBUI_PROXY_URL and not ALLOW_UNSAFE_WEBUI_PROXY:
    raise RuntimeError(
        "HERMES_WEBUI_PROXY_URL is disabled by default because a general-purpose Hermes web UI may expose "
        "filesystem and shell tools to enrolled nodes. Use a dedicated no-tools chat bridge instead, or set "
        "HERMES_ALLOW_UNSAFE_WEBUI_PROXY=1 only if you have intentionally hardened that local Hermes instance."
    )
if MEM0_API_URL and not MEM0_MEMORY_USER_ID:
    logger.warning(
        "HERMES_MEM0_API_URL is set but HERMES_MEMORY_USER_ID is empty. "
        "Hermes will fall back to request metadata or machine user identity, which may fragment memory across devices."
    )

store = JsonStore(DATA_ROOT)
migrate_legacy_secret_fields(store)
socket_hub = NodeSocketHub()
app = FastAPI(title="Hermes Companion Reference API", version="0.1.0")


def require_admin(x_hermes_admin_secret: Optional[str] = Header(default=None)) -> None:
    if not x_hermes_admin_secret or x_hermes_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret.")


def bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    return authorization.removeprefix("Bearer ").strip()


def extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    return authorization.removeprefix("Bearer ").strip()


def infer_url(request: CreateInviteRequest, request_obj: Request, suffix: str) -> str:
    if suffix == "registration" and request.registration_url:
        return request.registration_url
    if suffix == "chat" and request.chat_http_url:
        return request.chat_http_url
    if suffix == "status" and request.status_ws_url:
        return request.status_ws_url
    if suffix == "heartbeat" and request.heartbeat_url:
        return request.heartbeat_url
    if suffix == "chat-ws" and request.chat_ws_url:
        return request.chat_ws_url

    base = str(request_obj.base_url).rstrip("/")
    ws_base = (
        base.replace("https://", "wss://", 1)
        if base.startswith("https://")
        else base.replace("http://", "ws://", 1)
    )
    if suffix == "registration":
        return f"{base}/api/register-node"
    if suffix == "chat":
        return f"{base}/v1/responses"
    if suffix == "status":
        return f"{ws_base}/ws/nodes"
    if suffix == "heartbeat":
        return f"{base}/api/node-heartbeat"
    if suffix == "chat-ws":
        return f"{ws_base}/ws/chat"
    raise RuntimeError("Unknown suffix")


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "timestamp": iso_now(),
        "memoryEnabled": memory_enabled(),
        "memoryWriteEnabled": MEM0_WRITE_ENABLED,
    }


@app.get("/api/memory/status", dependencies=[Depends(require_admin)])
async def memory_status() -> Dict[str, Any]:
    if not memory_enabled():
        return {
            "enabled": False,
            "configured": False,
            "reachable": False,
        }

    reachable = False
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=MEM0_TIMEOUT_SECONDS) as client:
            response = await client.get(MEM0_API_URL or "", headers=memory_request_headers())
            reachable = response.status_code < 500
            detail = response.text[:200]
    except Exception as error:
        detail = str(error)

    return {
        "enabled": True,
        "configured": True,
        "reachable": reachable,
        "userId": MEM0_MEMORY_USER_ID,
        "agentId": MEM0_MEMORY_AGENT_ID,
        "baseUrl": MEM0_API_URL,
        "detail": detail,
    }


@app.post("/api/device-invites", dependencies=[Depends(require_admin)])
def create_invite(request: CreateInviteRequest, request_obj: Request) -> Dict[str, Any]:
    code = secrets.token_urlsafe(24)
    invite_id = secrets.token_hex(8)
    expires_at = (utc_now() + timedelta(minutes=request.expires_in_minutes)).isoformat()

    invite_record = {
        "id": invite_id,
        "code_sha256": sha256_text(code),
        "note": request.note,
        "created_at": iso_now(),
        "expires_at": expires_at,
        "consumed_at": None,
        "central_name": request.central_name,
        "chat_model": request.chat_model,
        "registration_url": infer_url(request, request_obj, "registration"),
        "chat_http_url": infer_url(request, request_obj, "chat"),
        "chat_ws_url": infer_url(request, request_obj, "chat-ws"),
        "status_ws_url": infer_url(request, request_obj, "status"),
        "heartbeat_url": infer_url(request, request_obj, "heartbeat"),
        "central_ssh_public_key": request.central_ssh_public_key,
        "ssh_authorized_user": request.ssh_authorized_user,
    }

    def updater(invites: Dict[str, Any]) -> Dict[str, Any]:
        invites[invite_id] = invite_record
        return invites

    store.update("invites.json", updater)
    base = str(request_obj.base_url).rstrip("/")
    return {
        "inviteId": invite_id,
        "inviteCode": code,
        "inviteUrl": f"{base}/invite?code={code}",
        "expiresAt": expires_at,
    }


@app.post("/api/device-invites/redeem")
def redeem_invite(request: InviteRedeemRequest) -> Dict[str, Any]:
    invite_id = ""
    invite: Dict[str, Any] = {}
    consumed_at = iso_now()
    consumed_by = request.machine.model_dump(by_alias=True)

    def consume_invite(payload: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal invite_id, invite
        for key, value in payload.items():
            if not matches_secret(value, request.invite_code, hash_key="code_sha256", legacy_key="code"):
                continue

            if value.get("consumed_at"):
                raise HTTPException(status_code=409, detail="Invite has already been consumed.")

            expires_at = datetime.fromisoformat(value["expires_at"])
            if expires_at <= utc_now():
                raise HTTPException(status_code=410, detail="Invite has expired.")

            updated = dict(value)
            updated["code_sha256"] = updated.get("code_sha256") or sha256_text(request.invite_code)
            updated.pop("code", None)
            updated["consumed_at"] = consumed_at
            updated["consumed_by"] = consumed_by
            payload[key] = updated
            invite_id = key
            invite = updated
            return payload

        raise HTTPException(status_code=404, detail="Invite not found.")

    store.update("invites.json", consume_invite)

    node_token = secrets.token_urlsafe(32)
    token_id = secrets.token_hex(8)
    token_record = {
        "id": token_id,
        "invite_id": invite_id,
        "token_sha256": sha256_text(node_token),
        "issued_at": iso_now(),
        "machine": consumed_by,
        "revoked_at": None,
    }

    def store_token(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload[token_id] = token_record
        return payload

    store.update("node_tokens.json", store_token)

    return {
        "centralName": invite["central_name"],
        "registrationUrl": invite["registration_url"],
        "chatHttpUrl": invite["chat_http_url"],
        "chatWsUrl": invite["chat_ws_url"],
        "statusWsUrl": invite["status_ws_url"],
        "heartbeatUrl": invite["heartbeat_url"],
        "apiToken": node_token,
        "chatModel": invite["chat_model"],
        "centralSshPublicKey": invite["central_ssh_public_key"],
        "sshAuthorizedUser": invite["ssh_authorized_user"],
    }


def token_record_from_secret(token: str) -> Dict[str, Any]:
    tokens = store.read("node_tokens.json")
    record = next(
        (
            value
            for value in tokens.values()
            if matches_secret(value, token, hash_key="token_sha256", legacy_key="token")
        ),
        None,
    )
    if not record or record.get("revoked_at"):
        raise HTTPException(status_code=401, detail="Invalid node token.")

    if record.get("token"):
        digest = sha256_text(token)

        def migrate_tokens(payload: Dict[str, Any]) -> Dict[str, Any]:
            for key, value in payload.items():
                if not matches_secret(value, token, hash_key="token_sha256", legacy_key="token"):
                    continue
                updated = dict(value)
                updated["token_sha256"] = updated.get("token_sha256") or digest
                updated.pop("token", None)
                payload[key] = updated
                break
            return payload

        store.update("node_tokens.json", migrate_tokens)
        record = dict(record)
        record["token_sha256"] = digest
        record.pop("token", None)

    return record


def websocket_token_record(websocket: WebSocket) -> Dict[str, Any]:
    token = websocket.query_params.get("token")
    authorization = websocket.headers.get("authorization")
    if not token and authorization:
        token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing node token.")
    return token_record_from_secret(token)


def request_segments(request: Dict[str, Any]) -> Dict[str, str]:
    user_segments: list[str] = []
    system_segments: list[str] = []

    for item in request.get("input", []):
        role = item.get("role")
        contents = item.get("content", [])
        if isinstance(contents, str):
            contents = [{"text": contents}]
        for content in contents:
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or content.get("output_text") or "").strip()
            if not text:
                continue
            if role == "system":
                system_segments.append(text)
            elif role == "user":
                user_segments.append(text)

    for item in request.get("messages", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        if item.get("role") == "system":
            system_segments.append(text)
        elif item.get("role") == "user":
            user_segments.append(text)

    return {
        "system": "\n\n".join(system_segments).strip(),
        "user": "\n\n".join(user_segments).strip(),
    }


def webui_model_name(request: Dict[str, Any]) -> str:
    model = str(request.get("model") or DEFAULT_CHAT_MODEL).strip() or DEFAULT_CHAT_MODEL
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    return model


def chat_session_key(request: Dict[str, Any], token_record: Dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    client_id = str(metadata.get("client_id") or "").strip()
    if client_id:
        return client_id
    token_id = str(token_record.get("id") or "").strip()
    if token_id:
        return f"token:{token_id}"
    machine = token_record.get("machine") if isinstance(token_record.get("machine"), dict) else {}
    hostname = str(machine.get("hostname") or "").strip()
    return hostname or "default"


def memory_enabled() -> bool:
    return bool(MEM0_API_URL)


def request_metadata(request: Dict[str, Any]) -> Dict[str, Any]:
    metadata = request.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def memory_scope(request: Dict[str, Any], token_record: Dict[str, Any]) -> Dict[str, str]:
    metadata = request_metadata(request)
    machine = token_record.get("machine") if isinstance(token_record.get("machine"), dict) else {}

    user_id = (
        str(metadata.get("memory_user_id") or "").strip()
        or str(metadata.get("user_id") or "").strip()
        or MEM0_MEMORY_USER_ID
        or str(machine.get("currentUser") or "").strip()
        or "hermes-user"
    )
    agent_id = (
        str(metadata.get("memory_agent_id") or "").strip()
        or str(metadata.get("agent_id") or "").strip()
        or MEM0_MEMORY_AGENT_ID
    )

    return {
        "user_id": user_id,
        "agent_id": agent_id,
    }


def memory_request_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if MEM0_API_KEY:
        headers["X-API-Key"] = MEM0_API_KEY
    return headers


def response_text(value: Dict[str, Any]) -> str:
    text = str(value.get("output_text") or "").strip()
    if text:
        return text

    output = value.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            contents = item.get("content")
            if isinstance(contents, list):
                for content in contents:
                    if not isinstance(content, dict):
                        continue
                    text = str(content.get("text") or content.get("output_text") or "").strip()
                    if text:
                        return text

    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = str(item.get("text") or "").strip()
                        if text:
                            return text

    return ""


def memory_entry_text(entry: Dict[str, Any]) -> str:
    for key in ("memory", "text", "content"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def memory_prompt(results: list[Dict[str, Any]]) -> str:
    lines = []
    for index, entry in enumerate(results[:MEM0_SEARCH_LIMIT], start=1):
        text = memory_entry_text(entry)
        if not text:
            continue
        lines.append(f"{index}. {text}")

    if not lines:
        return ""

    return (
        "Relevant long-term Hermes memory for this conversation:\n"
        + "\n".join(lines)
        + "\n\nUse these memories when they are relevant, but do not treat them as infallible if the current user message conflicts."
    )


def augment_request_with_memory(request: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    if not prompt:
        return request

    augmented = json.loads(json.dumps(request))

    if isinstance(augmented.get("input"), list):
        augmented["input"] = [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt}],
            },
            *augmented["input"],
        ]
        return augmented

    if isinstance(augmented.get("messages"), list):
        augmented["messages"] = [
            {"role": "system", "content": prompt},
            *augmented["messages"],
        ]
        return augmented

    augmented["input"] = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": prompt}],
        }
    ]
    return augmented


async def mem0_search(query: str, scope: Dict[str, str]) -> list[Dict[str, Any]]:
    if not memory_enabled() or not query.strip():
        return []

    payload = {
        "query": query.strip(),
        "limit": MEM0_SEARCH_LIMIT,
        **scope,
    }

    try:
        async with httpx.AsyncClient(timeout=MEM0_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{MEM0_API_URL}/search",
                json=payload,
                headers=memory_request_headers(),
            )
            response.raise_for_status()
            data = response.json()
    except Exception as error:
        logger.warning("Mem0 search failed: %s", error)
        return []

    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return []
    return [entry for entry in results if isinstance(entry, dict)]


def memory_write_payload(
    request: Dict[str, Any],
    token_record: Dict[str, Any],
    scope: Dict[str, str],
    user_message: str,
    assistant_message: str,
) -> Dict[str, Any]:
    metadata = request_metadata(request)
    machine = token_record.get("machine") if isinstance(token_record.get("machine"), dict) else {}
    session_key = chat_session_key(request, token_record)

    memory_metadata = {
        "app_id": MEM0_MEMORY_APP_ID,
        "source": "hermes-companion-central-api",
        "session_key": session_key,
        "node_hostname": str(machine.get("hostname") or "").strip(),
        "node_os_type": str(machine.get("osType") or "").strip(),
        "node_arch": str(machine.get("arch") or "").strip(),
        "node_user": str(machine.get("currentUser") or "").strip(),
        "client_id": str(metadata.get("client_id") or "").strip(),
        "project_id": str(metadata.get("project_id") or "").strip(),
        "project_name": str(metadata.get("project_name") or metadata.get("project") or "").strip(),
        "client_name": str(metadata.get("client_name") or "").strip(),
    }
    memory_metadata = {key: value for key, value in memory_metadata.items() if value}

    return {
        "messages": [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ],
        **scope,
        "metadata": memory_metadata,
    }


async def mem0_store_conversation(
    request: Dict[str, Any],
    token_record: Dict[str, Any],
    scope: Dict[str, str],
    user_message: str,
    assistant_message: str,
) -> None:
    if not memory_enabled() or not MEM0_WRITE_ENABLED:
        return
    if not user_message.strip() or not assistant_message.strip():
        return

    payload = memory_write_payload(request, token_record, scope, user_message, assistant_message)

    try:
        async with httpx.AsyncClient(timeout=MEM0_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{MEM0_API_URL}/memories",
                json=payload,
                headers=memory_request_headers(),
            )
            response.raise_for_status()
    except Exception as error:
        logger.warning("Mem0 write failed: %s", error)


async def ensure_webui_auth(client: httpx.AsyncClient) -> None:
    status_response = await client.get("/api/auth/status")
    status_response.raise_for_status()
    auth_status = status_response.json()
    if not auth_status.get("auth_enabled"):
        return
    if not HERMES_WEBUI_PASSWORD:
        raise RuntimeError("Hermes web UI auth is enabled, but HERMES_WEBUI_PASSWORD is not configured.")
    login_response = await client.post("/api/auth/login", json={"password": HERMES_WEBUI_PASSWORD})
    login_response.raise_for_status()


async def ensure_webui_session(
    client: httpx.AsyncClient,
    session_key: str,
    model: str,
) -> str:
    def find_existing(payload: Dict[str, Any]) -> str:
        existing = payload.get(session_key)
        if not isinstance(existing, dict):
            return ""
        if str(existing.get("model") or "").strip() != model:
            return ""
        return str(existing.get("session_id") or "").strip()

    existing_session = find_existing(store.read("chat_sessions.json"))
    if existing_session:
        return existing_session

    response = await client.post(
        "/api/session/new",
        json={
            "model": model,
            "workspace": str(Path(HERMES_WEBUI_WORKSPACE).expanduser()),
        },
    )
    response.raise_for_status()
    payload = response.json()
    session_id = str(payload.get("session", {}).get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("Hermes web UI did not return a session_id.")

    def store_session(sessions: Dict[str, Any]) -> Dict[str, Any]:
        sessions[session_key] = {
            "session_id": session_id,
            "model": model,
            "updated_at": iso_now(),
        }
        return sessions

    store.update("chat_sessions.json", store_session)
    return session_id


async def hermes_webui_response(request: Dict[str, Any], token_record: Dict[str, Any]) -> Dict[str, Any]:
    segments = request_segments(request)
    user_prompt = segments["user"]
    if not user_prompt:
        raise HTTPException(status_code=400, detail="No user prompt was provided.")

    system_prompt = segments["system"]
    message = user_prompt
    if system_prompt:
        message = f"System context:\n{system_prompt}\n\nUser request:\n{user_prompt}"

    model = webui_model_name(request)
    session_key = chat_session_key(request, token_record)

    async with httpx.AsyncClient(
        base_url=HERMES_WEBUI_PROXY_URL.rstrip("/") + "/",
        timeout=120.0,
        follow_redirects=True,
    ) as client:
        await ensure_webui_auth(client)
        session_id = await ensure_webui_session(client, session_key, model)

        response = await client.post(
            "/api/chat",
            json={
                "session_id": session_id,
                "message": message,
            },
        )
        if response.status_code == 404 and "Session not found" in response.text:
            def clear_session(sessions: Dict[str, Any]) -> Dict[str, Any]:
                sessions.pop(session_key, None)
                return sessions

            store.update("chat_sessions.json", clear_session)
            session_id = await ensure_webui_session(client, session_key, model)
            response = await client.post(
                "/api/chat",
                json={
                    "session_id": session_id,
                    "message": message,
                },
            )

        response.raise_for_status()
        payload = response.json()

    answer = str(payload.get("answer") or payload.get("result", {}).get("final_response") or "").strip()
    if not answer:
        raise RuntimeError(f"Hermes web UI did not return assistant text: {payload}")

    return {
        "output_text": answer,
        "output": [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer}],
            }
        ],
    }


@app.post("/api/register-node")
def register_node(request: RegisterNodeRequest, token: str = Depends(bearer_token)) -> Dict[str, Any]:
    token_record = token_record_from_secret(token)
    node_id = f"{slugify(request.hostname)}-{hashlib.sha256(request.public_key.encode('utf-8')).hexdigest()[:12]}"
    invites = store.read("invites.json")
    invite = invites[token_record["invite_id"]]
    node_record = {
        "node_id": node_id,
        "client_id": request.client_id,
        "hostname": request.hostname,
        "os_type": request.os_type,
        "arch": request.arch,
        "fingerprint": request.fingerprint,
        "requested_user": request.requested_user,
        "client_version": request.client_version,
        "public_key": request.public_key,
        "registered_at": iso_now(),
        "last_heartbeat_at": None,
        "invite_id": token_record["invite_id"],
        "central_name": invite["central_name"],
        "ssh_authorized_user": invite["ssh_authorized_user"],
        "revoked_at": None,
    }

    def updater(nodes: Dict[str, Any]) -> Dict[str, Any]:
        nodes[node_id] = node_record
        return nodes

    store.update("nodes.json", updater)
    return {"node_id": node_id, "registered_at": node_record["registered_at"]}


@app.post("/api/node-heartbeat")
def node_heartbeat(request: HeartbeatRequest, token: str = Depends(bearer_token)) -> Dict[str, Any]:
    token_record = token_record_from_secret(token)
    nodes = store.read("nodes.json")
    node_id = request.node_id or next(
        (
            key
            for key, value in nodes.items()
            if value.get("client_id") == request.client_id and value.get("invite_id") == token_record["invite_id"]
        ),
        None,
    )
    if not node_id or node_id not in nodes:
        raise HTTPException(status_code=404, detail="Node not registered.")

    def updater(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload[node_id]["last_heartbeat_at"] = request.timestamp
        return payload

    store.update("nodes.json", updater)
    return {"ok": True, "node_id": node_id}


@app.get("/api/nodes", dependencies=[Depends(require_admin)])
async def list_nodes() -> Dict[str, Any]:
    nodes = list(store.read("nodes.json").values())
    for node in nodes:
        node["connected"] = await socket_hub.is_connected(node["node_id"])
    return {"nodes": nodes}


@app.post("/api/nodes/{node_id}/revoke", dependencies=[Depends(require_admin)])
async def revoke_node(node_id: str) -> Dict[str, Any]:
    nodes = store.read("nodes.json")
    if node_id not in nodes:
        raise HTTPException(status_code=404, detail="Node not found.")

    node = nodes[node_id]
    revoked_at = iso_now()

    def updater(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload[node_id]["revoked_at"] = revoked_at
        return payload

    def revoke_tokens(payload: Dict[str, Any]) -> Dict[str, Any]:
        for token in payload.values():
            if token.get("invite_id") == node.get("invite_id"):
                token["revoked_at"] = revoked_at
        return payload

    store.update("nodes.json", updater)
    store.update("node_tokens.json", revoke_tokens)
    asyncio.create_task(best_effort_revoke_delivery(node_id, revoked_at))
    return {"ok": True, "nodeId": node_id}


@app.post("/api/nodes/{node_id}/reregister", dependencies=[Depends(require_admin)])
async def reregister_node(node_id: str) -> Dict[str, Any]:
    sent = await socket_hub.send_to_node(node_id, {"action": "reregister", "timestamp": iso_now()})
    if not sent:
        raise HTTPException(status_code=404, detail="Node is not currently connected.")
    return {"ok": True, "nodeId": node_id}


@app.post("/v1/responses")
async def responses(request: Dict[str, Any], token: str = Depends(bearer_token)) -> Dict[str, Any]:
    token_record = token_record_from_secret(token)
    segments = request_segments(request)
    scope = memory_scope(request, token_record)
    recalled_memories = await mem0_search(segments["user"], scope)
    augmented_request = augment_request_with_memory(request, memory_prompt(recalled_memories))

    if HERMES_WEBUI_PROXY_URL:
        payload = await hermes_webui_response(augmented_request, token_record)
        assistant_text = response_text(payload)
        asyncio.create_task(
            mem0_store_conversation(request, token_record, scope, segments["user"], assistant_text)
        )
        return payload

    if RESPONSES_PROXY_URL:
        headers = {}
        if RESPONSES_PROXY_TOKEN:
            headers["Authorization"] = f"Bearer {RESPONSES_PROXY_TOKEN}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(RESPONSES_PROXY_URL, json=augmented_request, headers=headers)
            response.raise_for_status()
            payload = response.json()
        assistant_text = response_text(payload)
        asyncio.create_task(
            mem0_store_conversation(request, token_record, scope, segments["user"], assistant_text)
        )
        return payload

    user_segments = []
    for item in augmented_request.get("input", []):
        if item.get("role") == "user":
            for content in item.get("content", []):
                text = content.get("text")
                if text:
                    user_segments.append(text)

    output_text = "Reference central Hermes received: " + " ".join(user_segments).strip()
    payload = {
        "output_text": output_text.strip(),
        "output": [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text.strip()}],
            }
        ],
    }
    assistant_text = response_text(payload)
    asyncio.create_task(
        mem0_store_conversation(request, token_record, scope, segments["user"], assistant_text)
    )
    return payload


@app.websocket("/ws/nodes")
async def node_status_socket(websocket: WebSocket) -> None:
    try:
        websocket_token_record(websocket)
    except HTTPException:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Expected JSON payload."})
                continue
            await socket_hub.bind(websocket, payload)
            await websocket.send_json(
                {
                    "type": "ack",
                    "nodeId": payload.get("node_id"),
                    "clientId": payload.get("client_id"),
                    "timestamp": iso_now(),
                }
            )
    except WebSocketDisconnect:
        await socket_hub.disconnect(websocket)
    except Exception:
        await socket_hub.disconnect(websocket)
        raise


@app.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    try:
        websocket_token_record(websocket)
    except HTTPException:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "assistant",
            "text": "Reference central Hermes chat socket is connected. Prefer the HTTP /v1/responses API in this build.",
        }
    )

    try:
        while True:
            raw_message = await websocket.receive_text()
            await websocket.send_json(
                {
                    "type": "assistant",
                    "text": f"Reference central Hermes received: {raw_message}",
                }
            )
    except WebSocketDisconnect:
        return
