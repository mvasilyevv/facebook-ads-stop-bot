from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_desktop_stack_is_healthy_before_server_release_switch() -> None:
    script = (ROOT / "scripts/deploy-server.sh").read_text(encoding="utf-8")

    desktop_install = script.index("install-vision-webtop.sh")
    release_switch = script.index("server-release.sh")
    caddy_install = script.index("install-server-units.sh")

    assert desktop_install < release_switch < caddy_install


def test_ci_artifact_digest_is_forwarded_into_production_environment() -> None:
    script = (ROOT / "scripts/deploy-server.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert 'DESKTOP_WEBTOP_IMAGE_OVERRIDE="${DESKTOP_WEBTOP_IMAGE:-}"' in script
    assert "--desktop-webtop-image" in script
    assert "DESKTOP_DOCKER_CONFIG_OVERRIDE" in script
    assert "build-desktop:" in workflow
    assert "deploy/vision-webtop/Dockerfile" in workflow
    assert "${{ steps.build.outputs.digest }}" in workflow
    assert "DESKTOP_WEBTOP_IMAGE: ${{ needs.build-desktop.outputs.image_ref }}" in workflow
    assert "DOCKER_CONFIG='$DESKTOP_DOCKER_CONFIG' docker login ghcr.io" in workflow
