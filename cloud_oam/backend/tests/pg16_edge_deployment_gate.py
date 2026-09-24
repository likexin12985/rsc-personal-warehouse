"""Real psql checks of the edge deployment verifier in isolated PG16 gates.

Every deliberate catalog drift lives inside the psql child's transaction.
ON_ERROR_STOP closes that connection on rejection, rolling the drift back.
Callers own the newly created local or CI database; this is not a deploy tool.
"""

from pathlib import Path
import subprocess


CLOUD = Path(__file__).resolve().parents[2]
RUNTIME_VOLATILITY = {
    "public.rsc_oam_rls_check_0044(text,text,jsonb)": "s",
    "public.rsc_oam_runtime_binding_ready_0044()": "s",
    "public.rsc_oam_material_capture_visible_0116(text)": "v",
    "public.rsc_oam_material_capture_binding_0116(text,text,text)": "v",
    "public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)": "s",
}


def assert_edge_deployment_verifier(*, command, environment, evidence_directory=None):
    source = (CLOUD / "deployment/verify_oam_edge_staging.sql").read_text()
    passed = []

    def verify(label, statements="", *, reject=False):
        result = subprocess.run(
            command, input="BEGIN;\n" + statements + "\n" + source + "\nROLLBACK;\n",
            cwd=CLOUD, env=environment, capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        if evidence_directory is not None:
            (evidence_directory / ("edge-verifier-" + label + ".log")).write_text(output)
        if reject:
            assert result.returncode == 3, (label, result.returncode, output)
            assert "edge/projector deployment ACL verification failed:" in output, (label, output)
            assert "functions_sequences" in output, (label, output)
        else:
            assert result.returncode == 0, (label, result.returncode, output)
            assert "edge/projector deployment ACL verified" in output, (label, output)
        passed.append(label)

    verify("baseline")
    for ordinal, (signature, expected) in enumerate(RUNTIME_VOLATILITY.items()):
        for actual, keyword in (("s", "STABLE"), ("v", "VOLATILE"), ("i", "IMMUTABLE")):
            if actual != expected:
                verify(f"volatility-{ordinal}-{actual}",
                       f"ALTER FUNCTION {signature} {keyword};", reject=True)

    visible = "public.rsc_oam_material_capture_visible_0116(text)"
    binding = "public.rsc_oam_material_capture_binding_0116(text,text,text)"
    receipt = "public.rsc_oam_receipt_rls_check_0082(text,text,text,jsonb)"
    for ordinal, (signature, grantee) in enumerate((
        (visible, "star_oam_projector"), (binding, "star_oam_projector"),
        (receipt, "edge_inbox"), (visible, "PUBLIC"),
        (visible, "star_oam_api"), (visible, "star_oam_backup"),
    )):
        verify(f"unexpected-grant-{ordinal}",
               f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee};", reject=True)
    verify("security-invoker", f"ALTER FUNCTION {visible} SECURITY INVOKER;", reject=True)
    verify("unsafe-search-path", f"ALTER FUNCTION {visible} SET search_path=public;", reject=True)
    for role in ("edge_inbox", "star_oam_projector"):
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            verify(f"sequence-{role}-{privilege.lower()}",
                   "CREATE SEQUENCE public.rsc_pg16_edge_verifier_sequence;\n"
                   f"GRANT {privilege} ON SEQUENCE public.rsc_pg16_edge_verifier_sequence TO {role};",
                   reject=True)
    verify("restored")
    return {"status": "passed", "cases": passed,
            "expectedVolatility": RUNTIME_VOLATILITY,
            "driftRolledBack": True}
