"""Run the real safety gate in isolated Git repositories, without credentials."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_repository_safety.sh"
BASELINE = "docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md"
RUNTIME = "cloud_oam/deployment/openbao-pilot/runtime/"
# Independently enumerate the approved contract; do not derive it from the gate.
PUBLIC_NAMES = (
    "README.md",
    "runtime_application_overlay.py",
    "runtime_bootstrap.py",
    "runtime_bootstrap_README.md",
    "runtime_bootstrap_plan.md",
    "runtime_bootstrap_remote.py",
    "runtime_bootstrap_writer.py",
    "runtime_bundle.py",
    "runtime_configure.py",
    "runtime_configure_README.md",
    "runtime_configure_remote.py",
    "runtime_init.py",
    "runtime_init_remote.py",
    "runtime_install.py",
    "runtime_preflight.py",
    "runtime_transit.py",
    "runtime_transit_README.md",
    "runtime_transit_contract.py",
    "runtime_transit_remote.py",
    "runtime_unseal.py",
    "runtime_unseal_remote.py",
    "test_runtime_bootstrap.py",
    "test_runtime_bootstrap_writer.py",
    "test_runtime_bundle.py",
    "test_runtime_bytecode_boundary.py",
    "test_runtime_caddy_copy.py",
    "test_runtime_configure.py",
    "test_runtime_init.py",
    "test_runtime_native_protocol.py",
    "test_runtime_transit.py",
    "test_runtime_unseal.py",
)


class Repository:
    def __init__(self, root):
        self.root = root
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        self.git("init", "--quiet")
        for name in ("README.md", "AGENTS.md", BASELINE, "cloud_oam/README.md", "cloud_oam/.gitignore"):
            self.write(name, "Synthetic public fixture.\n")
        self.write(".gitignore", "/work/\n/output/\n/outputs/\n/scripts/\n/tmp/\n" + RUNTIME + "\n")
        target = self.root / "cloud_oam/scripts/verify_repository_safety.sh"
        target.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, target)
        self.git("add", "--all")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, env=self.env,
                              check=True, text=True, capture_output=True, timeout=20)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def visible(self, relative, content="Synthetic public source.\n"):
        path = self.write(relative, content)
        self.git("add", "--force", "--", relative)
        return path

    def scan(self):
        return subprocess.run(["bash", "scripts/verify_repository_safety.sh"],
                              cwd=self.root / "cloud_oam", env=self.env,
                              text=True, capture_output=True, timeout=30)


@pytest.fixture
def repository(tmp_path):
    return Repository(tmp_path)


def test_all_31_exact_public_sources_pass_the_real_gate(repository):
    assert len(PUBLIC_NAMES) == len(set(PUBLIC_NAMES)) == 31
    for name in PUBLIC_NAMES:
        repository.visible(RUNTIME + name)
    result = repository.scan()
    assert result.returncode == 0, result.stderr
    assert "repository-safety: PASS" in result.stdout


@pytest.mark.parametrize("relative", [
    RUNTIME + "new_source.py",
    RUNTIME + "runtime_init.py.extra",
    RUNTIME + "README.MD",
    RUNTIME + "nested/runtime_init.py",
    RUNTIME + "runtime_init.py/state.json",
    RUNTIME + "session.json",
    "cloud_oam/deployment/other/runtime/runtime_init.py",
    "cloud_oam/runtime/runtime_init.py",
])
def test_unreviewed_runtime_paths_remain_rejected(repository, relative):
    repository.visible(relative)
    result = repository.scan()
    assert result.returncode == 1
    assert "forbidden runtime, credential, or business-data artifact is Git-visible: " + relative in result.stderr


def test_public_path_still_rejects_secret_content_without_printing_it(repository):
    synthetic = "L" + "TAI" + "A" * 20
    relative = RUNTIME + "runtime_init.py"
    repository.visible(relative, synthetic + "\n")
    result = repository.scan()
    assert result.returncode == 1
    assert "high-confidence credential pattern detected (content withheld): " + relative in result.stderr
    assert synthetic not in result.stdout + result.stderr


def test_public_path_still_rejects_personal_home_without_printing_it(repository):
    synthetic = "/" + "Users/" + "private-test-user/"
    relative = RUNTIME + "README.md"
    repository.visible(relative, synthetic + "\n")
    result = repository.scan()
    assert result.returncode == 1
    assert "personal macOS home path detected (content withheld): " + relative in result.stderr
    assert synthetic not in result.stdout + result.stderr


def test_public_path_still_rejects_symlink(repository):
    relative = RUNTIME + "runtime_init.py"
    path = repository.root / relative
    path.parent.mkdir(parents=True)
    path.symlink_to(repository.root / "README.md")
    repository.git("add", "--force", "--", relative)
    result = repository.scan()
    assert result.returncode == 1
    assert "symbolic links are not permitted in the cloud repository: " + relative in result.stderr


def test_public_path_still_rejects_oversized_file(repository):
    relative = RUNTIME + "runtime_init.py"
    repository.visible(relative, b"\0" * (10 * 1024 * 1024 + 1))
    result = repository.scan()
    assert result.returncode == 1
    assert "file exceeds the 10 MiB repository limit: " + relative in result.stderr
