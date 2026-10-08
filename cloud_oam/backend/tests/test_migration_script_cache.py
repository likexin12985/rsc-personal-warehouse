from pathlib import Path
import runpy

import pytest

from migration_script_cache import cache_migration_compilation


def test_bytecode_reuse_keeps_fresh_execution_globals_and_runpy_metadata(tmp_path):
    script = tmp_path / "revision.py"
    script.write_text(
        "values = [seed]\n"
        "def current(): return values\n"
        "metadata = (__name__, __file__, __package__, __spec__)\n"
    )
    with cache_migration_compilation(tmp_path) as stats:
        first = runpy.run_path(str(script), init_globals={"seed": 1}, run_name="first")
        first["current"].__globals__["values"].append("mutated by later revision")
        second = runpy.run_path(str(script), init_globals={"seed": 2}, run_name="second")
    assert first["current"]() == [1, "mutated by later revision"]
    assert second["current"]() == [2]
    assert first["current"].__globals__ is not second["current"].__globals__
    assert first["metadata"] == ("first", str(script), "", None)
    assert second["metadata"] == ("second", str(script), "", None)
    assert stats.enabled and stats.compiled == 1 and stats.reused == 1


def test_predecessor_side_effects_still_run_on_every_load(tmp_path):
    counter = tmp_path / "executions.txt"
    script = tmp_path / "revision.py"
    script.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as output: output.write('executed\\n')\n"
    )
    with cache_migration_compilation(tmp_path):
        runpy.run_path(str(script))
        runpy.run_path(str(script))
    assert counter.read_text().splitlines() == ["executed", "executed"]


def test_changed_revision_is_refused_and_original_loader_is_restored(tmp_path):
    script = tmp_path / "revision.py"
    script.write_text("value = 1\n")
    original = runpy._get_code_from_file
    with pytest.raises(RuntimeError, match="source changed"):
        with cache_migration_compilation(tmp_path):
            assert runpy.run_path(str(script))["value"] == 1
            script.write_text("value = 2\n")
            runpy.run_path(str(script))
    assert runpy._get_code_from_file is original
    assert runpy.run_path(str(script))["value"] == 2


