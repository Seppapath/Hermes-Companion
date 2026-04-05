from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = os.environ.get("HERMES_CHAT_BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("HERMES_CHAT_BRIDGE_PORT", "8788"))
BRIDGE_TOKEN = os.environ.get("HERMES_CHAT_BRIDGE_TOKEN", "").strip()
AGENT_DIR = Path(os.environ.get("HERMES_CHAT_BRIDGE_AGENT_DIR", "")).expanduser()
HERMES_HOME = Path(os.environ.get("HERMES_CHAT_BRIDGE_HOME", "")).expanduser()
DEFAULT_MODEL = os.environ.get("HERMES_CHAT_BRIDGE_DEFAULT_MODEL", "gpt-5").strip() or "gpt-5"
MODEL_PROVIDER = os.environ.get("HERMES_CHAT_BRIDGE_PROVIDER", "").strip() or None
MODEL_BASE_URL = os.environ.get("HERMES_CHAT_BRIDGE_BASE_URL", "").strip() or None
WORKSPACE = str(
    Path(os.environ.get("HERMES_CHAT_BRIDGE_WORKSPACE", "/var/empty")).expanduser().resolve()
)
MAX_ITERATIONS = int(os.environ.get("HERMES_CHAT_BRIDGE_MAX_ITERATIONS", "2"))

if not BRIDGE_TOKEN:
    raise RuntimeError("HERMES_CHAT_BRIDGE_TOKEN must be set.")
if not AGENT_DIR or not (AGENT_DIR / "run_agent.py").exists():
    raise RuntimeError("HERMES_CHAT_BRIDGE_AGENT_DIR must point to a Hermes agent checkout.")
if not HERMES_HOME:
    raise RuntimeError("HERMES_CHAT_BRIDGE_HOME must be set.")
if not HERMES_HOME.exists():
    raise RuntimeError(f"HERMES_CHAT_BRIDGE_HOME does not exist: {HERMES_HOME}")

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

os.environ["HERMES_HOME"] = str(HERMES_HOME)

from run_agent import AIAgent  # noqa: E402


AGENT_LOCK = threading.Lock()


def normalize_model(request: dict[str, Any]) -> str:
    model = str(request.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    return model


def request_segments(request: dict[str, Any]) -> dict[str, str]:
    user_segments: list[str] = []
    system_segments: list[str] = []

    for item in request.get("input", []):
        if not isinstance(item, dict):
            continue
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


def response_payload(answer: str) -> dict[str, Any]:
    return {
        "output_text": answer,
        "output": [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer}],
            }
        ],
    }


def chat_completions_payload(answer: str, model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                },
                "finish_reason": "stop",
            }
        ],
    }


def bridge_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workspace": WORKSPACE,
        "agent_dir": str(AGENT_DIR),
        "hermes_home": str(HERMES_HOME),
        "default_model": DEFAULT_MODEL,
        "provider": MODEL_PROVIDER,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesCompanionChatBridge/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bearer_token(self) -> str:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return ""
        return authorization.removeprefix("Bearer ").strip()

    def _authorized(self) -> bool:
        token = self._bearer_token()
        return bool(token) and hmac.compare_digest(token, BRIDGE_TOKEN)

    def do_GET(self) -> None:
        try:
            if self.path not in ("/health", "/api/health"):
                return self._json({"error": "not found"}, status=404)
            if not self._authorized():
                return self._json({"error": "unauthorized"}, status=401)
            return self._json(bridge_health())
        except Exception as exc:
            print(f"[chat-bridge] GET {self.path} failed\n{traceback.format_exc()}", flush=True)
            return self._json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        try:
            if self.path not in ("/v1/responses", "/v1/chat/completions"):
                return self._json({"error": "not found"}, status=404)
            if not self._authorized():
                return self._json({"error": "unauthorized"}, status=401)

            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")

            segments = request_segments(payload)
            if not segments["user"]:
                return self._json({"error": "No user prompt was provided."}, status=400)

            model = normalize_model(payload)

            old_hermes_home = os.environ.get("HERMES_HOME")
            old_terminal_cwd = os.environ.get("TERMINAL_CWD")
            old_exec_ask = os.environ.get("HERMES_EXEC_ASK")

            try:
                os.environ["HERMES_HOME"] = str(HERMES_HOME)
                os.environ["TERMINAL_CWD"] = WORKSPACE
                os.environ["HERMES_EXEC_ASK"] = "0"
                with AGENT_LOCK:
                    agent = AIAgent(
                        model=model,
                        provider=MODEL_PROVIDER,
                        base_url=MODEL_BASE_URL,
                        quiet_mode=True,
                        enabled_toolsets=[],
                        max_iterations=MAX_ITERATIONS,
                    )
                    result = agent.run_conversation(
                        user_message=segments["user"],
                        system_message=segments["system"] or None,
                        task_id=f"chat-bridge-{int(time.time() * 1000)}",
                    )
            finally:
                if old_hermes_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_hermes_home
                if old_terminal_cwd is None:
                    os.environ.pop("TERMINAL_CWD", None)
                else:
                    os.environ["TERMINAL_CWD"] = old_terminal_cwd
                if old_exec_ask is None:
                    os.environ.pop("HERMES_EXEC_ASK", None)
                else:
                    os.environ["HERMES_EXEC_ASK"] = old_exec_ask

            answer = str(result.get("final_response") or "").strip()
            if not answer:
                messages = result.get("messages") or []
                assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]
                if assistant_messages:
                    answer = str(assistant_messages[-1].get("content") or "").strip()
            if not answer:
                raise RuntimeError("Bridge agent returned no assistant text.")

            if self.path == "/v1/chat/completions":
                return self._json(chat_completions_payload(answer, model))

            return self._json(response_payload(answer))
        except Exception as exc:
            print(f"[chat-bridge] POST {self.path} failed\n{traceback.format_exc()}", flush=True)
            return self._json({"error": str(exc)}, status=500)


def main() -> None:
    print("[chat-bridge] startup", flush=True)
    print(f"[chat-bridge] host={HOST} port={PORT}", flush=True)
    print(f"[chat-bridge] agent_dir={AGENT_DIR}", flush=True)
    print(f"[chat-bridge] hermes_home={HERMES_HOME}", flush=True)
    print(f"[chat-bridge] workspace={WORKSPACE}", flush=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
