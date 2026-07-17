from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "remove-caddy-site-block.py"
SPEC = importlib.util.spec_from_file_location("remove_caddy_site_block", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_removes_only_exact_top_level_site_block() -> None:
    original = """{
\temail ops@example.com
}

app.adpulse.su {
\treverse_proxy 127.0.0.1:8080
}

desktop.adpulse.su {
\tbasic_auth {
\t\toperator hash
\t}
\treverse_proxy 127.0.0.1:3000
}

import /etc/caddy/sites-enabled/*
"""
    updated, changed = MODULE.remove_site_block(original, "desktop.adpulse.su")

    assert changed is True
    assert "desktop.adpulse.su" not in updated
    assert "app.adpulse.su" in updated
    assert "import /etc/caddy/sites-enabled/*" in updated


def test_is_idempotent_when_legacy_site_is_already_absent() -> None:
    original = "app.adpulse.su {\n\treverse_proxy localhost:8080\n}\n"
    assert MODULE.remove_site_block(original, "desktop.adpulse.su") == (original, False)


def test_refuses_duplicate_or_unterminated_target_blocks() -> None:
    with pytest.raises(MODULE.CaddyBlockError, match="found 2"):
        MODULE.remove_site_block(
            "desktop.adpulse.su {\n}\ndesktop.adpulse.su {\n}\n",
            "desktop.adpulse.su",
        )
    with pytest.raises(MODULE.CaddyBlockError, match="unterminated"):
        MODULE.remove_site_block(
            "desktop.adpulse.su {\n\treverse_proxy localhost:3000\n",
            "desktop.adpulse.su",
        )


def test_atomic_file_update_preserves_mode(tmp_path: Path) -> None:
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(
        "desktop.adpulse.su {\n\treverse_proxy localhost:3000\n}\n\n"
        "import /etc/caddy/sites-enabled/*\n",
        encoding="utf-8",
    )
    caddyfile.chmod(0o640)

    assert MODULE.update_file(caddyfile, "desktop.adpulse.su") is True
    assert caddyfile.read_text(encoding="utf-8") == "import /etc/caddy/sites-enabled/*\n"
    assert os.stat(caddyfile).st_mode & 0o777 == 0o640
