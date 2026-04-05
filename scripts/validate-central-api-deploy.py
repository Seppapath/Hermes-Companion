#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets


def join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix


def websocket_url(base_url: str, suffix: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + suffix
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def current_user() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "validator"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    expected_status: int,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(method, url, headers=headers, json=payload)
    body_text = response.text
    require(
        response.status_code == expected_status,
        f"{method} {url} returned {response.status_code}, expected {expected_status}: {body_text}",
    )
    if not body_text:
        return {}
    try:
        return response.json()
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{method} {url} returned invalid JSON: {body_text}") from error


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a live Hermes Companion central API deployment."
    )
    parser.add_argument("--base-url", required=True, help="Public base URL, for example https://companion.example.com")
    parser.add_argument("--admin-secret", required=True, help="Admin secret for invite and node admin routes")
    parser.add_argument("--central-name", default="Production Hermes", help="Central display name to use in the validation invite")
    parser.add_argument("--chat-model", default="gpt-4.1-mini", help="Chat model value to place in the validation invite")
    parser.add_argument("--ssh-authorized-user", default=current_user(), help="Requested local SSH user for the validation node")
    parser.add_argument(
        "--central-ssh-public-key",
        default="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKhermescentraltestkey hermes-central",
        help="Central SSH public key to include in the validation invite",
    )
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for temporary testing only")
    args = parser.parse_args()

    suffix = secrets.token_hex(4)
    client_id = f"deploy-validate-{suffix}"
    hostname = f"deploy-validate-{suffix}"

    print(f"[1/8] Checking health at {join_url(args.base_url, '/api/health')}")

    async with httpx.AsyncClient(timeout=30.0, verify=not args.insecure) as client:
        health = await request_json(
            client,
            "GET",
            join_url(args.base_url, "/api/health"),
            expected_status=200,
        )
        require(health.get("status") == "ok", f"Unexpected health payload: {health}")

        print("[2/8] Creating validation invite")
        invite = await request_json(
            client,
            "POST",
            join_url(args.base_url, "/api/device-invites"),
            expected_status=200,
            headers={"X-Hermes-Admin-Secret": args.admin_secret},
            payload={
                "note": f"Deployment validation {suffix}",
                "expires_in_minutes": 15,
                "central_name": args.central_name,
                "chat_model": args.chat_model,
                "central_ssh_public_key": args.central_ssh_public_key,
                "ssh_authorized_user": args.ssh_authorized_user,
            },
        )
        invite_code = invite.get("inviteCode")
        require(bool(invite_code), f"Invite response did not include inviteCode: {invite}")

        print("[3/8] Redeeming invite and checking returned endpoints")
        bundle = await request_json(
            client,
            "POST",
            join_url(args.base_url, "/api/device-invites/redeem"),
            expected_status=200,
            payload={
                "inviteCode": invite_code,
                "machine": {
                    "hostname": hostname,
                    "osType": "validation",
                    "arch": "x86_64",
                    "currentUser": args.ssh_authorized_user,
                },
            },
        )
        token = bundle.get("apiToken")
        require(bool(token), f"Redeem response did not include apiToken: {bundle}")
        for key in (
            "registrationUrl",
            "chatHttpUrl",
            "chatWsUrl",
            "statusWsUrl",
            "heartbeatUrl",
        ):
            require(bool(bundle.get(key)), f"Redeem response missing {key}: {bundle}")

        auth_headers = {"Authorization": f"Bearer {token}"}

        print("[4/8] Registering validation node")
        registration = await request_json(
            client,
            "POST",
            join_url(args.base_url, "/api/register-node"),
            expected_status=200,
            headers=auth_headers,
            payload={
                "client_id": client_id,
                "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICdeploymentvalidationkey deploy-validate",
                "hostname": hostname,
                "os_type": "validation",
                "arch": "x86_64",
                "fingerprint": suffix,
                "requested_user": args.ssh_authorized_user,
                "client_version": "0.1.0",
            },
        )
        node_id = registration.get("node_id")
        require(bool(node_id), f"Register response missing node_id: {registration}")

        await request_json(
            client,
            "POST",
            join_url(args.base_url, "/api/node-heartbeat"),
            expected_status=200,
            headers=auth_headers,
            payload={
                "node_id": node_id,
                "client_id": client_id,
                "hostname": hostname,
                "timestamp": "2026-04-04T12:00:00+00:00",
            },
        )

        print("[5/8] Verifying chat HTTP endpoint")
        responses_payload = await request_json(
            client,
            "POST",
            join_url(args.base_url, "/v1/responses"),
            expected_status=200,
            headers=auth_headers,
            payload={
                "model": bundle["chatModel"],
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "deployment validation"}],
                    }
                ],
            },
        )
        require(
            bool(responses_payload.get("output_text")),
            f"Chat response missing output_text: {responses_payload}",
        )

        print("[6/8] Verifying node status WebSocket and re-register signal")
        async with websockets.connect(
            websocket_url(args.base_url, "/ws/nodes"),
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=30,
            close_timeout=10,
        ) as node_socket:
            await node_socket.send(
                json.dumps(
                    {
                        "type": "node-status",
                        "node_id": node_id,
                        "client_id": client_id,
                        "hostname": hostname,
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(node_socket.recv(), timeout=30))
            require(ack.get("type") == "ack", f"Unexpected node socket ack: {ack}")

            nodes = await request_json(
                client,
                "GET",
                join_url(args.base_url, "/api/nodes"),
                expected_status=200,
                headers={"X-Hermes-Admin-Secret": args.admin_secret},
            )
            matching = next((item for item in nodes.get("nodes", []) if item.get("node_id") == node_id), None)
            require(bool(matching), f"Registered node not found in /api/nodes: {nodes}")
            require(matching.get("connected") is True, f"Node is not marked connected: {matching}")

            await request_json(
                client,
                "POST",
                join_url(args.base_url, f"/api/nodes/{node_id}/reregister"),
                expected_status=200,
                headers={"X-Hermes-Admin-Secret": args.admin_secret},
            )
            instruction = json.loads(await asyncio.wait_for(node_socket.recv(), timeout=30))
            require(
                instruction.get("action") == "reregister",
                f"Unexpected re-register instruction: {instruction}",
            )

        print("[7/8] Verifying chat WebSocket greeting")
        async with websockets.connect(
            websocket_url(args.base_url, "/ws/chat"),
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=30,
            close_timeout=10,
        ) as chat_socket:
            greeting = json.loads(await asyncio.wait_for(chat_socket.recv(), timeout=30))
            require(greeting.get("type") == "assistant", f"Unexpected chat greeting: {greeting}")

        print("[8/8] Verifying revoke signal and token invalidation")
        async with websockets.connect(
            websocket_url(args.base_url, "/ws/nodes"),
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=30,
            close_timeout=10,
        ) as node_socket:
            await node_socket.send(
                json.dumps(
                    {
                        "type": "node-status",
                        "node_id": node_id,
                        "client_id": client_id,
                        "hostname": hostname,
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(node_socket.recv(), timeout=30))
            require(ack.get("type") == "ack", f"Unexpected revoke ack: {ack}")

            await request_json(
                client,
                "POST",
                join_url(args.base_url, f"/api/nodes/{node_id}/revoke"),
                expected_status=200,
                headers={"X-Hermes-Admin-Secret": args.admin_secret},
            )
            instruction = json.loads(await asyncio.wait_for(node_socket.recv(), timeout=30))
            require(instruction.get("action") == "revoke", f"Unexpected revoke instruction: {instruction}")

        rejected = await client.post(
            join_url(args.base_url, "/v1/responses"),
            headers=auth_headers,
            json={"model": bundle["chatModel"], "input": []},
        )
        require(
            rejected.status_code == 401,
            f"Revoked token still works unexpectedly: {rejected.status_code} {rejected.text}",
        )

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
