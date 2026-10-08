"""Read-only scrap preparation after real API-role correction approval.

Receives only the synthetic owned-cluster fixture. No scrap posting or public
endpoint is installed. Storage is the existing in-memory file-test adapter.
"""
from uuid import UUID, uuid4

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import FileObject
from app.stock_scrap_schemas import ScrapPreview
from app.formal_services import formal_files, stock_scrap_plan
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import bound_commands, reversal_stock
from app.formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove,
)
from pg16_loss_multigeneration_fixture import exercise as original_fixture
from pg16_loss_correction_gate import stock_snapshot
from test_formal_files_service import FakeStorage, SECRET


def exercise(context, *, require_database_read_only=True):
    original = original_fixture(context)
    owner, api = (context['engines'][role] for role in ('star_oam_migrator', 'star_oam_api'))
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        selection = ReversalPreview(**original['binding'], reversed_correction_id=None,
            expected_execution_request_hash=original['binding']['expected_root_request_hash'])
        preview = reversal_stock.prepare(db, actor=actor, request=selection)
        reversed_result = bound_commands.inverse(db, actor=actor, request=ReversalExecute(
            **selection.model_dump(), expected_plan_hash=preview.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
        approval = CorrectionApprove(**original['binding'],
            reversal_id=UUID(reversed_result['reversal_id']), expected_reversal_hash=reversed_result['request_hash'],
            disposition='scrap', request_id=uuid4().hex, idempotency_key=uuid4().hex)
        approved = bound_commands.approve(db, actor=load_formal_principal(db, context['admin_id']), request=approval)
        db.commit()
        actor = load_formal_principal(db, context['admin_id'])
        storage = FakeStorage()
        upload = formal_files.create_file_upload_intent(db, actor=actor,
            command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',
                original_filename='synthetic-scrap-execution.jpg', size_bytes=128,
                mime_type='image/jpeg', sha256='9' * 64),
            idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET, trace_request_id=uuid4().hex,
            storage=storage, upload_ttl_seconds=60)
        storage.materialize(db.get(FileObject, upload.file_id))
        formal_files.complete_file_upload(db, actor=actor, file_id=upload.file_id,
            trace_request_id=uuid4().hex, storage=storage)
        db.commit()
        request = ScrapPreview(source=dict(kind='correction',
            **{key: value for key, value in original['binding'].items() if key != 'reason'},
            reversal_id=UUID(reversed_result['reversal_id']), expected_reversal_hash=reversed_result['request_hash'],
            correction_decision_id=UUID(approved['correction_decision_id']),
            expected_correction_decision_hash=approved['request_hash']),
            execution_reason='Synthetic verified scrap execution evidence', evidence_file_ids=(upload.file_id,))
    before = stock_snapshot(owner)
    statements = []
    def nonmutating(connection, cursor, statement, parameters, execution, executemany):
        assert statement.lstrip().upper().startswith(('SELECT ', 'SAVEPOINT ',
            'RELEASE SAVEPOINT ', 'ROLLBACK TO SAVEPOINT ')), 'preflight emitted a non-query statement'
        statements.append(statement)
    with Session(api) as db:
        if require_database_read_only:
            db.execute(text('SET TRANSACTION READ ONLY'))
        # The listener belongs to this short-lived connection, including failed
        # probes. Do not relax the original strict READ ONLY diagnostic.
        connection = db.connection()
        event.listen(connection, 'before_cursor_execute', nonmutating)
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        actor = load_formal_principal(db, context['admin_id'])
        first = stock_scrap_plan.prepare(db, actor=actor, request=request)
        assert stock_scrap_plan.prepare(db, actor=actor, request=request).plan_hash == first.plan_hash
        value = first.document
        assert value['target_account_id'] is None and value['external_boundary_code'] == 'stock_operation_scrap'
        assert value['stock_effect'] == 'none' and value['predecessor_reversal_id'] == reversed_result['reversal_id']
        assert len(value['serial_ids']) == int(context['tracking'] == 'serial')
        assert all(row['lifecycle_before'] == 'active' and row['lifecycle_after'] == 'scrapped' for row in value['serials'])
        for field in ('expected_root_request_hash', 'expected_reversal_hash', 'expected_correction_decision_hash'):
            bad = request.model_copy(update={'source': request.source.model_copy(update={field: 'f' * 64})})
            try:
                stock_scrap_plan.prepare(db, actor=actor, request=bad)
            except InventoryReadError:
                pass
            else:
                raise AssertionError('changed scrap predecessor produced a plan')
        assert not db.new and not db.dirty and not db.deleted
    assert stock_snapshot(owner) == before
    assert statements
    with owner.begin() as db:
        # The complete opening proof locks this head first. A leaked preview
        # transaction would conflict here; the owned fixture has no writers.
        db.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE NOWAIT')).all()
        db.execute(text('SELECT stream_key FROM audit_chain_heads FOR UPDATE NOWAIT')).all()
    return dict(passed=True, tracking=context['tracking'], actualApiRoleNonmutating=True,
        databaseReadOnlyTransaction=require_database_read_only,
        nonmutatingStatements=len(statements), openingProofRowLocks=any('FOR UPDATE' in s for s in statements),
        ledgerAndAuditLocksReleased=True,
        originalInverseAndApprovalCommitted=True, correctFrozenShare=True, exactAncestorRejection=True,
        stockUnchanged=True, scrapPostingImplemented=False, publicEndpoint=False, productionAcceptance=False)
