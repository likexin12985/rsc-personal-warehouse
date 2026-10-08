"""Read a known formal request through the migrated API role, without writes."""
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.demand_models import MaterialRequest
from app.formal_access import load_formal_principal
from app.formal_services.material_request_overview import material_request_overview
from app.formal_services.material_request_query import MaterialRequestReadError


def assert_material_request_overview_gate(
    api_engine, *, request_id, manager_user_id, expected_status,
):
    # All assertions run in a PostgreSQL read-only transaction. This catches
    # accidental audit, projection or inventory mutations in the read service.
    with Session(api_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        request = db.get(MaterialRequest, request_id)
        assert request is not None and request.status == expected_status
        actor = load_formal_principal(db, manager_user_id)
        filters = dict(
            organization_id=request.requester_org_id,
            created_from=request.created_at,
            created_before=request.created_at + timedelta(microseconds=1),
        )
        report = material_request_overview(db, actor=actor, **filters)
        assert report.matched_requests == 1
        assert report.counts['request_status'][expected_status] == 1
        assert report.counts['personal_inbound_status']['posted'] == 0
        assert report.counts['shipment_status']['not_started'] == 1
        assert all(sum(states.values()) == 1 for states in report.counts.values())
        if expected_status == 'approved':
            assert all(report.counts[f'approval_level_{level}']['approved'] == 1 for level in (1, 2, 3))
        excluded = material_request_overview(db, actor=actor,
            organization_id=request.requester_org_id, created_before=request.created_at)
        assert excluded.matched_requests == 0
        try:
            material_request_overview(db, actor=load_formal_principal(db, request.requester_user_id))
        except MaterialRequestReadError as error:
            assert error.status_code == 403
        else:
            raise AssertionError('personal-only identity admitted to regional report')
        db.rollback()
    return {'passed': True, 'requestStatus': expected_status, 'readOnlyTransaction': True}
