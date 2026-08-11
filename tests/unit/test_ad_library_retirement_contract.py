"""Architecture guard for the retired Ad Library and Telegram /spy vertical."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_retired_ad_library_sources_and_contracts_are_absent() -> None:
    forbidden_paths = (
        "core/ad_library",
        "core/models/ad_library",
        "clients/python_grpc/ad_library_client.py",
        "clients/python_grpc/v1/ad_library_pb2.py",
        "clients/python_grpc/v1/ad_library_pb2.pyi",
        "clients/python_grpc/v1/ad_library_pb2_grpc.py",
        "proto/v1/ad_library.proto",
        "services/browser-agent/src/ad-library",
        "core/ai_assistant/tools/meta/get_competitor_patterns.py",
        "scripts/ad_library_keywords.py",
        "scripts/ad_library_poc.py",
        "tests/integration/test_ad_library_pipeline_e2e.py",
    )

    assert not [path for path in forbidden_paths if (PROJECT_ROOT / path).exists()]


def test_runtime_and_generation_commands_cannot_restore_ad_library() -> None:
    runtime_sources = (
        "services/browser-agent/src/index.ts",
        "core/ai_assistant/tools/meta/__init__.py",
        "core/models/__init__.py",
        "core/tasks/queue.py",
        "core/models/tasks/task_queue.py",
        "apps/cleanup_worker/main.py",
        "apps/cleanup_worker/retention.py",
        "apps/cleanup_worker/worker.py",
    )
    generation_sources = ("Makefile", ".github/workflows/verify.yml")

    for relative in (*runtime_sources, *generation_sources):
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "ad_library" not in source, relative
        assert "AdLibrary" not in source, relative


def test_fresh_schema_has_no_ad_library_objects_or_task_type() -> None:
    baseline = (PROJECT_ROOT / "migrations/versions/0001_safety_first_baseline.sql").read_text(
        encoding="utf-8"
    )
    revision = (PROJECT_ROOT / "migrations/versions/0001_safety_first_baseline.py").read_text(
        encoding="utf-8"
    )

    assert "ad_library" not in baseline
    assert "ad_library" not in revision
