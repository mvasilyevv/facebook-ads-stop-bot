from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "platform-network-inventory.py"
CLUSTER_ID = "a" * 32
NETWORK = "fb_agent_safety_first_platform"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "platform_network_inventory",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory = _load_module()


def _container(
    container_id: str,
    *,
    name: str,
    project: str,
    service: str,
    aliases: list[str],
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    purpose = {
        "fb_agent_infra": "infra",
        "fb_agent_blue": "app",
        "fb_agent_green": "app",
        "fb_agent_telemetry_agent": "telemetry",
        "fb_agent_vision": "vision",
    }.get(project, "unknown")
    inventory_labels = {
        "com.fb-agent.managed": "true",
        "com.fb-agent.cluster-id": CLUSTER_ID,
        "com.fb-agent.purpose": purpose,
    }
    if purpose in {"app", "telemetry", "vision"}:
        inventory_labels["com.fb-agent.release"] = "release-1"
    if purpose == "app":
        inventory_labels["com.fb-agent.color"] = project.removeprefix("fb_agent_")
    if labels:
        inventory_labels.update(labels)
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                **inventory_labels,
            }
        },
        "NetworkSettings": {
            "Networks": {
                NETWORK: {
                    "Aliases": aliases,
                }
            }
        },
    }


def _network(containers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "Name": NETWORK,
        "Driver": "bridge",
        "Scope": "local",
        "Internal": False,
        "Labels": {
            "com.fb-agent.cluster-id": CLUSTER_ID,
            "com.fb-agent.network-contract": "safety-first-v1",
        },
        "Containers": {
            container["Id"]: {
                "Name": container["Name"].removeprefix("/"),
            }
            for container in containers
        },
    }


def test_runtime_inventory_accepts_only_canonical_endpoint_identities() -> None:
    containers = [
        _container(
            "1" * 64,
            name="fb_agent_infra-postgres-1",
            project="fb_agent_infra",
            service="postgres",
            aliases=["fb_agent_infra-postgres-1", "postgres"],
        ),
        _container(
            "2" * 64,
            name="fb_agent_blue-api-1",
            project="fb_agent_blue",
            service="api",
            aliases=["fb_agent_blue-api-1", "api"],
        ),
        _container(
            "3" * 64,
            name="fb_agent_telemetry_agent-alloy-agent-1",
            project="fb_agent_telemetry_agent",
            service="alloy-agent",
            aliases=["fb_agent_telemetry_agent-alloy-agent-1", "alloy-agent"],
        ),
        _container(
            "4" * 64,
            name="vision-webtop",
            project="fb_agent_vision",
            service="webtop",
            aliases=["vision-webtop"],
        ),
    ]

    assert (
        inventory.validate_inventory(
            _network(containers),
            containers,
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )
        == 4
    )


@pytest.mark.parametrize(
    ("project", "service"),
    (
        ("fb_agent", "postgres"),
        ("fb_agent_telemetry_candidate", "alloy-agent"),
        ("vision-webtop", "webtop"),
        ("fb_agent_blue", "telegram_poller"),
        ("fb_agent_green", "migrator"),
    ),
)
def test_runtime_inventory_rejects_legacy_candidate_and_unknown_endpoints(
    project: str,
    service: str,
) -> None:
    containers = [
        _container(
            "1" * 64,
            name="fb_agent_infra-postgres-1",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
        ),
        _container(
            "2" * 64,
            name="unowned",
            project=project,
            service=service,
            aliases=[service],
        ),
    ]

    with pytest.raises(
        inventory.InventoryError,
        match="unowned endpoint",
    ):
        inventory.validate_inventory(
            _network(containers),
            containers,
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )


def test_runtime_inventory_rejects_protected_alias_hijacking() -> None:
    containers = [
        _container(
            "1" * 64,
            name="fb_agent_infra-postgres-1",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
        ),
        _container(
            "2" * 64,
            name="fb_agent_blue-api-1",
            project="fb_agent_blue",
            service="api",
            aliases=["api", "postgres"],
        ),
    ]

    with pytest.raises(
        inventory.InventoryError,
        match="owns protected alias postgres",
    ):
        inventory.validate_inventory(
            _network(containers),
            containers,
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )


def test_runtime_inventory_rejects_duplicate_owned_identity() -> None:
    containers = [
        _container(
            "1" * 64,
            name="postgres-one",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
        ),
        _container(
            "2" * 64,
            name="postgres-two",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
        ),
    ]

    with pytest.raises(
        inventory.InventoryError,
        match="duplicate owned endpoints",
    ):
        inventory.validate_inventory(
            _network(containers),
            containers,
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )


