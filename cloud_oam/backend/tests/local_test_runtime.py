"""Process-owned scratch paths for app singletons used by local test runners.

Never use an inherited database/upload setting or the repository's legacy
.test_oam.db. Keep failed-run files under ignored artifacts for inspection.
"""
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = ROOT / "artifacts" / "test-runtime"
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
RUN_DIRECTORY = Path(tempfile.mkdtemp(prefix="run-", dir=ARTIFACT_ROOT))
DATABASE_PATH = RUN_DIRECTORY / "application.db"
UPLOAD_PATH = RUN_DIRECTORY / "uploads"
