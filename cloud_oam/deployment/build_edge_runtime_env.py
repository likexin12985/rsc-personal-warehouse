#!/usr/bin/env python3
"""Build the receiver-only runtime env without printing any secret values."""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path


SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)


def contains_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def required(values: dict[str, str], key: str, source: Path) -> str:
    value = values.get(key, "")
    if not value:
        raise RuntimeError(f"{source} 缺少 {key}")
    return value


def build_runtime_values(edge_env: dict[str, str], edge_path: Path) -> dict[str, str]:
    database_url = required(edge_env, "RSC_EDGE_DATABASE_URL", edge_path)
    edge_secret = required(edge_env, "RSC_EDGE_SYNC_SECRET", edge_path)
    allowed_sources = required(edge_env, "RSC_EDGE_ALLOWED_SOURCES", edge_path)
    if not database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("RSC_EDGE_DATABASE_URL必须使用postgresql+psycopg专用暂存账号")
    if contains_placeholder(database_url):
        raise RuntimeError("RSC_EDGE_DATABASE_URL仍包含示例占位值")
    if len(edge_secret) < 32 or contains_placeholder(edge_secret):
        raise RuntimeError("边缘同步密钥长度不足32位")
    sources = [item.strip() for item in allowed_sources.split(",") if item.strip()]
    if not sources or any(not SOURCE_PATTERN.fullmatch(item) for item in sources):
        raise RuntimeError("RSC_EDGE_ALLOWED_SOURCES必须是有效且非空的来源白名单")

    values = {
        "OAM_DATABASE_URL": database_url,
        "OAM_EDGE_SYNC_SECRET": edge_secret,
        "OAM_EDGE_SYNC_ALLOWED_SOURCES": ",".join(dict.fromkeys(sources)),
    }
    if any("\r" in value or "\n" in value for value in values.values()):
        raise RuntimeError("运行配置值包含非法换行")
    return values


def main() -> int:
    if len(sys.argv) != 3:
        raise RuntimeError("用法: build_edge_runtime_env.py EDGE_ENV OUTPUT_ENV")
    edge_path, output_path = map(Path, sys.argv[1:])
    edge_env = read_env(edge_path)
    values = build_runtime_values(edge_env, edge_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output_path)
        os.chmod(output_path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"runtime_env_ready keys={len(values)} mode=600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
