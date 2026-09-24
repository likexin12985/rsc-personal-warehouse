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
