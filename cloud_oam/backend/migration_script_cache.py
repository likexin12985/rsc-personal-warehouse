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
import os
from pathlib import Path
import runpy
import sys
from types import CodeType, FunctionType


@dataclass
class MigrationCompilationStats:
    enabled: bool = False
    compiled: int = 0
    reused: int = 0
    skipped: int = 0
    execution_enabled: bool = False
    execution_cached: int = 0
    execution_reused: int = 0
    execution_skipped: int = 0


# Bound bytecode retention independently of the opt-in namespace cache below.
# The 165-revision tree has 5.6 MB of source and approximately 8.9 MB of code
# objects on CPython 3.12. Its largest revision is 834 KB; a 128 KB per-file
# limit forced that predecessor to be recompiled throughout the entire graph.
# These budgets admit the current tree while still bounding future growth.
# Every runpy invocation continues to execute fresh globals and side effects.
# Entries above any limit still execute normally without being retained.
MAX_CACHED_SOURCE_BYTES = 8 * 1024 * 1024
MAX_CACHED_FILE_BYTES = 1 * 1024 * 1024
MAX_CACHED_ENTRIES = 192
MAX_EXECUTION_SOURCE_BYTES = 8 * 1024 * 1024
MAX_EXECUTION_FILE_BYTES = 256 * 1024
MAX_EXECUTION_ENTRIES = 96


