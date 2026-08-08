"""Architecture guard for the retired Vision Creator/Recorder runtime."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_retired_browser_creator_sources_and_bindings_are_absent() -> None:
    forbidden_paths = (
        "proto/v1/creator.proto",
        "clients/python_grpc/v1/creator_pb2.py",
        "clients/python_grpc/v1/creator_pb2.pyi",
        "clients/python_grpc/v1/creator_pb2_grpc.py",
        "services/browser-agent/src/creator",
        "services/browser-agent/src/creator-injector.ts",
        "services/browser-agent/src/creator-service.ts",
        "services/browser-agent/generated",
    )

    assert not [path for path in forbidden_paths if (PROJECT_ROOT / path).exists()]


def test_browser_agent_has_no_retired_creator_runtime_wiring() -> None:
    runtime_sources = (
        PROJECT_ROOT / "clients/python_grpc/client.py",
        PROJECT_ROOT / "services/browser-agent/src/index.ts",
        PROJECT_ROOT / "services/browser-agent/src/session-manager.ts",
    )
    forbidden_tokens = (
        "creator_pb2",
        "CreatorService",
        "_creator_stub",
        "creator-service",
        "creator-injector",
        "injectCreator",
        "start_recording",
        "stop_recording",
        "get_recorder_status",
        "def run_plan(",
    )

    for source_path in runtime_sources:
        source = source_path.read_text(encoding="utf-8")
        assert not [token for token in forbidden_tokens if token in source], source_path


def test_proto_and_build_commands_cannot_recreate_retired_creator_artifacts() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "services/browser-agent/package.json").read_text(encoding="utf-8")
    )
    scripts = manifest["scripts"]
    dev_dependencies = manifest["devDependencies"]

    assert "bundle:creator" not in scripts
    assert "proto" not in scripts
    assert "clean" in scripts["build"]
    assert "grpc-tools" not in dev_dependencies
    assert "grpc_tools_node_protoc_ts" not in dev_dependencies

    generation_sources = (
        PROJECT_ROOT / "Makefile",
        PROJECT_ROOT / ".github/workflows/deploy.yml",
    )
    for source_path in generation_sources:
        source = source_path.read_text(encoding="utf-8")
        assert "proto/v1/creator.proto" not in source

    assert "npm run proto" not in (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")


def test_meta_api_campaign_creator_remains_available() -> None:
    assert (PROJECT_ROOT / "apps/campaign_creator_worker/main.py").is_file()
    assert (PROJECT_ROOT / "run_campaign_creator_worker.py").is_file()
