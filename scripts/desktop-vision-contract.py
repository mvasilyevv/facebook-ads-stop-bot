#!/usr/bin/env python3
"""Fail-closed parser for the public Vision/browser compatibility contract."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

REQUIRED_BROWSER_CONTRACT_VERSION = 5


def _payload_from_text(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Vision response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Vision response must be a JSON object")
    return payload


def _ready(payload: dict[str, Any]) -> bool:
    version = payload.get("browser_contract_version")
    return (
        payload.get("channel_status") == "READY"
        and isinstance(payload.get("profile_id"), str)
        and bool(payload["profile_id"].strip())
        and payload.get("browser_contract_compatible") is True
        and payload.get("graph_probe_performed") is True
        and payload.get("graph_probe_ok") is True
        and isinstance(payload.get("browser_session_id"), str)
        and bool(payload["browser_session_id"].strip())
        and isinstance(payload.get("live_profile_id"), str)
        and bool(payload["live_profile_id"].strip())
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version == REQUIRED_BROWSER_CONTRACT_VERSION
    )


def _command_ready(expected_profile_id: str | None) -> bool:
    payload = _payload_from_text(sys.stdin.read())
    if not _ready(payload):
        return False
    configured_profile_id = payload.get("profile_id")
    live_profile_id = payload.get("live_profile_id")
    if not isinstance(configured_profile_id, str) or not configured_profile_id.strip():
        return False
    if live_profile_id.strip() != configured_profile_id.strip():
        return False
    return expected_profile_id is None or configured_profile_id.strip() == expected_profile_id


def _command_browser_ready(expected_profile_id: str) -> bool:
    payload = _payload_from_text(sys.stdin.read())
    version = payload.get("browser_contract_version")
    live_profile_id = payload.get("vision_profile_id")
    return (
        bool(expected_profile_id.strip())
        and payload.get("healthy") is True
        and payload.get("probe_performed") is True
        and payload.get("probe_ok") is True
        and isinstance(payload.get("session_id"), str)
        and bool(payload["session_id"].strip())
        and isinstance(live_profile_id, str)
        and live_profile_id.strip() == expected_profile_id.strip()
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version == REQUIRED_BROWSER_CONTRACT_VERSION
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    ready = subparsers.add_parser("ready")
    ready.add_argument("--expected-profile-id")
    browser_ready = subparsers.add_parser("browser-ready")
    browser_ready.add_argument("--expected-profile-id", required=True)
    subparsers.add_parser("required-version")
    args = parser.parse_args()

    if args.command == "required-version":
        print(REQUIRED_BROWSER_CONTRACT_VERSION)
        return 0

    try:
        if args.command == "browser-ready":
            ok = _command_browser_ready(args.expected_profile_id)
        else:
            ok = _command_ready(args.expected_profile_id)
    except (ValueError, KeyError):
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
