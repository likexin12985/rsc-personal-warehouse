"""The target API image must install the reviewed, fully hashed dependency set."""

from pathlib import Path
import re


BACKEND = Path(__file__).resolve().parents[1]


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_versions(lock: str) -> dict[str, str]:
    headers = list(re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\]+) \\$", lock))
    resolved = {}
    for index, match in enumerate(headers):
        name = _normalized(match.group(1))
        assert name not in resolved
        resolved[name] = match.group(2)
        end = headers[index + 1].start() if index + 1 < len(headers) else len(lock)
        block = lock[match.end():end]
        assert re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)", block), name
    return resolved


def test_linux_image_lock_covers_every_direct_requirement_and_distribution():
    direct = {}
    for line in (BACKEND / "requirements.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s]+)", line)
        assert match is not None, line
        direct[_normalized(match.group(1))] = match.group(2)

    lock = (BACKEND / "requirements-linux-amd64.lock").read_text()
    resolved = _locked_versions(lock)
    assert len(resolved) >= len(direct)
    assert all(resolved.get(name) == version for name, version in direct.items())


def test_build_tools_are_pinned_and_hashed():
    direct = dict(line.split("==", 1) for line in
                  (BACKEND / "requirements-build.in").read_text().splitlines()
                  if line and not line.startswith("#"))
    locked = _locked_versions((BACKEND / "requirements-build.lock").read_text())
    assert {_normalized(name): version for name, version in direct.items()} == locked
    assert set(locked) == {"setuptools", "wheel"}


def test_linux_image_enforces_the_hashed_lock():
    dockerfile = (BACKEND / "Dockerfile").read_text()
    assert "COPY requirements.txt requirements-linux-amd64.lock requirements-build.in requirements-build.lock ./" in dockerfile
    assert 'pip install --no-cache-dir --require-hashes --index-url "$PIP_INDEX_URL" -r requirements-build.lock' in dockerfile
    assert 'pip install --no-cache-dir --no-build-isolation --require-hashes --index-url "$PIP_INDEX_URL" -r requirements-linux-amd64.lock' in dockerfile
    assert "RUN pip uninstall -y setuptools wheel" in dockerfile
