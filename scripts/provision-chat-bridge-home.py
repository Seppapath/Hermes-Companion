from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision an isolated Hermes chat bridge home.")
    parser.add_argument("--source-home", required=True, help="Existing Hermes home that contains auth.json.")
    parser.add_argument("--target-home", required=True, help="Destination isolated bridge home directory.")
    parser.add_argument("--default-model", default="gpt-5.4-mini")
    parser.add_argument("--provider", default="openai-codex")
    parser.add_argument("--base-url", default="https://chatgpt.com/backend-api/codex")
    args = parser.parse_args()

    source_home = Path(args.source_home).expanduser().resolve()
    target_home = Path(args.target_home).expanduser().resolve()
    auth_source = source_home / "auth.json"
    if not auth_source.exists():
        raise SystemExit(f"Missing auth.json in source home: {auth_source}")

    target_home.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        target_home.chmod(0o700)

    auth_target = target_home / "auth.json"
    shutil.copy2(auth_source, auth_target)
    if os.name != "nt":
        auth_target.chmod(0o600)

    config = (
        "model:\n"
        f"  default: {args.default_model}\n"
        f"  provider: {args.provider}\n"
        f"  base_url: {args.base_url}\n"
        "platform_toolsets:\n"
        "  cli: []\n"
    )
    write_text(target_home / "config.yaml", config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
