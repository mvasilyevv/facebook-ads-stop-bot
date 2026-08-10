from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_build_and_runtime_never_receive_master_api_key() -> None:
    active_build_files = (
        ROOT / "docker" / "Dockerfile.frontend",
        ROOT / "docker-compose.yml",
        ROOT / "frontend" / "src" / "vite-env.d.ts",
        ROOT / "frontend" / "src" / "main.tsx",
    )
    for path in active_build_files:
        assert "VITE_API_KEY" not in path.read_text(encoding="utf-8"), path

    source_files = [
        path
        for path in (ROOT / "frontend" / "src").rglob("*")
        if path.suffix in {".ts", ".tsx"} and "/tests/" not in str(path)
    ]
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        assert "X-API-Key" not in source, path
        assert "VITE_API_KEY" not in source, path
        assert "?api_key=" not in source, path

    assert not (ROOT / "frontend" / "src" / "stores" / "auth.ts").exists()


def test_desktop_websocket_url_contains_no_secret_query() -> None:
    source = (ROOT / "packages" / "operator-api" / "src" / "realtime.ts").read_text(
        encoding="utf-8"
    )

    assert 'path = "/ws/operator"' in source
    assert "new WebSocket(url)" in source
    assert "encodeURIComponent(apiKey)" not in source
    assert "api_key=" not in source
