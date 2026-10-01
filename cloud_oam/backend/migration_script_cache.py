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
import sys
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
    negative_importers: set[str] = set()

    @wraps(original)
    def load(fname):
        if not isinstance(fname, str):
            return original(fname)
        path = Path(fname)
        if path.suffix != ".py":
            return original(fname)
        # Absolute regular files in the already-resolved directory need no
        # repeated walk through every ancestor. Symlinks and alternate path
        # spellings retain the original canonical-directory check.
        if not (path.parent == root and not path.is_symlink()) and path.resolve().parent != root:
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
            # Reaching the direct-file loader proves run_path found no finder
            # for this immutable source file. pkgutil otherwise reopens the
            # same .py as a possible zip archive on every predecessor load.
            # Cache only that negative lookup, never a module or its globals.
            # Existing/custom finders are untouched, and our entries are
            # removed when the invocation ends.
            if fname not in sys.path_importer_cache:
                sys.path_importer_cache[fname] = None
                negative_importers.add(fname)
        return code

    stats.enabled = True
    runpy._get_code_from_file = load
    try:
        yield stats
    finally:
        runpy._get_code_from_file = original
        for name in negative_importers:
            if name in sys.path_importer_cache and sys.path_importer_cache[name] is None:
                del sys.path_importer_cache[name]
