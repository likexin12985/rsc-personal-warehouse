"""Execute recovery against real 0109 tables, seeded roles and audit guards."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.foundation_models import AuditEvent
from notification_target_recovery_gate import assert_recovery_on_migrated_database
from test_notification_person_targets import migrated  # noqa: F401


def test_recovery_uses_migrated_permissions_binding_and_immutable_audit(migrated):
    engine,_,_,_=migrated
    proof=assert_recovery_on_migrated_database(engine,engine)
    with Session(engine) as db:
        original=db.get(AuditEvent,proof.result.audit_id)
        original.after_jsonb=dict(original.after_jsonb,outcome="delivered")
        with pytest.raises(IntegrityError):db.commit()
        db.rollback()
        assert db.get(AuditEvent,proof.result.audit_id).after_jsonb["outcome"]=="bound"
