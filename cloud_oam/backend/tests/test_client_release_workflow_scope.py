"""Keep the private trial branch separate from the full public-catalog gate."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "client-release-gate.yml"
PILOT_VERIFIER = ROOT / "cloud_oam" / "frontend" / "build" / "verify-pilot-release.mjs"
REQUEST_PAGE = ROOT / "cloud_oam" / "frontend" / "src" / "pages" / "FormalMaterialRequests.tsx"


def _steps() -> list[dict[str, object]]:
    document = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["client-release-gate"]["steps"]


def test_trial_branch_runs_artifact_boundary_without_catalog_ready_requirement():
    steps = _steps()
    pilot = next(step for step in steps if step["name"] == "Verify pilot public and private entry artifacts")
    assert pilot["run"] == "node cloud_oam/scripts/verify_public_entry.mjs"
    assert "--release" not in pilot["run"]

    strict = next(step for step in steps if step["name"] == "Verify complete public catalog on release refs")
    condition = str(strict["if"])
    assert strict["run"] == "node cloud_oam/scripts/verify_public_entry.mjs --release"
    assert "github.event_name == 'pull_request'" in condition
    assert "refs/heads/main" in condition
    assert "refs/heads/codex/production-readiness-gates" in condition


def test_notification_worker_branch_remains_a_client_gate_trigger():
    document = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    assert "codex/notification-delivery-worker" in document["on"]["push"]["branches"]


def test_client_gate_keeps_current_scope_regressions_in_the_release_job():
    steps = _steps()
    by_name = {str(step["name"]): step for step in steps}
    assert by_name["Run Web tests"]["run"] == "pnpm test"
    assert by_name["Run WeChat mini-program and gate contract tests"]["run"] == "node --test tests/*.test.js"
    assert by_name["Build private warehouse assets for /xx without deployment"]["run"] == "pnpm exec vite build --mode warehouse"


def test_client_gate_verifies_frozen_trial_mvp_scope_after_private_build():
    steps = _steps()
    by_name = {str(step["name"]): step for step in steps}
    step = by_name["Verify frozen trial MVP scope and /xx artifact"]
    assert step["working-directory"] == "cloud_oam/frontend"
    assert step["run"] == "pnpm run verify:pilot-release"
    names = [str(item["name"]) for item in steps]
    assert names.index("Build private warehouse assets for /xx without deployment") < names.index(step["name"])


def test_pilot_artifact_gate_keeps_every_operator_panel_behind_backend_capability():
    source = REQUEST_PAGE.read_text(encoding="utf-8")
    verifier = PILOT_VERIFIER.read_text(encoding="utf-8")
    assert "access.can_read_allocation_options === true" in source
    for panel in (
        "FormalMaterialRequestSupplyPanel",
        "FormalMaterialRequestReservationPanel",
        "FormalMaterialRequestOutboundPanel",
        "FormalMaterialRequestShipmentPanel",
        "FormalMaterialRequestReceiptPanel",
        "FormalMaterialRequestInboundPanel",
        "FormalMaterialRequestPickPanel",
        "FormalMaterialRequestFulfillmentPreparationPanel",
    ):
        assert panel in source
        assert panel in verifier
    assert "PILOT_PANEL_NOT_CAPABILITY_GATED" in verifier
