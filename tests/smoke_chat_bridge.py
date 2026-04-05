#!/usr/bin/env python3

import importlib.util
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


def load_bridge_module(root: Path):
    module_path = root / "chat-bridge" / "server.py"
    spec = importlib.util.spec_from_file_location("hermes_companion_chat_bridge", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def request(url: str, *, token: str | None = None, payload: dict | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="hermes-chat-bridge-smoke-") as tempdir:
        temp = Path(tempdir)
        fake_agent_dir = temp / "fake-agent"
        fake_agent_dir.mkdir()
        invocation_path = temp / "invocation.json"
        auth_home = temp / "home"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text('{"active_provider":"openai-codex"}\n', encoding="utf-8")
        (auth_home / "config.yaml").write_text(
            "model:\n"
            "  default: gpt-5.4-mini\n"
            "  provider: openai-codex\n"
            "  base_url: https://chatgpt.com/backend-api/codex\n"
            "platform_toolsets:\n"
            "  cli: []\n",
            encoding="utf-8",
        )
        (fake_agent_dir / "run_agent.py").write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "class AIAgent:\n"
            "    def __init__(self, **kwargs):\n"
            "        self.kwargs = kwargs\n"
            "    def run_conversation(self, user_message, system_message=None, task_id=None):\n"
            "        dump_path = Path(os.environ['BRIDGE_TEST_INVOCATION_PATH'])\n"
            "        dump_path.write_text(json.dumps({\n"
            "            'kwargs': self.kwargs,\n"
            "            'user_message': user_message,\n"
            "            'system_message': system_message,\n"
            "            'task_id': task_id,\n"
            "        }), encoding='utf-8')\n"
            "        return {'final_response': 'stub-answer'}\n",
            encoding="utf-8",
        )

        os.environ["HERMES_CHAT_BRIDGE_TOKEN"] = "bridge-secret"
        os.environ["HERMES_CHAT_BRIDGE_AGENT_DIR"] = str(fake_agent_dir)
        os.environ["HERMES_CHAT_BRIDGE_HOME"] = str(auth_home)
        os.environ["HERMES_CHAT_BRIDGE_DEFAULT_MODEL"] = "gpt-5.4-mini"
        os.environ["HERMES_CHAT_BRIDGE_PROVIDER"] = "openai-codex"
        os.environ["HERMES_CHAT_BRIDGE_BASE_URL"] = "https://chatgpt.com/backend-api/codex"
        os.environ["HERMES_CHAT_BRIDGE_WORKSPACE"] = tempdir
        os.environ["HERMES_CHAT_BRIDGE_MAX_ITERATIONS"] = "2"
        os.environ["BRIDGE_TEST_INVOCATION_PATH"] = str(invocation_path)

        module = load_bridge_module(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"

            try:
                request(f"{base_url}/api/health")
                raise AssertionError("health should require auth")
            except urllib.error.HTTPError as exc:
                assert exc.code == 401, exc

            status, payload = request(f"{base_url}/api/health", token="bridge-secret")
            assert status == 200, payload
            assert payload["status"] == "ok", payload

            status, payload = request(
                f"{base_url}/v1/responses",
                token="bridge-secret",
                payload={
                    "model": "openai/gpt-5.4-mini",
                    "input": [
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": "You are safe."}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Say hello."}],
                        },
                    ],
                },
            )
            assert status == 200, payload
            assert payload["output_text"] == "stub-answer", payload

            invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            assert invocation["kwargs"]["enabled_toolsets"] == [], invocation
            assert invocation["kwargs"]["provider"] == "openai-codex", invocation
            assert invocation["kwargs"]["base_url"] == "https://chatgpt.com/backend-api/codex", invocation
            assert invocation["kwargs"]["model"] == "gpt-5.4-mini", invocation
            assert invocation["user_message"] == "Say hello.", invocation
            assert invocation["system_message"] == "You are safe.", invocation
        finally:
            server.shutdown()
            server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
