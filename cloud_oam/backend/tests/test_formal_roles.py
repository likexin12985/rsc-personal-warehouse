from pathlib import Path

from app.authorization import FORMAL_ROLE_CODES, RETIRED_ROLE_CODES, is_formal_role


BACKEND_APP = Path(__file__).resolve().parents[1] / "app"


def test_only_three_internal_roles_and_external_approver_are_formal():
    assert FORMAL_ROLE_CODES == {
        "admin",
        "provincial_manager",
        "technician",
        "star_headquarters_approver",
    }
    assert RETIRED_ROLE_CODES == {"auditor", "warehouse_manager"}
    assert all(is_formal_role(role) for role in FORMAL_ROLE_CODES)
    assert all(not is_formal_role(role) for role in RETIRED_ROLE_CODES)
    assert not is_formal_role("unknown")


def test_active_api_authorization_does_not_reference_retired_roles():
    active_sources = [
        BACKEND_APP / "schemas.py",
        *sorted((BACKEND_APP / "routers").glob("*.py")),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in active_sources)
    assert '"auditor"' not in combined
    assert '"warehouse_manager"' not in combined
