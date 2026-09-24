"""Reuse immutable bytecode while runpy still executes fresh migration globals.

Historical revisions load predecessor definitions with runpy. Caching their
returned dictionaries would share mutable function globals and change the
meaning of migrations. Only the compiler result may be reused; runpy keeps
its normal module, argv, namespace and execution lifecycle on every call.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from hashlib import sha256
import inspect
from pathlib import Path
import runpy
from types import CodeType


@dataclass
class MigrationCompilationStats:
    enabled: bool = False
    compiled: int = 0
    reused: int = 0


@contextmanager
def cache_migration_compilation(versions_directory: Path):
    """Optimize only reviewed .py revisions for one migration invocation.

    The narrow CPython hook is checked at runtime. Unsupported interpreters
    retain normal runpy behavior. The cache is process-local, never stores
    execution results, detects changed source bytes, and is always removed.
    """

    root = versions_directory.resolve(strict=True)
    stats = MigrationCompilationStats()
    original = getattr(runpy, "_get_code_from_file", None)
    try:
        supported = callable(original) and tuple(inspect.signature(original).parameters) == ("fname",)
    except (TypeError, ValueError):
        supported = False
    if not supported:
        yield stats
        return
    cache: dict[str, tuple[str, CodeType]] = {}

    @wraps(original)
    def load(fname):
        if not isinstance(fname, str):
            return original(fname)
        path = Path(fname)
        if path.suffix != ".py" or path.resolve().parent != root:
            return original(fname)
        digest = sha256(path.read_bytes()).hexdigest()
        cached = cache.get(fname)
        if cached is not None:
            if cached[0] != digest:
                raise RuntimeError("migration source changed during one invocation")
            stats.reused += 1
            return cached[1]
        code = original(fname)
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("migration source changed during compilation")
        # Do not assume a different interpreter's private return contract.
        if isinstance(code, CodeType):
            cache[fname] = (digest, code)
            stats.compiled += 1
        return code

    stats.enabled = True
    runpy._get_code_from_file = load
    try:
        yield stats
    finally:
        runpy._get_code_from_file = original
