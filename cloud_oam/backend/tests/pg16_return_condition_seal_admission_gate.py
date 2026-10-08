"""Owned PG16 API admission reads; no seal is inserted or claimed durable."""
from datetime import datetime, timezone
import json
from unittest.mock import patch

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import RolePermission
from app.inventory_models import CustodyAssignment
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_seal_admission as subject
from app.formal_services.stock_loss_corrections import return_condition_request_inputs as inputs
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from pg16_return_condition_authority_gate import owned_role_connection


def run(owner, api, *, directory, original, command, permission_link_id):
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        actor = load_formal_principal(db, original['receiverUserId'])
        admitted = subject.authorize_absence_seal(db, actor=actor, request=command)
        assert json.loads(admitted.original_input_json) == inputs.canonical(command)
        assert not admitted.absence_sealed and not admitted.retry_allowed
        source = admitted.basis.source
        db.rollback()

    # All mutable fixture changes stay in this validated, owned connection and
    # are rolled back. The service itself always executes with the real API role.
    with owned_role_connection(owner, directory) as connection:
        connection.rollback()
        with Session(bind=connection) as db:
            custody = db.scalars(select(CustodyAssignment).where(
                CustodyAssignment.location_id == source.location_id,
                CustodyAssignment.custodian_person_id == source.custodian_person_id,
                CustodyAssignment.valid_to.is_(None))).one()
            custody.valid_to = datetime.now(timezone.utc)
            db.flush()
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            actor = load_formal_principal(db, original['receiverUserId'])
            try:
                authority.authorize_submission(db, actor=actor, inbound_line_id=command.inbound_line_id)
            except InventoryReadError as error:
                assert error.code == 'return_condition_custody_changed'
            else:
                raise AssertionError('expired custody admitted new stock action')
            closed = subject.authorize_absence_seal(db, actor=actor, request=command)
            assert closed.basis == admitted.basis and closed.original_input_hash == admitted.original_input_hash
            db.rollback()

    with owned_role_connection(owner, directory) as connection:
        connection.rollback()
        with Session(bind=connection) as db:
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            actor = load_formal_principal(db, original['receiverUserId'])
            original_read = subject.history.read
            called = []
            def revoke(*args, **kwargs):
                result = original_read(*args, **kwargs)
                called.append(True)
                db.execute(text('SET LOCAL ROLE star_oam_migrator'))
                db.execute(update(RolePermission).where(RolePermission.id == permission_link_id).values(effect='deny'))
                db.execute(text('SET LOCAL ROLE star_oam_api'))
                return result
            with patch.object(subject.history, 'read', revoke):
                try:
                    subject.authorize_absence_seal(db, actor=actor, request=command)
                except InventoryReadError as error:
                    assert error.code == 'return_condition_forbidden'
                else:
                    raise AssertionError('late action revocation admitted seal preparation')
            assert called == [True]
            db.rollback()
    return dict(actualApiReadOnly=True, expiredCustodyAllowsClosurePreparation=True,
        expiredCustodyRefusesNewStockAction=True, lateActionRevocationRefused=True,
        fullOriginalInputRetained=True, durableSeal=False, lateRequestCommitFence=False)