def _positive_limit(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _clone_namespace(namespace: dict[str, object]) -> dict[str, object]:
    """Return an isolated, cheap copy of a cached runpy namespace.

    Migration helpers deliberately use ``runpy`` instead of imports so their
    globals are fresh for each predecessor load.  The execution cache keeps
    immutable source-derived values once, then recreates function objects with
    a new globals dictionary.  Top-level containers are copied shallowly: the
    reviewed helpers mutate their own bindings, while their SQL strings and
    immutable nested tuples remain shared.
    """

    clone = dict(namespace)
    functions = {
        name: value
        for name, value in namespace.items()
        if isinstance(value, FunctionType)
    }
    for name, function in functions.items():
        isolated = FunctionType(
            function.__code__,
            clone,
            function.__name__,
            function.__defaults__,
            function.__closure__,
        )
        isolated.__kwdefaults__ = function.__kwdefaults__
        isolated.__annotations__ = function.__annotations__
        isolated.__dict__.update(function.__dict__)
        clone[name] = isolated
    for name, value in namespace.items():
        if name in functions:
            continue
        if isinstance(value, dict):
            clone[name] = dict(value)
        elif isinstance(value, list):
            clone[name] = list(value)
        elif isinstance(value, set):
            clone[name] = set(value)
    return clone


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
    cache_source_bytes = 0
    cache_source_limit = _positive_limit(
        "OAM_MIGRATION_CACHE_MAX_SOURCE_BYTES", MAX_CACHED_SOURCE_BYTES
    )
    cache_file_limit = _positive_limit(
        "OAM_MIGRATION_CACHE_MAX_FILE_BYTES", MAX_CACHED_FILE_BYTES
    )
    cache_entry_limit = _positive_limit(
        "OAM_MIGRATION_CACHE_MAX_ENTRIES", MAX_CACHED_ENTRIES
    )
    execution_enabled = os.getenv("OAM_MIGRATION_CACHE_EXECUTION") == "1"
    # Direct predecessor runpy calls are more invasive than Alembic's module
    # discovery path: they may intentionally repeat side effects. Keep this
    # second layer explicitly opt-in for controlled fixture diagnostics only.
    # Production migration containers enable the bounded discovery cache above;
    # they must not silently change direct migration semantics.
    direct_execution_enabled = (
        execution_enabled and os.getenv("OAM_MIGRATION_CACHE_EXECUTION_DIRECT") == "1"
    )
    execution_cache: dict[str, tuple[str, int, int, dict[str, object]]] = {}
    execution_source_bytes = 0
    execution_source_limit = _positive_limit(
        "OAM_MIGRATION_EXECUTION_CACHE_MAX_SOURCE_BYTES",
        MAX_EXECUTION_SOURCE_BYTES,
    )
    execution_file_limit = _positive_limit(
        "OAM_MIGRATION_EXECUTION_CACHE_MAX_FILE_BYTES",
        MAX_EXECUTION_FILE_BYTES,
    )
    execution_entry_limit = _positive_limit(
        "OAM_MIGRATION_EXECUTION_CACHE_MAX_ENTRIES", MAX_EXECUTION_ENTRIES
    )
    revision_loading = False
    original_run_path = runpy.run_path
    original_from_path = None
    original_from_path_descriptor = None
    negative_importers: set[str] = set()

    def eligible_path(path_name: object):
        if not isinstance(path_name, str):
            return None
        path = Path(path_name)
        if path.suffix != ".py":
            return None
        if not (path.parent == root and not path.is_symlink()) and path.resolve().parent != root:
            return None
        return path_name, path, path.stat()

    def eligible_source(path_name: object) -> tuple[str, Path, bytes, str] | None:
        path_info = eligible_path(path_name)
        if path_info is None:
            return None
        _, path, _ = path_info
        source = path.read_bytes()
        digest = sha256(source).hexdigest()
        return path_name, path, source, digest

    @wraps(original)
    def load(fname):
        nonlocal cache_source_bytes
        source_info = eligible_source(fname)
        if source_info is None:
            return original(fname)
        _, path, source, digest = source_info
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
            stats.compiled += 1
            if (
                len(source) <= cache_file_limit
                and len(cache) < cache_entry_limit
                and cache_source_bytes + len(source) <= cache_source_limit
            ):
                cache[fname] = (digest, code)
                cache_source_bytes += len(source)
            else:
                stats.skipped += 1
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

    def cached_run_path(path_name, *, init_globals=None, run_name=None):
        nonlocal execution_source_bytes
        path_info = eligible_path(path_name)
        if (
            not execution_enabled
            or (not revision_loading and not direct_execution_enabled)
            or init_globals is not None
            or run_name is not None
            or path_info is None
        ):
            return original_run_path(
                path_name, init_globals=init_globals, run_name=run_name
            )
        fname, path, stat = path_info
        cached = execution_cache.get(fname)
        if cached is not None:
            if cached[1] == stat.st_size and cached[2] == stat.st_mtime_ns:
                stats.execution_reused += 1
                return _clone_namespace(cached[3])
            source = path.read_bytes()
            digest = sha256(source).hexdigest()
            if cached[0] != digest:
                raise RuntimeError("migration source changed during one invocation")
            stats.execution_reused += 1
            return _clone_namespace(cached[3])
        source_info = eligible_source(path_name)
        assert source_info is not None
        _, path, source, digest = source_info
        namespace = original_run_path(
            path_name, init_globals=init_globals, run_name=run_name
        )
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("migration source changed during execution")
        if (
            len(source) <= execution_file_limit
            and len(execution_cache) < execution_entry_limit
            and execution_source_bytes + len(source) <= execution_source_limit
        ):
            execution_cache[fname] = (
                digest,
                stat.st_size,
                stat.st_mtime_ns,
                namespace,
            )
            execution_source_bytes += len(source)
            stats.execution_cached += 1
            return _clone_namespace(namespace)
        stats.execution_skipped += 1
        return namespace

    stats.enabled = True
    stats.execution_enabled = execution_enabled
    runpy._get_code_from_file = load
    if execution_enabled:
        # Alembic discovers revision files through this private classmethod.
        # Limit execution-result reuse to that discovery phase; upgrade()
        # bodies still receive the historical fresh runpy semantics.
        try:
            from alembic.script.base import Script

            original_from_path_descriptor = Script.__dict__["_from_path"]
            original_from_path = Script._from_path

            def from_path(cls, scriptdir, path):
                nonlocal revision_loading
                previous = revision_loading
                revision_loading = True
                try:
                    return original_from_path(scriptdir, path)
                finally:
                    revision_loading = previous

            Script._from_path = classmethod(from_path)
            runpy.run_path = cached_run_path
        except (ImportError, KeyError, AttributeError):
            stats.execution_enabled = False
    try:
        yield stats
    finally:
        runpy._get_code_from_file = original
        if original_from_path_descriptor is not None:
            from alembic.script.base import Script

            Script._from_path = original_from_path_descriptor
        if execution_enabled:
            runpy.run_path = original_run_path
        for name in negative_importers:
            if name in sys.path_importer_cache and sys.path_importer_cache[name] is None:
                del sys.path_importer_cache[name]