def test_other_directories_keep_normal_reload_semantics(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    unrelated = tmp_path / "revision.py"
    unrelated.write_text("value = 1\n")
    with cache_migration_compilation(versions) as stats:
        assert runpy.run_path(str(unrelated))["value"] == 1
        unrelated.write_text("value = 2\n")
        assert runpy.run_path(str(unrelated))["value"] == 2
    assert stats.compiled == stats.reused == 0


def test_nested_caches_restore_each_loader_and_never_reuse_globals(tmp_path):
    script = tmp_path / "revision.py"
    script.write_text("value = []\n")
    original = runpy._get_code_from_file
    with cache_migration_compilation(tmp_path):
        outer = runpy._get_code_from_file
        one = runpy.run_path(str(script))
        with cache_migration_compilation(tmp_path):
            two = runpy.run_path(str(script))
        assert runpy._get_code_from_file is outer
        three = runpy.run_path(str(script))
    assert runpy._get_code_from_file is original
    assert len({id(item["value"]) for item in (one, two, three)}) == 3


def test_unsupported_interpreter_hook_falls_back_without_patch(tmp_path, monkeypatch):
    def alternative_loader(path, other):
        raise AssertionError("not called")
    monkeypatch.setattr(runpy, "_get_code_from_file", alternative_loader)
    with cache_migration_compilation(tmp_path) as stats:
        assert runpy._get_code_from_file is alternative_loader
        assert not stats.enabled


def test_direct_file_negative_finder_is_scoped_and_existing_entry_is_preserved(tmp_path):
    import sys
    script = tmp_path / "revision.py"
    script.write_text("value = []\n")
    key = str(script)
    sys.path_importer_cache.pop(key, None)
    with cache_migration_compilation(tmp_path):
        first = runpy.run_path(key)
        assert key in sys.path_importer_cache and sys.path_importer_cache[key] is None
        second = runpy.run_path(key)
        assert first["value"] is not second["value"]
    assert key not in sys.path_importer_cache
    sys.path_importer_cache[key] = None
    try:
        with cache_migration_compilation(tmp_path):
            runpy.run_path(key)
        assert key in sys.path_importer_cache and sys.path_importer_cache[key] is None
    finally:
        sys.path_importer_cache.pop(key, None)


def test_symlink_outside_versions_is_never_cached(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n")
    link = versions / "revision.py"
    link.symlink_to(outside)
    with cache_migration_compilation(versions) as stats:
        assert runpy.run_path(str(link))["value"] == 1
        outside.write_text("value = 2\n")
        assert runpy.run_path(str(link))["value"] == 2
    assert stats.compiled == stats.reused == 0


def test_large_revision_is_executed_but_not_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("OAM_MIGRATION_CACHE_MAX_FILE_BYTES", str(128 * 1024))
    script = tmp_path / "large_revision.py"
    script.write_text("value = 1\n" + ("padding = 'x'\n" * 20_000))
    with cache_migration_compilation(tmp_path) as stats:
        assert runpy.run_path(str(script))["value"] == 1
        assert runpy.run_path(str(script))["value"] == 1
    assert stats.compiled == 2
    assert stats.reused == 0
    assert stats.skipped == 2


def test_large_predecessor_bytecode_is_reused_with_fresh_execution(tmp_path, monkeypatch):
    monkeypatch.delenv("OAM_MIGRATION_CACHE_EXECUTION", raising=False)
    script = tmp_path / "large_predecessor.py"
    counter = tmp_path / "executions.txt"
    script.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as output: output.write('executed\\n')\n"
        "values = [seed]\n"
        "def current(): return values\n"
        "sql = " + repr("x" * 850_000) + "\n"
    )
    with cache_migration_compilation(tmp_path) as stats:
        first = runpy.run_path(str(script), init_globals={"seed": 1})
        first["current"]().append("changed")
        second = runpy.run_path(str(script), init_globals={"seed": 2})
    assert first["current"]() == [1, "changed"]
    assert second["current"]() == [2]
    assert first["current"].__globals__ is not second["current"].__globals__
    assert counter.read_text().splitlines() == ["executed", "executed"]
    assert stats.compiled == 1 and stats.reused == 1 and stats.skipped == 0
    assert not stats.execution_enabled


def test_opt_in_execution_cache_reuses_import_pure_predecessors(monkeypatch, tmp_path):
    monkeypatch.setenv("OAM_MIGRATION_CACHE_EXECUTION", "1")
    counter = tmp_path / "executions.txt"
    versions = tmp_path / "versions"
    versions.mkdir()
    helper = versions / "20260101_0001_helper.py"
    helper.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as output: output.write('executed\\n')\n"
        "values = []\n"
        "def current(): return values\n"
        "revision = '20260101_0001'\n"
        "down_revision = None\n"
    )
    script = versions / "20260101_0002_revision.py"
    script.write_text(
        "from pathlib import Path\n"
        "import runpy\n"
        "helper = Path(__file__).with_name('20260101_0001_helper.py')\n"
        "first = runpy.run_path(str(helper))\n"
        "second = runpy.run_path(str(helper))\n"
        "assert first['current'].__globals__ is not second['current'].__globals__\n"
        "revision = '20260101_0002'\n"
        "down_revision = '20260101_0001'\n"
    )
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(tmp_path))
    with cache_migration_compilation(versions) as stats:
        assert ScriptDirectory.from_config(config).get_heads() == ["20260101_0002"]
    # Alembic imports the helper once as a revision module; the cache removes
    # only repeated runpy predecessor executions inside later revisions.
    assert counter.read_text().splitlines() == ["executed", "executed"]
    assert stats.execution_enabled
    assert stats.execution_cached == 1
    assert stats.execution_reused >= 1


def test_direct_execution_cache_is_separate_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("OAM_MIGRATION_CACHE_EXECUTION", "1")
    monkeypatch.setenv("OAM_MIGRATION_CACHE_EXECUTION_DIRECT", "1")
    counter = tmp_path / "executions.txt"
    script = tmp_path / "revision.py"
    script.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as output: output.write('executed\\n')\n"
        "value = []\n"
    )
    with cache_migration_compilation(tmp_path) as stats:
        first = runpy.run_path(str(script))
        second = runpy.run_path(str(script))
    assert counter.read_text().splitlines() == ["executed"]
    assert first["value"] is not second["value"]
    assert stats.execution_enabled
    assert stats.execution_cached == 1
    assert stats.execution_reused == 1
