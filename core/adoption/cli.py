"""Non-secret CLI implementation for adoption bundle operations."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from core.adoption.bundle import canonical_bundle_json, parse_adoption_bundle_json
from core.adoption.profiles import SOURCE_PROFILES, get_source_profile
from core.adoption.service import apply_adoption_bundle, export_legacy_bundle

SOURCE_DSN_ENV = "FB_AGENT_ADOPTION_SOURCE_DATABASE_URL"
TARGET_DSN_ENV = "FB_AGENT_ADOPTION_TARGET_DATABASE_URL"
IMPORT_CONFIRMATION = "IMPORT adoption-bundle/v1"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AdoptionCliError(RuntimeError):
    """A sanitized operator-facing CLI failure."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Do not echo rejected command-line values, which may contain a DSN."""

    def error(self, _message: str) -> None:
        raise AdoptionCliError("invalid adoption command arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="adoption-bundle",
        description="Export and adopt allowlisted FB Agent configuration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument(
        "--source-profile",
        choices=sorted(SOURCE_PROFILES),
        required=True,
    )

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--input", type=Path, required=True)

    dry_run_parser = subparsers.add_parser("dry-run")
    dry_run_parser.add_argument("--input", type=Path, required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--source-fingerprint", required=True)
    import_parser.add_argument("--confirm", required=True)
    return parser


def _database_url(env_name: str) -> str:
    raw = os.environ.get(env_name, "")
    if not raw:
        raise AdoptionCliError("required adoption database environment is not configured")
    try:
        parsed = make_url(raw)
    except Exception as exc:
        raise AdoptionCliError("adoption database environment is invalid") from exc
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.drivername not in {"postgresql", "postgresql+asyncpg"}
        or not parsed.username
        or not parsed.host
        or not parsed.database
    ):
        raise AdoptionCliError("adoption database environment is invalid")
    return parsed.set(
        drivername="postgresql+asyncpg",
        port=parsed.port or 5432,
    ).render_as_string(hide_password=False)


def _engine(env_name: str) -> AsyncEngine:
    return create_async_engine(_database_url(env_name), poolclass=NullPool)


def _read_bundle(path: Path):
    try:
        size = path.stat().st_size
        if size > MAX_BUNDLE_BYTES:
            raise AdoptionCliError("adoption bundle exceeds the size limit")
        payload = path.read_bytes()
    except AdoptionCliError:
        raise
    except OSError as exc:
        raise AdoptionCliError("adoption bundle cannot be read") from exc
    return parse_adoption_bundle_json(payload)


def write_private_bundle(path: Path, payload: str) -> None:
    """Create one mode-0600 file and refuse every overwrite/symlink target."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise AdoptionCliError("adoption output already exists") from exc
    except OSError as exc:
        raise AdoptionCliError("adoption output cannot be created") from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)
        finally:
            raise


async def _run(args: argparse.Namespace) -> str:
    if args.command == "validate":
        bundle = _read_bundle(args.input)
        return f"bundle valid; source_fingerprint={bundle.source_fingerprint}"

    if args.command == "export":
        engine = _engine(SOURCE_DSN_ENV)
        try:
            bundle = await export_legacy_bundle(
                engine,
                profile=get_source_profile(args.source_profile),
            )
        finally:
            await engine.dispose()
        write_private_bundle(args.output, canonical_bundle_json(bundle))
        return f"export complete; source_fingerprint={bundle.source_fingerprint}"

    bundle = _read_bundle(args.input)
    if args.command == "import":
        if args.confirm != IMPORT_CONFIRMATION:
            raise AdoptionCliError("exact import confirmation phrase is required")
        if not _SHA256_RE.fullmatch(args.source_fingerprint):
            raise AdoptionCliError("source fingerprint confirmation is invalid")
        confirmed_fingerprint = args.source_fingerprint
        dry_run = False
    else:
        confirmed_fingerprint = None
        dry_run = True

    engine = _engine(TARGET_DSN_ENV)
    try:
        result = await apply_adoption_bundle(
            engine,
            bundle=bundle,
            dry_run=dry_run,
            confirmed_source_fingerprint=confirmed_fingerprint,
        )
    finally:
        await engine.dispose()
    outcome = "dry-run verified" if result.dry_run else "import complete"
    return f"{outcome}; source_fingerprint={result.source_fingerprint}"


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        message = asyncio.run(_run(args))
    except Exception:
        print("adoption command failed", file=sys.stderr)
        return 1
    print(message)
    return 0


__all__ = [
    "IMPORT_CONFIRMATION",
    "SOURCE_DSN_ENV",
    "TARGET_DSN_ENV",
    "AdoptionCliError",
    "main",
    "write_private_bundle",
]
