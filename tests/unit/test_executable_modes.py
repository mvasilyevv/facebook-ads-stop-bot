from pathlib import Path

from scripts.validate_executable_modes import validate


def _git(root: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_validator_reads_executable_bit_from_git_index(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    script = root / "tool"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    _git(root, "init", "-q")
    _git(root, "add", "tool")

    assert validate(root, ["tool"]) == []

    _git(root, "update-index", "--chmod=-x", "tool")
    assert validate(root, ["tool"]) == ["tool: expected Git mode 100755, found 100644"]


def test_validator_rejects_untracked_entrypoint(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")

    assert validate(root, ["scripts/fbctl"]) == ["scripts/fbctl: not tracked"]
