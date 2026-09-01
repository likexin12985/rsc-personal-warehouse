from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_services.authentication_audit import (
    AuthenticationEvidenceError,
    add_authentication_state_transition,
    append_authentication_event,
)
from app.foundation_models import AuditChainHead, AuditEvent, StateTransitionEvent


NOW = datetime(2026, 8, 30, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AuditChainHead(
                stream_key="authentication",
                last_event_id=None,
                last_hash=None,
                version=0,
            )
        )
        session.commit()
        yield session


def test_authentication_evidence_is_redacted_chained_and_never_commits(db: Session):
    aggregate_id = str(uuid.uuid4())
    audit = append_authentication_event(
        db,
        actor_user_id=None,
        action="authentication.sms.challenge_created",
        aggregate_type="login_challenge",
        aggregate_id=aggregate_id,
        request_id="web-request-0001",
        client_type="web",
        outcome="accepted",
        reason_code="challenge_created",
        occurred_at=NOW,
        before_status=None,
        after_status="pending",
    )
    transition = add_authentication_state_transition(
        db,
        aggregate_type="login_challenge",
        aggregate_id=aggregate_id,
        actor_user_id=None,
        from_status=None,
        to_status="pending",
        reason_code="challenge_created",
        request_id="web-request-0001",
        occurred_at=NOW,
    )

    persisted = json.dumps(
        {
            "before": audit.before_jsonb,
            "after": audit.after_jsonb,
            "metadata": transition.metadata_jsonb,
        },
        ensure_ascii=False,
    )
    for secret in ("13800000000", "246810", "openid-value", "raw-token", "10.0.0.8"):
        assert secret not in persisted
    assert audit.request_id.startswith("authreq-")
    assert audit.request_id != "web-request-0001"
    assert transition.metadata_jsonb["request_id"] == audit.request_id
    assert db.get(AuditEvent, audit.id) is not None
    assert db.get(StateTransitionEvent, transition.id) is not None

    db.rollback()
    assert db.scalar(select(AuditEvent.id)) is None
    assert db.scalar(select(StateTransitionEvent.id)) is None
    head = db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == "authentication"))
    assert head is not None and head.version == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "authentication.sms code"),
        ("outcome", "13800000000"),
        ("reason_code", "bad/token"),
    ],
)
def test_authentication_evidence_rejects_values_outside_closed_codes(
    db: Session,
    field: str,
    value: str,
):
    arguments = {
        "actor_user_id": None,
        "action": "authentication.sms.rejected",
        "aggregate_type": "login_challenge",
        "aggregate_id": str(uuid.uuid4()),
        "request_id": "web-request-0002",
        "client_type": "web",
        "outcome": "rejected",
        "reason_code": "identity_not_ready",
        "occurred_at": NOW,
    }
    arguments[field] = value
    with pytest.raises(AuthenticationEvidenceError):
        append_authentication_event(db, **arguments)