@pytest.mark.parametrize(
    ("label", "value", "message"),
    (
        ("com.fb-agent.managed", "false", "not explicitly managed"),
        ("com.fb-agent.cluster-id", "b" * 32, "another cluster"),
        ("com.fb-agent.purpose", "infra", "invalid inventory purpose"),
        ("com.fb-agent.color", "green", "does not match"),
        ("com.fb-agent.release", "", "no valid release identity"),
    ),
)
def test_runtime_inventory_rejects_spoofed_application_inventory_labels(
    label: str,
    value: str,
    message: str,
) -> None:
    containers = [
        _container(
            "1" * 64,
            name="fb_agent_infra-postgres-1",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
        ),
        _container(
            "2" * 64,
            name="fb_agent_blue-api-1",
            project="fb_agent_blue",
            service="api",
            aliases=["api"],
            labels={label: value},
        ),
    ]

    with pytest.raises(inventory.InventoryError, match=message):
        inventory.validate_inventory(
            _network(containers),
            containers,
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )


def test_infra_inventory_rejects_false_release_and_color_labels() -> None:
    for label in ("com.fb-agent.release", "com.fb-agent.color"):
        container = _container(
            "1" * 64,
            name="fb_agent_infra-postgres-1",
            project="fb_agent_infra",
            service="postgres",
            aliases=["postgres"],
            labels={label: "false-identity"},
        )
        with pytest.raises(inventory.InventoryError, match="false .*identity|false color"):
            inventory.validate_inventory(
                _network([container]),
                [container],
                cluster_id=CLUSTER_ID,
                phase="runtime",
            )


def test_inventory_rejects_wrong_network_owner_and_nonlocal_driver() -> None:
    network = _network([])
    network["Labels"]["com.fb-agent.cluster-id"] = "b" * 32
    with pytest.raises(inventory.InventoryError, match="another cluster"):
        inventory.validate_inventory(
            network,
            [],
            cluster_id=CLUSTER_ID,
            phase="bootstrap",
        )

    network = _network([])
    network["Driver"] = "overlay"
    with pytest.raises(inventory.InventoryError, match="local bridge"):
        inventory.validate_inventory(
            network,
            [],
            cluster_id=CLUSTER_ID,
            phase="bootstrap",
        )


def test_runtime_requires_postgres_but_bootstrap_allows_empty_network() -> None:
    network = _network([])
    assert (
        inventory.validate_inventory(
            network,
            [],
            cluster_id=CLUSTER_ID,
            phase="bootstrap",
        )
        == 0
    )
    with pytest.raises(inventory.InventoryError, match="no owned PostgreSQL"):
        inventory.validate_inventory(
            network,
            [],
            cluster_id=CLUSTER_ID,
            phase="runtime",
        )


def test_release_and_boot_paths_run_inventory_before_shared_network_use() -> None:
    release = (ROOT / "scripts/server-platform-release.sh").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts/platform-compose.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts/platform-bootstrap.sh").read_text(encoding="utf-8")
    vision = (ROOT / "deploy/vision-webtop/compose.yaml").read_text(encoding="utf-8")

    assert "name: fb_agent_vision\n" in vision
    assert "fb_agent_vision" in release
    assert release.count("platform-network-inventory.py") == 2
    existing_desktop = release.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        release.index('if [[ "$FIRST_RELEASE" == false ]]; then'),
    )
    existing_inventory = release.rindex(
        "platform-network-inventory.py",
        0,
        existing_desktop,
    )
    assert existing_inventory < existing_desktop
    assert release.index("candidate-cleanup", existing_inventory - 500) < (existing_inventory)
    assert "network_inventory_is_owned" in runtime
    assert runtime.index("network_inventory_is_owned") < runtime.index(
        '"${app[@]}" --profile workers up'
    )
    assert "platform-network-inventory.py" in bootstrap


def test_desktop_producers_emit_exact_vision_plane_inventory_labels() -> None:
    vision = (ROOT / "deploy/vision-webtop/compose.yaml").read_text(encoding="utf-8")
    browser = (ROOT / "deploy/compose/docker-compose.desktop-agent.yml").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install-vision-webtop.sh").read_text(encoding="utf-8")
    boot_consumer = (ROOT / "scripts/wait-for-vision-container.sh").read_text(encoding="utf-8")

    for source in (vision, browser):
        assert 'com.fb-agent.managed: "true"' in source
        assert "com.fb-agent.cluster-id: ${FB_AGENT_BOOTSTRAP_CLUSTER_ID:?" in source
        assert "com.fb-agent.purpose: vision" in source
        assert "com.fb-agent.color:" not in source
    assert "com.fb-agent.release: ${FB_AGENT_VISION_RELEASE_ID:?" in vision
    assert "com.fb-agent.release: ${RELEASE_ID:?" in browser
    assert "FB_AGENT_VISION_RELEASE_ID" in installer
    for contract in (
        "DESKTOP_WEBTOP_IMAGE",
        "DESKTOP_KASMVNC_IMAGE",
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID",
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.fb-agent.managed",
        "com.fb-agent.cluster-id",
        "com.fb-agent.purpose",
        "com.fb-agent.release",
    ):
        assert contract in boot_consumer
    assert "fb_agent_vision" in boot_consumer
    assert "kasmvnc" in boot_consumer
    assert ".HostConfig.NetworkMode" in boot_consumer
    assert "healthy" in boot_consumer
