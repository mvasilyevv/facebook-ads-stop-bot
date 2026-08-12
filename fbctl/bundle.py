"""Deterministic stdlib zipapp releases and embedded runtime resources."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fbctl.errors import FbctlError
from fbctl.files import IMAGE_DIGEST, require_release_id, sha256_file

BUNDLE_SCHEMA = "fb-agent-zipapp/v1"
PREFLIGHT_BUNDLE_SCHEMA = "fb-agent-preflight-zipapp/v1"
RELEASE_SCHEMA = "fb-agent-release/v1"
IMAGE_KEYS = (
    "API_IMAGE",
    "WORKERS_IMAGE",
    "FRONTEND_IMAGE",
    "MINI_APP_IMAGE",
    "BROWSER_AGENT_IMAGE",
    "DESKTOP_WEBTOP_IMAGE",
    "REDIS_IMAGE",
)
RESOURCE_FILES: dict[str, str] = {
    "deploy/compose/docker-compose.infra.yml": "deploy/compose/docker-compose.infra.yml",
    "deploy/compose/docker-compose.jobs.yml": "deploy/compose/docker-compose.jobs.yml",
    "deploy/compose/docker-compose.app.yml": "deploy/compose/docker-compose.app.yml",
    "deploy/compose/docker-compose.desktop-agent.yml": (
        "deploy/compose/docker-compose.desktop-agent.yml"
    ),
    "deploy/caddy/app.adpulse.su.caddy": "deploy/caddy/app.adpulse.su.caddy",
    "deploy/caddy/desktop.adpulse.su.caddy": "deploy/caddy/desktop.adpulse.su.caddy",
    "deploy/caddy/Caddyfile.validation": "deploy/caddy/Caddyfile.validation",
    "deploy/systemd/caddy-fb-agent-env.conf": "deploy/systemd/caddy-fb-agent-env.conf",
}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
PREFLIGHT_MODULES = (
    "__init__.py",
    "adoption.py",
    "bundle.py",
    "config.py",
    "errors.py",
    "files.py",
    "identity.py",
    "preflight.py",
    "runner.py",
    "vision_profile.py",
)


@dataclass(frozen=True)
class BundleMetadata:
    schema: str
    release_id: str
    sha256: str
    entries: tuple[dict[str, object], ...]


def read_release_manifest(
    path: Path,
    *,
    expected_release_id: str | None = None,
) -> dict[str, object]:
    try:
        release = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FbctlError(f"release manifest is unreadable or invalid: {path}") from exc
    if not isinstance(release, dict) or set(release) != {"schema", "release_id", "images"}:
        raise FbctlError("release manifest must contain exactly schema, release_id and images")
    if release.get("schema") != RELEASE_SCHEMA:
        raise FbctlError("release manifest uses an unsupported schema")
    release_id = require_release_id(str(release.get("release_id", "")))
    if expected_release_id is not None and release_id != expected_release_id:
        raise FbctlError("release manifest belongs to a different release")
    raw_images = release.get("images")
    if not isinstance(raw_images, dict) or set(raw_images) != set(IMAGE_KEYS):
        raise FbctlError("release manifest images must contain the exact production image set")
    images: dict[str, str] = {}
    for key in IMAGE_KEYS:
        value = raw_images[key]
        if not isinstance(value, str) or not IMAGE_DIGEST.fullmatch(value):
            raise FbctlError(f"{key} must be an immutable image@sha256 reference")
        images[key] = value
    return {"schema": RELEASE_SCHEMA, "release_id": release_id, "images": images}


def build_bundle(
    *,
    source_root: Path,
    output: Path,
    release_id: str,
    release_manifest: Path,
) -> dict[str, object]:
    source_root = source_root.resolve(strict=True)
    release_id = require_release_id(release_id)
    release = read_release_manifest(release_manifest, expected_release_id=release_id)
    payloads: dict[str, tuple[bytes, int]] = {
        "__main__.py": (
            b"from fbctl.__main__ import main\nraise SystemExit(main())\n",
            0o644,
        ),
        "fbctl/resources/release.json": (
            json.dumps(release, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            0o400,
        ),
    }
    for source in sorted((source_root / "fbctl").glob("*.py")):
        if source.is_symlink() or not source.is_file():
            raise FbctlError(f"zipapp module is unsafe: {source}")
        payloads[f"fbctl/{source.name}"] = (source.read_bytes(), 0o644)
    for archive_relative, source_relative in sorted(RESOURCE_FILES.items()):
        source = source_root / source_relative
        if source.is_symlink() or not source.is_file():
            raise FbctlError(f"zipapp resource is missing or unsafe: {source}")
        payloads[f"fbctl/resources/{archive_relative}"] = (source.read_bytes(), 0o644)

    manifest_entries = [
        {
            "path": name,
            "mode": mode,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, (payload, mode) in sorted(payloads.items())
    ]
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "release_id": release_id,
        "entries": manifest_entries,
    }
    payloads["fbctl/resources/artifact-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        0o400,
    )

    _write_bundle(output, payloads)
    return {
        "schema": BUNDLE_SCHEMA,
        "release_id": release_id,
        "artifact": os.fspath(output),
        "sha256": sha256_file(output),
        "files": len(manifest_entries),
    }


def build_preflight_bundle(
    *,
    source_root: Path,
    output: Path,
    release_id: str,
) -> dict[str, object]:
    """Build the deterministic, secret-free host identity preflight zipapp."""

    source_root = source_root.resolve(strict=True)
    release_id = require_release_id(release_id)
    payloads: dict[str, tuple[bytes, int]] = {
        "__main__.py": (
            b"from fbctl.preflight import main\nraise SystemExit(main())\n",
            0o644,
        )
    }
    for name in PREFLIGHT_MODULES:
        source = source_root / "fbctl" / name
        if source.is_symlink() or not source.is_file():
            raise FbctlError(f"preflight zipapp module is missing or unsafe: {source}")
        payloads[f"fbctl/{name}"] = (source.read_bytes(), 0o644)
    entries = [
        {
            "path": name,
            "mode": mode,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, (payload, mode) in sorted(payloads.items())
    ]
    payloads["fbctl/resources/artifact-manifest.json"] = (
        json.dumps(
            {
                "schema": PREFLIGHT_BUNDLE_SCHEMA,
                "release_id": release_id,
                "entries": entries,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n",
        0o400,
    )
    _write_bundle(output, payloads)
    return {
        "schema": PREFLIGHT_BUNDLE_SCHEMA,
        "release_id": release_id,
        "artifact": os.fspath(output),
        "sha256": sha256_file(output),
        "files": len(entries),
    }


def _write_bundle(output: Path, payloads: dict[str, tuple[bytes, int]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, (payload, mode) in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.chmod(temporary, 0o500)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_bundle(path: Path) -> BundleMetadata:
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise FbctlError(f"release zipapp cannot be opened: {path}") from exc
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise FbctlError("release zipapp contains duplicate paths")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise FbctlError(f"release zipapp contains unsafe path: {name}")
        try:
            manifest = json.loads(archive.read("fbctl/resources/artifact-manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeError) as exc:
            raise FbctlError("release zipapp manifest is missing or invalid") from exc
        schema = manifest.get("schema")
        if schema not in {BUNDLE_SCHEMA, PREFLIGHT_BUNDLE_SCHEMA}:
            raise FbctlError("release zipapp uses an unsupported schema")
        release_id = require_release_id(str(manifest.get("release_id", "")))
        raw_entries = manifest.get("entries")
        if not isinstance(raw_entries, list):
            raise FbctlError("release zipapp manifest entries are invalid")
        expected_names = {"fbctl/resources/artifact-manifest.json"}
        entries: list[dict[str, object]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise FbctlError("release zipapp manifest entry is invalid")
            name = str(raw.get("path", ""))
            expected_names.add(name)
            try:
                info = archive.getinfo(name)
                payload = archive.read(name)
                expected_mode = int(raw["mode"])
                expected_size = int(raw["size"])
                expected_sha = str(raw["sha256"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FbctlError(f"release zipapp manifest entry is incomplete: {name}") from exc
            actual_mode = (info.external_attr >> 16) & 0o777
            if actual_mode != expected_mode:
                raise FbctlError(f"release zipapp mode mismatch: {name}")
            if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha:
                raise FbctlError(f"release zipapp digest mismatch: {name}")
            entries.append(dict(raw))
        if set(names) != expected_names:
            raise FbctlError("release zipapp contains files outside its manifest")
        return BundleMetadata(str(schema), release_id, sha256_file(path), tuple(entries))


def embedded_release() -> dict[str, object]:
    try:
        payload = (
            importlib.resources.files("fbctl")
            .joinpath("resources/release.json")
            .read_text(encoding="utf-8")
        )
        release = json.loads(payload)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError) as exc:
        raise FbctlError("fbctl must run from a release zipapp for deploy operations") from exc
    if release.get("schema") != RELEASE_SCHEMA:
        raise FbctlError("embedded release descriptor is invalid")
    release_id = require_release_id(str(release.get("release_id", "")))
    images = release.get("images")
    if not isinstance(images, dict) or set(images) != set(IMAGE_KEYS):
        raise FbctlError("embedded release images are invalid")
    for key, value in images.items():
        if not isinstance(value, str) or not IMAGE_DIGEST.fullmatch(value):
            raise FbctlError(f"embedded {key} is not digest pinned")
    return {"schema": RELEASE_SCHEMA, "release_id": release_id, "images": dict(images)}


def materialize_candidate(destination: Path) -> dict[str, object]:
    release = embedded_release()
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise FbctlError("candidate path is unsafe")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, mode=0o700)
    resources = importlib.resources.files("fbctl").joinpath("resources")
    for relative in (*RESOURCE_FILES, "release.json", "artifact-manifest.json"):
        source = resources.joinpath(*PurePosixPath(relative).parts)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o400 if relative.endswith(".json") else 0o644
        target.write_bytes(source.read_bytes())
        os.chmod(target, mode)
    archive = Path(sys.argv[0])
    if archive.is_file() and zipfile.is_zipfile(archive):
        shutil.copyfile(archive, destination / "fbctl.pyz")
        os.chmod(destination / "fbctl.pyz", 0o500)
    return release


def verify_materialized_resources(base: Path) -> None:
    """Verify every extracted runtime asset against the signed bundle inventory."""
    try:
        manifest = json.loads((base / "artifact-manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FbctlError("materialized artifact manifest is missing or invalid") from exc
    if manifest.get("schema") != BUNDLE_SCHEMA or not isinstance(manifest.get("entries"), list):
        raise FbctlError("materialized artifact manifest uses an invalid schema")
    indexed = {
        str(entry.get("path")): entry for entry in manifest["entries"] if isinstance(entry, dict)
    }
    for relative in (*RESOURCE_FILES, "release.json"):
        archive_name = f"fbctl/resources/{relative}"
        entry = indexed.get(archive_name)
        path = base / relative
        if entry is None or path.is_symlink() or not path.is_file():
            raise FbctlError(f"materialized release asset is missing: {relative}")
        try:
            expected_size = int(entry["size"])
            expected_sha = str(entry["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FbctlError(f"materialized release inventory is invalid: {relative}") from exc
        if path.stat().st_size != expected_size or sha256_file(path) != expected_sha:
            raise FbctlError(f"materialized release asset digest mismatch: {relative}")
