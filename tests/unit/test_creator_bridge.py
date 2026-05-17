# Проверяем что bundle.load_bundle читает скомпилированный creator/index.js.
from pathlib import Path

import pytest

from core.creator_bridge.bundle import load_bundle


def test_load_bundle_returns_nonempty_string(tmp_path: Path):
    fake_dist = tmp_path / "dist" / "creator"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.js").write_text("window.__fbAgent = {};\n", encoding="utf-8")
    code = load_bundle(fake_dist / "index.js")
    assert "window.__fbAgent" in code


# Проверяем что FileNotFoundError содержит подсказку про npm run build.
def test_load_bundle_raises_when_missing(tmp_path: Path):
    missing = tmp_path / "nope.js"
    with pytest.raises(FileNotFoundError) as exc:
        load_bundle(missing)
    assert "npm run build" in str(exc.value)


# Проверяем что дефолтный путь bundle loader'а указывает на dist/creator/index.js.
def test_load_bundle_default_path_points_to_repo_dist():
    from core.creator_bridge.bundle import _DEFAULT_PATH

    assert _DEFAULT_PATH.parts[-3:] == ("dist", "creator", "index.js")
