#!/usr/bin/env python3
"""Run one exhaustive, deterministic slice of the static release suite.

Every test module under backend/tests and edge_sync is discovered at runtime.
The destructive PG16 runtime module belongs to its separate CI job only.
"""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


CLOUD = Path(__file__).resolve().parents[1]
SCOPES = (CLOUD / "backend/tests", CLOUD / "edge_sync")
RUNTIME = CLOUD / "backend/tests/test_postgresql16_release_gate.py"


def candidates() -> tuple[Path, ...]:
    paths = tuple(sorted({path.resolve() for root in SCOPES for path in root.rglob("test_*.py")
                          if path.is_file() and not path.is_symlink()}))
    if RUNTIME not in paths or len(paths) < 2:
        raise RuntimeError("static_test_discovery_incomplete")
    return tuple(path for path in paths if path != RUNTIME)


def shard_files(index: int, count: int) -> tuple[Path, ...]:
    if not 0 <= index < count <= 8:
        raise ValueError("invalid_static_shard")
    paths = candidates()
    return tuple(path for path in paths if int.from_bytes(hashlib.sha256(
        str(path.relative_to(CLOUD)).encode()).digest()[:8], "big") % count == index)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--list", action="store_true", help="print selected paths without pytest")
    args = parser.parse_args(argv)
    selected = shard_files(args.index, args.count)
    if not selected:
        raise RuntimeError("static_shard_empty")
    relative = [str(path.relative_to(CLOUD)) for path in selected]
    if args.list:
        print("\n".join(relative))
        return 0
    if shutil.which("node") is None:
        raise RuntimeError("node_runtime_required_for_static_contract_tests")
    print(f"static shard {args.index + 1}/{args.count}: {len(relative)} files", flush=True)
    environment = {key:value for key,value in os.environ.items() if not key.startswith(
        ("PG", "OAM_", "RSC_PG16_", "ALIBABA_CLOUD_", "OSS_", "AWS_"))}
    environment["PYTHONPATH"] = "backend"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-ra", "--tb=short", *relative],
                          cwd=CLOUD, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
