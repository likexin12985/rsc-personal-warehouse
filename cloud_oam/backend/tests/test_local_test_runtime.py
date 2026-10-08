"""Parallel pytest processes must never share the app's default mutable state."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys


CLOUD = Path(__file__).resolve().parents[2]
TESTS = CLOUD / "backend" / "tests"


def test_parallel_singletons_and_legacy_flow_use_owned_paths(tmp_path):
    # Run the actual conftest and core-flow imports, not a surrogate settings
    # object. Both processes write the same table key and upload filename.
    script = r'''
import json, runpy, sys, time
from pathlib import Path
runpy.run_path(sys.argv[1])
import local_test_runtime as paths
from app.database import engine
legacy = runpy.run_path(sys.argv[2])
assert legacy['DB_PATH'] == paths.DATABASE_PATH
assert legacy['UPLOAD_PATH'] == paths.UPLOAD_PATH
assert Path(engine.url.database) == paths.DATABASE_PATH
assert paths.DATABASE_PATH.parent == paths.RUN_DIRECTORY
marker, barrier = sys.argv[3], Path(sys.argv[4])
with engine.begin() as db:
    db.exec_driver_sql('CREATE TABLE isolation_probe (id INTEGER PRIMARY KEY, owner TEXT NOT NULL)')
    db.exec_driver_sql('INSERT INTO isolation_probe VALUES (1, ?)', (marker,))
paths.UPLOAD_PATH.mkdir()
(paths.UPLOAD_PATH / 'same-filename.txt').write_text(marker)
(barrier / (marker + '.ready')).write_text('ready')
deadline = time.monotonic() + 20
while len(list(barrier.glob('*.ready'))) != 2:
    assert time.monotonic() < deadline, 'parallel process did not reach barrier'
    time.sleep(.05)
with engine.connect() as db:
    assert db.exec_driver_sql('SELECT owner FROM isolation_probe').scalar_one() == marker
assert (paths.UPLOAD_PATH / 'same-filename.txt').read_text() == marker
engine.dispose()
print(json.dumps({'database': str(paths.DATABASE_PATH), 'uploads': str(paths.UPLOAD_PATH), 'owner': marker}))
'''
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(CLOUD / "backend"), str(TESTS))),
        OAM_DATABASE_URL="postgresql+psycopg://forbidden:forbidden@127.0.0.1:1/forbidden",
        OAM_UPLOAD_DIR=str(tmp_path / "must-not-use"),
    )

    def run(marker):
        result = subprocess.run(
            [sys.executable, "-c", script, str(TESTS / "conftest.py"),
             str(TESTS / "test_core_flows.py"), marker, str(tmp_path)],
            cwd=CLOUD, env=environment, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stderr[-4000:]
        return json.loads(result.stdout.splitlines()[-1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(run, ("first", "second")))
    assert first["database"] != second["database"]
    assert first["uploads"] != second["uploads"]
    assert {first["owner"], second["owner"]} == {"first", "second"}
    assert not (tmp_path / "must-not-use").exists()
    for result in (first, second):
        assert Path(result["database"]).is_relative_to(CLOUD / "artifacts" / "test-runtime")
        assert Path(result["database"]).is_file()
        assert (Path(result["uploads"]) / "same-filename.txt").read_text() == result["owner"]
