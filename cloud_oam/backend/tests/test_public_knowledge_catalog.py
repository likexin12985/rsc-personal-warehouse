from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("import_public_knowledge", ROOT / "scripts/import_public_knowledge.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample():
    return {"code": "TEST001", "name": "测试备件", "category": "测试分类", "model": "TEST", "note": "仅测试夹具",
            "sourceSheet": "测试页", "sourceRow": 2}


def build(rows, **overrides):
    return module.build_catalog(rows, **dict({"source_url": module.SOURCE_URL,
        "verified_at": "2026-01-01T00:00:00+08:00", "source_sha256": "a" * 64}, **overrides))


def test_catalog_retains_exact_provenance_and_does_not_infer_compatibility():
    row = sample()
    row["model"] = ""
    catalog = build([row])
    assert catalog["items"] == [row]
    assert catalog["sourceSha256"] == "a" * 64
    assert catalog["status"] == "ready"


@pytest.mark.parametrize("changes", [
    {"token": "must-not-be-published"}, {"name": ""}, {"category": ""}, {"code": "="},
    {"sourceRow": 0}, {"sourceRow": True}, {"sourceSheet": ""}, {"note": "x" * 3001}, {"model": []},
])
def test_import_rejects_unreviewed_fields_and_untraceable_rows(changes):
    row = dict(sample(), **changes)
    with pytest.raises(ValueError):
        build([row])


@pytest.mark.parametrize("overrides", [
    {"source_url": "https://example.com"}, {"verified_at": "2026-01-01"},
    {"verified_at": "2999-01-01T00:00:00Z"}, {"source_sha256": ""},
])
def test_import_requires_selected_source_and_dated_export(overrides):
    with pytest.raises(ValueError):
        build([sample()], **overrides)


def test_duplicate_source_cannot_silently_overwrite_or_merge_material_facts():
    with pytest.raises(ValueError):
        build([sample(), deepcopy(sample())])
    row = dict(sample(), sourceSheet="另一测试页")
    assert len(build([sample(), row])["items"]) == 2
    with pytest.raises(ValueError):
        build([])


def test_published_web_and_mini_catalogs_are_identical_and_valid():
    web, mini = (json.loads(path.read_text()) for path in module.OUTPUTS)
    assert web == mini
    if web["status"] == "ready":
        assert web == build(web["items"], source_url=web["sourceUrl"], verified_at=web["verifiedAt"], source_sha256=web["sourceSha256"])
    else:
        assert web == {"schemaVersion": 1, "status": "pending", "sourceUrl": module.SOURCE_URL,
                       "verifiedAt": None, "sourceSha256": None, "items": []}
