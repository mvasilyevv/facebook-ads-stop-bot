#!/usr/bin/env python3
"""Fail-closed ownership validation for the shared production Docker network."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

CANONICAL_NETWORK = "fb_agent_safety_first_platform"
NETWORK_CONTRACT = "safety-first-v1"

INFRA_SERVICES = frozenset({"postgres", "redis"})
APPLICATION_SERVICES = frozenset(
    {
        "api",
        "frontend",
        "mini-app",
        "observer",
        "autopause_worker",
        "meta_api",
        "telegram_delivery_worker",
        "telegram_update_worker",
        "cleanup",
        "reconciler",
        "health_watchdog",
        "enable_recommendation",
        "digest_scheduler",
        "cabinet_scheduler",
        "tracker_reconciliation_worker",
        "campaign_creator",
    }
)

BOOTSTRAP_IDENTITIES = {("fb_agent_infra", service) for service in INFRA_SERVICES}
RUNTIME_IDENTITIES = (
    BOOTSTRAP_IDENTITIES
    | {
        (project, service)
        for project in ("fb_agent_blue", "fb_agent_green")
        for service in APPLICATION_SERVICES
    }
    | {
        ("fb_agent_telemetry_agent", "alloy-agent"),
        ("fb_agent_vision", "webtop"),
    }
)

SENSITIVE_ALIAS_OWNERS = {
    "postgres": ("fb_agent_infra", "postgres"),
    "redis": ("fb_agent_infra", "redis"),
    "alloy-agent": ("fb_agent_telemetry_agent", "alloy-agent"),
    "vision-webtop": ("fb_agent_vision", "webtop"),
}
REQUIRED_IDENTITY_ALIASES = {identity: alias for alias, identity in SENSITIVE_ALIAS_OWNERS.items()}
IDENTITY_PURPOSES = {
    **{identity: "infra" for identity in BOOTSTRAP_IDENTITIES},
    **{
        (project, service): "app"
        for project in ("fb_agent_blue", "fb_agent_green")
        for service in APPLICATION_SERVICES
    },
    ("fb_agent_telemetry_agent", "alloy-agent"): "telemetry",
    ("fb_agent_vision", "webtop"): "vision",
}
RELEASE_SCOPED_IDENTITIES = frozenset(
    identity
    for identity, purpose in IDENTITY_PURPOSES.items()
    if purpose in {"app", "telemetry", "vision"}
)
SAFE_RELEASE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class InventoryError(RuntimeError):
    """The shared network is missing or contains an unowned endpoint."""


def _mapping(value: Any, *, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InventoryError(f"{description} is malformed")
    return value


def _string(value: Any, *, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise InventoryError(f"{description} is missing")
    return value


def _network_aliases(container: Mapping[str, Any], network: str) -> frozenset[str]:
    settings = _mapping(
        container.get("NetworkSettings"),
        description="container network settings",
    )
    networks = _mapping(
        settings.get("Networks"),
        description="container network membership",
    )
    endpoint = _mapping(
        networks.get(network),
        description=f"container endpoint on {network}",
    )
    raw_aliases = endpoint.get("Aliases")
    if raw_aliases is None:
        return frozenset()
    if not isinstance(raw_aliases, Sequence) or isinstance(raw_aliases, str):
        raise InventoryError("container network aliases are malformed")
    if any(not isinstance(alias, str) or not alias for alias in raw_aliases):
        raise InventoryError("container network aliases are malformed")
    return frozenset(raw_aliases)


def validate_inventory(
    network: Mapping[str, Any],
    containers: Sequence[Mapping[str, Any]],
    *,
    cluster_id: str,
    phase: str,
) -> int:
    """Validate network ownership and return the number of attached endpoints."""

    if phase not in {"bootstrap", "runtime"}:
        raise InventoryError(f"unsupported inventory phase: {phase}")
    if not re.fullmatch(r"[0-9a-f]{32}", cluster_id):
        raise InventoryError("bootstrap cluster id is invalid")

    name = _string(network.get("Name"), description="network name")
    if name != CANONICAL_NETWORK:
        raise InventoryError(f"unexpected platform network: {name}")
    if network.get("Driver") != "bridge" or network.get("Scope") != "local":
        raise InventoryError("platform network must be a local bridge")
    if network.get("Internal") is not False:
        raise InventoryError("platform network must not be internal")

    labels = _mapping(network.get("Labels"), description="network labels")
    if labels.get("com.fb-agent.cluster-id") != cluster_id:
        raise InventoryError("platform network belongs to another cluster")
    if labels.get("com.fb-agent.network-contract") != NETWORK_CONTRACT:
        raise InventoryError("platform network contract is invalid")

    endpoints = _mapping(
        network.get("Containers"),
        description="network endpoint inventory",
    )
    inspected_by_id: dict[str, Mapping[str, Any]] = {}
    for container in containers:
        container_id = _string(container.get("Id"), description="container id")
        if container_id in inspected_by_id:
            raise InventoryError(f"duplicate container inspection: {container_id}")
        inspected_by_id[container_id] = container
    if set(inspected_by_id) != set(endpoints):
        raise InventoryError("container inspection does not match network endpoints")

    allowed = BOOTSTRAP_IDENTITIES if phase == "bootstrap" else RUNTIME_IDENTITIES
    identities: dict[tuple[str, str], str] = {}
    alias_owners: dict[str, tuple[str, str]] = {}

    for container_id in sorted(endpoints):
        endpoint = _mapping(
            endpoints[container_id],
            description=f"network endpoint {container_id}",
        )
        container = inspected_by_id[container_id]
        endpoint_name = _string(
            endpoint.get("Name"),
            description=f"network endpoint name {container_id}",
        )
        inspected_name = _string(
            container.get("Name"),
            description=f"container name {container_id}",
        ).removeprefix("/")
        if endpoint_name != inspected_name:
            raise InventoryError(f"network endpoint identity mismatch for {container_id}")

        config = _mapping(
            container.get("Config"),
            description=f"container config {container_id}",
        )
        container_labels = _mapping(
            config.get("Labels"),
            description=f"container labels {endpoint_name}",
        )
        project = _string(
            container_labels.get("com.docker.compose.project"),
            description=f"Compose project label for {endpoint_name}",
        )
        service = _string(
            container_labels.get("com.docker.compose.service"),
            description=f"Compose service label for {endpoint_name}",
        )
        identity = (project, service)
        if identity not in allowed:
            raise InventoryError(
                f"platform network contains an unowned endpoint: {project}/{service}"
            )
        if container_labels.get("com.fb-agent.managed") != "true":
            raise InventoryError(f"{project}/{service} is not explicitly managed")
        if container_labels.get("com.fb-agent.cluster-id") != cluster_id:
            raise InventoryError(f"{project}/{service} belongs to another cluster")
        expected_purpose = IDENTITY_PURPOSES[identity]
        if container_labels.get("com.fb-agent.purpose") != expected_purpose:
            raise InventoryError(f"{project}/{service} has an invalid inventory purpose")
        release_id = container_labels.get("com.fb-agent.release")
        color = container_labels.get("com.fb-agent.color")
        if identity in RELEASE_SCOPED_IDENTITIES:
            if not isinstance(release_id, str) or not SAFE_RELEASE.fullmatch(release_id):
                raise InventoryError(f"{project}/{service} has no valid release identity")
        elif release_id not in {None, ""}:
            raise InventoryError(f"{project}/{service} has a false release identity")
        if expected_purpose == "app":
            expected_color = project.removeprefix("fb_agent_")
            if color != expected_color:
                raise InventoryError(
                    f"{project}/{service} color does not match its Compose project"
                )
        elif color not in {None, ""}:
            raise InventoryError(f"{project}/{service} has a false color identity")
        if identity in identities:
            raise InventoryError(
                f"platform network contains duplicate owned endpoints: {project}/{service}"
            )
        identities[identity] = container_id

        aliases = _network_aliases(container, name)
        required_alias = REQUIRED_IDENTITY_ALIASES.get(identity)
        if required_alias is not None and required_alias not in aliases:
            raise InventoryError(f"{project}/{service} is missing required alias {required_alias}")
        for alias in aliases & SENSITIVE_ALIAS_OWNERS.keys():
            expected_owner = SENSITIVE_ALIAS_OWNERS[alias]
            if identity != expected_owner:
                raise InventoryError(f"{project}/{service} owns protected alias {alias}")
            previous_owner = alias_owners.get(alias)
            if previous_owner is not None:
                raise InventoryError(f"protected alias {alias} has multiple owners")
            alias_owners[alias] = identity

    if phase == "runtime" and ("fb_agent_infra", "postgres") not in identities:
        raise InventoryError("runtime network has no owned PostgreSQL endpoint")
    return len(endpoints)


def _docker_json(*args: str) -> Any:
    try:
        result = subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise InventoryError(f"Docker inventory failed: {detail.strip()}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InventoryError("Docker inventory returned invalid JSON") from exc


def inspect_and_validate(*, cluster_id: str, phase: str, network_name: str) -> int:
    if network_name != CANONICAL_NETWORK:
        raise InventoryError(f"only canonical network {CANONICAL_NETWORK} may be inspected")
    raw_networks = _docker_json("network", "inspect", network_name)
    if not isinstance(raw_networks, list) or len(raw_networks) != 1:
        raise InventoryError("Docker returned an ambiguous network inventory")
    network = _mapping(raw_networks[0], description="network inspection")
    endpoints = _mapping(
        network.get("Containers"),
        description="network endpoint inventory",
    )
    raw_containers: Any = []
    if endpoints:
        raw_containers = _docker_json(
            "container",
            "inspect",
            *sorted(endpoints),
        )
    if not isinstance(raw_containers, list):
        raise InventoryError("Docker returned an invalid container inventory")
    containers = [_mapping(item, description="container inspection") for item in raw_containers]
    return validate_inventory(
        network,
        containers,
        cluster_id=cluster_id,
        phase=phase,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the safety-first Docker network inventory",
    )
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument(
        "--network",
        default=CANONICAL_NETWORK,
    )
    parser.add_argument(
        "--phase",
        choices=("bootstrap", "runtime"),
        required=True,
    )
    args = parser.parse_args()
    try:
        count = inspect_and_validate(
            cluster_id=args.cluster_id,
            phase=args.phase,
            network_name=args.network,
        )
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Owned {args.phase} network inventory is valid ({count} attached endpoints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
