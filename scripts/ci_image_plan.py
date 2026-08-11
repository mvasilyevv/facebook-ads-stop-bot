#!/usr/bin/env python3
"""Deterministic content-addressed image plan for GitHub Actions.

The catalog intentionally describes production build inputs instead of using the
release commit as an image tag.  Host-only deployment changes therefore create a
new release manifest without rebuilding application or Vision images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PlanError(RuntimeError):
    """The image catalog or its inputs are invalid."""


@dataclass(frozen=True)
class ImageSpec:
    name: str
    group: str
    dockerfile: str
    context: str = "."
    exact: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    is_python: bool = False


ROOT_CONTEXT_COMMON = (".dockerignore",)
PYTHON_RUNTIME_PREFIXES = (
    "apps/",
    "clients/",
    "core/",
    "migrations/",
    "proto/",
)
PYTHON_RUNTIME_SCRIPTS = (
    "scripts/adopt-first-release.py",
    "scripts/adoption-receipt-status.py",
    "scripts/bootstrap-runtime-config.py",
    "scripts/bootstrap-vision-config.py",
    "scripts/check-database-contract.py",
    "scripts/configure-telegram-webhook.py",
    "scripts/run-migrations-locked.py",
)
WORKSPACE_MANIFESTS = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
)
SHARED_FRONTEND_PREFIXES = (
    "packages/features/",
    "packages/operator-api/",
    "packages/operator-ui/",
    "packages/shared/",
)


SPECS: dict[str, ImageSpec] = {
    "python-base": ImageSpec(
        name="python-base",
        group="base",
        dockerfile="docker/Dockerfile.python-base",
        exact=ROOT_CONTEXT_COMMON
        + PYTHON_RUNTIME_SCRIPTS
        + (
            "alembic.ini",
            "docker/Dockerfile.python-base",
            "pyproject.toml",
            "sitecustomize.py",
            "uv.lock",
        ),
        prefixes=PYTHON_RUNTIME_PREFIXES,
        # Root entrypoints and registry YAML are copied into the final image;
        # reports, source media and host automation are deliberately excluded.
        suffixes=("run_*.py", "docs/creatives/*.yaml"),
        is_python=True,
    ),
    "api": ImageSpec(
        name="api",
        group="app",
        dockerfile="docker/Dockerfile.api",
        exact=("docker/Dockerfile.api",),
        dependencies=("python-base",),
        is_python=True,
    ),
    "workers": ImageSpec(
        name="workers",
        group="app",
        dockerfile="docker/Dockerfile.workers",
        exact=("docker/Dockerfile.workers", "docker/worker-entrypoint.sh"),
        dependencies=("python-base",),
        is_python=True,
    ),
    "frontend": ImageSpec(
        name="frontend",
        group="app",
        dockerfile="docker/Dockerfile.frontend",
        exact=ROOT_CONTEXT_COMMON
        + WORKSPACE_MANIFESTS
        + ("docker/Dockerfile.frontend", "docker/nginx.conf"),
        prefixes=SHARED_FRONTEND_PREFIXES + ("frontend/",),
    ),
    "mini-app": ImageSpec(
        name="mini-app",
        group="app",
        dockerfile="docker/Dockerfile.mini-app",
        exact=ROOT_CONTEXT_COMMON
        + WORKSPACE_MANIFESTS
        + ("docker/Dockerfile.mini-app", "docker/nginx-tma.conf"),
        prefixes=SHARED_FRONTEND_PREFIXES + ("frontend-mini/",),
    ),
    "browser-agent": ImageSpec(
        name="browser-agent",
        group="app",
        dockerfile="docker/Dockerfile.browser-agent",
        exact=ROOT_CONTEXT_COMMON
        + (
            "docker/Dockerfile.browser-agent",
            "services/browser-agent/package-lock.json",
            "services/browser-agent/package.json",
            "services/browser-agent/tsconfig.json",
        ),
        prefixes=("proto/v1/", "services/browser-agent/src/"),
    ),
    "vision-webtop": ImageSpec(
        name="vision-webtop",
        group="desktop",
        dockerfile="deploy/vision-webtop/Dockerfile",
        context="deploy/vision-webtop",
        prefixes=("deploy/vision-webtop/",),
    ),
}

RUNTIME_MANIFEST_KEYS = {
    "api": "API_IMAGE",
    "workers": "WORKERS_IMAGE",
    "frontend": "FRONTEND_IMAGE",
    "mini-app": "MINI_APP_IMAGE",
    "browser-agent": "BROWSER_AGENT_IMAGE",
    "vision-webtop": "DESKTOP_WEBTOP_IMAGE",
}


def _tracked_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise PlanError(f"cannot enumerate tracked files: {detail}")
    return tuple(
        path.decode("utf-8", errors="strict") for path in result.stdout.split(b"\0") if path
    )


def _matches(spec: ImageSpec, relative: str) -> bool:
    if relative in spec.exact:
        return True
    if any(relative.startswith(prefix) for prefix in spec.prefixes):
        return True
    for pattern in spec.suffixes:
        prefix, suffix = pattern.split("*", 1)
        if relative.startswith(prefix) and relative.endswith(suffix):
            return True
    return False


def _mode_marker(path: Path) -> bytes:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return b"symlink"
    if stat.S_ISREG(mode):
        return b"file+x" if mode & 0o111 else b"file"
    raise PlanError(f"unsupported tracked input type: {path}")


def _hash_file(hasher: "hashlib._Hash", root: Path, relative: str) -> None:
    path = root / relative
    if not path.exists() and not path.is_symlink():
        raise PlanError(f"tracked image input is missing: {relative}")
    marker = _mode_marker(path)
    payload = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    for value in (relative.encode(), marker, payload):
        hasher.update(len(value).to_bytes(8, "big"))
        hasher.update(value)


def compute_hashes(root: Path) -> dict[str, str]:
    tracked = _tracked_files(root)
    resolved: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> str:
        if name in resolved:
            return resolved[name]
        if name in resolving:
            raise PlanError(f"cyclic image dependency: {name}")
        spec = SPECS.get(name)
        if spec is None:
            raise PlanError(f"unknown image: {name}")
        resolving.add(name)
        matched = sorted(path for path in tracked if _matches(spec, path))
        missing_exact = sorted(set(spec.exact) - set(matched))
        if missing_exact:
            raise PlanError(f"{name} inputs are missing: {', '.join(missing_exact)}")
        if not matched:
            raise PlanError(f"{name} has no tracked build inputs")

        hasher = hashlib.sha256()
        hasher.update(b"fb-agent-image-context-v1\0")
        hasher.update(name.encode())
        for dependency in spec.dependencies:
            dependency_hash = resolve(dependency)
            hasher.update(b"\0dependency\0")
            hasher.update(dependency.encode())
            hasher.update(bytes.fromhex(dependency_hash))
        for relative in matched:
            _hash_file(hasher, root, relative)
        resolving.remove(name)
        resolved[name] = hasher.hexdigest()
        return resolved[name]

    for image_name in SPECS:
        resolve(image_name)
    return resolved


def build_matrix(root: Path, group: str) -> dict[str, list[dict[str, object]]]:
    hashes = compute_hashes(root)
    include: list[dict[str, object]] = []
    for spec in SPECS.values():
        if spec.group != group:
            continue
        item: dict[str, object] = {
            "name": spec.name,
            "image_suffix": spec.name,
            "dockerfile": spec.dockerfile,
            "context": spec.context,
            "tag": f"{spec.name}-ctx-{hashes[spec.name]}",
            "is_python": spec.is_python,
        }
        if spec.dependencies:
            dependency = spec.dependencies[0]
            item["base_tag"] = f"{dependency}-ctx-{hashes[dependency]}"
        include.append(item)
    return {"include": include}


def _validate_image_ref(value: str, label: str) -> str:
    value = value.strip()
    if "@sha256:" not in value:
        raise PlanError(f"image reference is not immutable: {label}")
    name, digest = value.rsplit("@sha256:", 1)
    if (
        not name
        or any(char.isspace() for char in name)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise PlanError(f"image reference is invalid: {label}")
    return value


def _read_image_ref(path: Path) -> str:
    return _validate_image_ref(path.read_text(encoding="utf-8"), str(path))


def write_manifest(refs_dir: Path, release_id: str, output: Path, redis_image: str) -> None:
    if (
        not release_id
        or release_id in {".", ".."}
        or any(not (char.isalnum() or char in "._-") for char in release_id)
    ):
        raise PlanError("release ID contains unsupported characters")
    images: dict[str, str] = {}
    for image_name, env_key in RUNTIME_MANIFEST_KEYS.items():
        images[env_key] = _read_image_ref(refs_dir / f"{image_name}.ref")
    images["REDIS_IMAGE"] = _validate_image_ref(redis_image, "redis image")
    manifest = {
        "schema": "fb-agent-release/v1",
        "release_id": release_id,
        "images": images,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary.chmod(0o600)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subcommands = parser.add_subparsers(dest="command", required=True)

    matrix = subcommands.add_parser("matrix")
    matrix.add_argument("--group", choices=("base", "app", "desktop"), required=True)

    image_hash = subcommands.add_parser("hash")
    image_hash.add_argument("--image", choices=tuple(SPECS), required=True)

    manifest = subcommands.add_parser("manifest")
    manifest.add_argument("--refs-dir", type=Path, required=True)
    manifest.add_argument("--release-id", required=True)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--redis-image", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "matrix":
            print(json.dumps(build_matrix(root, args.group), separators=(",", ":")))
        elif args.command == "hash":
            print(f"{args.image}-ctx-{compute_hashes(root)[args.image]}")
        else:
            write_manifest(args.refs_dir, args.release_id, args.output, args.redis_image)
    except (OSError, PlanError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
