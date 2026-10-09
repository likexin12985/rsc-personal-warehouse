"""Owned synthetic directories with the same safe ancestry required in runtime.

Linux /tmp is world-writable even when pytest's leaf is private. Never chmod
shared ancestors or mock their metadata to make a positive test pass. Only a
unique private tree below the verified current user's home is created here.
Cleanup runs after function-scoped monkeypatches have all been restored.
"""
import os
from pathlib import Path
import stat
import tempfile

import pytest


@pytest.fixture(scope='session')
def projection_test_base():
    home = Path.home().resolve(strict=True)
    for path in (*reversed(home.parents), home):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
                and not info.st_mode & 0o022):
            raise RuntimeError('projection_test_safe_ancestor_unavailable')
    with tempfile.TemporaryDirectory(prefix='.rsc-projection-tests-', dir=home) as owned:
        yield Path(owned)


@pytest.fixture
def safe_projection_root(projection_test_base):
    # Retain each tiny synthetic subtree until session teardown, avoiding any
    # interaction between cleanup and a test's deliberate os.* monkeypatch.
    return Path(tempfile.mkdtemp(prefix='case-', dir=projection_test_base))
