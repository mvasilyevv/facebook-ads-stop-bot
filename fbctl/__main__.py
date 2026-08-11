"""Command-line interface for the FB Agent production control plane."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from fbctl.bundle import build_bundle
from fbctl.controller import (
    REHEARSAL_FAILPOINTS,
    DeployOptions,
    ProductionController,
    bootstrap_host,
)
from fbctl.errors import FbctlError
from fbctl.operations import (
    cleanup,
    db_check,
    db_migrate,
    db_new,
    db_status,
    doctor,
    logs,
    restart,
    status,
)
from fbctl.publish import publish
from fbctl.runner import SubprocessRunner

DEFAULT_ROOT = Path("/opt/fb-agent")


def _path(value: str) -> Path:
    return Path(value)


def _docker_config_default() -> Path | None:
    value = os.environ.get("DEPLOY_DOCKER_CONFIG") or os.environ.get("DOCKER_CONFIG")
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fbctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle = subparsers.add_parser("bundle", help="build a deterministic release zipapp")
    bundle.add_argument("--output", required=True, type=_path)
    bundle.add_argument("--release-id", required=True)
    bundle.add_argument("--release-manifest", required=True, type=_path)
    bundle.add_argument("--source-root", type=_path, default=Path.cwd())

    publish_parser = subparsers.add_parser("publish", help="publish and deploy over SSH")
    publish_parser.add_argument("--host", required=True)
    publish_parser.add_argument("--bundle", required=True, type=_path)
    publish_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    publish_parser.add_argument("--source-env-stdin", action="store_true")
    publish_parser.add_argument("--docker-config", type=_path, default=_docker_config_default())
    publish_parser.add_argument("--bootstrap", action="store_true")
    publish_parser.add_argument("--reuse-existing-caddy-credentials", action="store_true")
    publish_parser.add_argument("--adoption-bundle-remote", type=_path)
    publish_parser.add_argument("--desktop-profile-seed-remote", type=_path)
    publish_parser.add_argument("--enable-scanning", action="store_true")

    bootstrap = subparsers.add_parser("bootstrap", help="one-time host provisioning")
    bootstrap.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    bootstrap.add_argument("--source-env", required=True, type=_path)
    bootstrap.add_argument("--adoption-bundle", type=_path)
    bootstrap.add_argument("--desktop-profile-seed", type=_path)
    bootstrap.add_argument("--rehearsal", action="store_true")
    bootstrap.add_argument("--reuse-existing-caddy-credentials", action="store_true")
    bootstrap.add_argument("--docker-config", type=_path, default=_docker_config_default())

    deploy = subparsers.add_parser("deploy", help="apply one single-slot release")
    deploy.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    deploy.add_argument("--docker-config", type=_path, default=_docker_config_default())
    deploy.add_argument("--rehearsal", action="store_true")
    deploy.add_argument("--fail-after-step")
    deploy.add_argument("--list-failpoints", action="store_true")
    deploy.add_argument("--enable-scanning", action="store_true")

    doctor_parser = subparsers.add_parser("doctor", help="validate host prerequisites")
    doctor_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    doctor_parser.add_argument("--docker-config", type=_path, default=_docker_config_default())
    doctor_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status_parser = subparsers.add_parser("status", help="show active runtime evidence")
    status_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    status_parser.add_argument("--docker-config", type=_path, default=_docker_config_default())
    status_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    logs_parser = subparsers.add_parser("logs", help="show allowlisted service logs")
    logs_parser.add_argument("service")
    logs_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    logs_parser.add_argument("--lines", type=int, default=200)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument("--docker-config", type=_path, default=_docker_config_default())

    restart_parser = subparsers.add_parser("restart", help="restart one allowlisted service")
    restart_parser.add_argument("service")
    restart_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    restart_parser.add_argument("--docker-config", type=_path, default=_docker_config_default())

    cleanup_parser = subparsers.add_parser("cleanup", help="remove stale temporary releases")
    cleanup_parser.add_argument("--root", type=_path, default=DEFAULT_ROOT)
    cleanup_parser.add_argument("--max-age-hours", type=int, default=24)

    db_parser = subparsers.add_parser("db", help="inspect and operate the canonical database")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    for name in ("status", "check", "migrate"):
        command = db_subparsers.add_parser(name)
        command.add_argument("--root", type=_path, default=DEFAULT_ROOT)
        command.add_argument("--docker-config", type=_path, default=_docker_config_default())
    new = db_subparsers.add_parser("new")
    new.add_argument("revision_name")
    new.add_argument("--source-root", type=_path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except FbctlError as exc:
        payload = {
            "status": "FAILED",
            "step": exc.step,
            "error": str(exc),
        }
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"status": "FAILED", "error": "interrupted"}), file=sys.stderr)
        return 130
    if result is not None:
        print(json.dumps(result, indent=2, sort_keys=True))
    if args.command in {"doctor", "status"} and result is not None:
        return 0 if result.get("status") == "READY" else 1
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, object] | None:
    runner = SubprocessRunner()
    if args.command == "bundle":
        return build_bundle(
            source_root=args.source_root,
            output=args.output,
            release_id=args.release_id,
            release_manifest=args.release_manifest,
        )
    if args.command == "publish":
        return publish(
            host=args.host,
            bundle=args.bundle,
            root=args.root,
            source_env_stdin=args.source_env_stdin,
            docker_config=args.docker_config,
            bootstrap=args.bootstrap,
            adoption_bundle_remote=args.adoption_bundle_remote,
            desktop_profile_seed_remote=args.desktop_profile_seed_remote,
            enable_scanning=args.enable_scanning,
            reuse_existing_caddy_credentials=args.reuse_existing_caddy_credentials,
            runner=runner,
        )
    if args.command == "bootstrap":
        return bootstrap_host(
            runner=runner,
            root=args.root,
            source_env=args.source_env,
            adoption_bundle=args.adoption_bundle,
            desktop_profile_seed=args.desktop_profile_seed,
            docker_config=args.docker_config,
            rehearsal=args.rehearsal,
            reuse_existing_caddy_credentials=args.reuse_existing_caddy_credentials,
        )
    if args.command == "deploy":
        if args.list_failpoints:
            return {
                "schema": "fb-agent-rehearsal-failpoints/v1",
                "failpoints": list(REHEARSAL_FAILPOINTS),
            }
        deployment = ProductionController(runner=runner).deploy(
            DeployOptions(
                root=args.root,
                docker_config=args.docker_config,
                rehearsal=args.rehearsal,
                fail_after_step=args.fail_after_step,
                enable_scanning=args.enable_scanning,
            )
        )
        return deployment.as_dict()
    if args.command == "doctor":
        return doctor(root=args.root, runner=runner, docker_config=args.docker_config)
    if args.command == "status":
        return status(root=args.root, runner=runner, docker_config=args.docker_config)
    if args.command == "logs":
        logs(
            root=args.root,
            service=args.service,
            lines=args.lines,
            follow=args.follow,
            runner=runner,
            docker_config=args.docker_config,
        )
        return None
    if args.command == "restart":
        return restart(
            root=args.root,
            service=args.service,
            runner=runner,
            docker_config=args.docker_config,
        )
    if args.command == "cleanup":
        return cleanup(root=args.root, max_age_hours=args.max_age_hours)
    if args.command == "db":
        if args.db_command == "status":
            return db_status(root=args.root, runner=runner, docker_config=args.docker_config)
        if args.db_command == "check":
            return db_check(root=args.root, runner=runner, docker_config=args.docker_config)
        if args.db_command == "migrate":
            return db_migrate(root=args.root, runner=runner, docker_config=args.docker_config)
        if args.db_command == "new":
            return db_new(source_root=args.source_root, message=args.revision_name, runner=runner)
    raise FbctlError("unknown command")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
