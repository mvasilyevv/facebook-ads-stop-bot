#!/usr/bin/env python3
"""Generate the committed Python gRPC stubs with the locked project toolchain."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_ROOT = ROOT / "proto"
OUTPUT_ROOT = ROOT / "clients" / "python_grpc"
PROTO_FILES = (
    "v1/browser_session.proto",
    "v1/scanner.proto",
    "v1/meta_api.proto",
)
GENERATED_FILES = tuple(
    Path("v1") / f"{stem}{suffix}"
    for stem in ("browser_session", "scanner", "meta_api")
    for suffix in ("_pb2.py", "_pb2_grpc.py", "_pb2.pyi")
)


def _patch_package_imports(output_root: Path) -> None:
    for path in output_root.joinpath("v1").glob("*_pb2_grpc.py"):
        path.write_text(
            path.read_text(encoding="utf-8").replace("from v1 import ", "from . import "),
            encoding="utf-8",
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fb-agent-grpc-") as directory:
        temporary_root = Path(directory)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"-I{PROTO_ROOT}",
                f"--python_out={temporary_root}",
                f"--grpc_python_out={temporary_root}",
                f"--pyi_out={temporary_root}",
                *(str(PROTO_ROOT / proto) for proto in PROTO_FILES),
            ],
            check=True,
        )
        _patch_package_imports(temporary_root)

        missing = [path for path in GENERATED_FILES if not temporary_root.joinpath(path).is_file()]
        if missing:
            raise RuntimeError(f"grpc_tools did not generate expected stubs: {missing}")

        for relative_path in GENERATED_FILES:
            source = temporary_root / relative_path
            target = OUTPUT_ROOT / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


if __name__ == "__main__":
    main()
