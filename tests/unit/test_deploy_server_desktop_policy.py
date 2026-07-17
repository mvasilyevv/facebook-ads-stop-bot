from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_stack_is_healthy_before_server_release_switch() -> None:
    script = (ROOT / "scripts/deploy-server.sh").read_text(encoding="utf-8")

    desktop_install = script.index("install-vision-webtop.sh")
    release_switch = script.index("server-release.sh")
    caddy_install = script.index("install-server-units.sh")

    assert desktop_install < release_switch < caddy_install
