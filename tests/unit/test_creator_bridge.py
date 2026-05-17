# Проверяем что bundle.load_bundle читает скомпилированный creator/index.js.
from pathlib import Path

from core.creator_bridge.bundle import load_bundle


def test_load_bundle_returns_nonempty_string(tmp_path: Path):
    fake_dist = tmp_path / "dist" / "creator"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.js").write_text("window.__fbAgent = {};\n", encoding="utf-8")
    code = load_bundle(fake_dist / "index.js")
    assert "window.__fbAgent" in code
