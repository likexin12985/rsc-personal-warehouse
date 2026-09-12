"""Real API-role recipient acceptance, replay, rollback and quantity race."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_my_receipt_gate(api_engine, security_engine, *, request_id, posting_id, admin_user_id, reject_serials=True):
    from app.demand_models import MaterialRequest
    from app.foundation_models import AuditEvent, FileObject, Person
    from app.formal_access import load_formal_principal
    from app.inventory_models import CustodyAssignment, InventoryTransaction, OutboundPosting, OutboundPostingSerial, Receipt, ShipmentLine, StockBalance, StockLocation
    from app.models import User
    from app.formal_services import material_request_shipment as shipping
    from app.formal_services import material_request_my_receipt as service
    from app.formal_services import formal_files
    from app.formal_services.material_request_my_receipt_candidates import my_receipt_candidates
    from app.formal_services.material_request_query import MaterialRequestReadError
    from app.material_request_my_receipt_schemas import MyReceiptIn
    from test_material_request_draft_service import SECRET
    from test_formal_files_service import FakeStorage

    now = datetime.now(timezone.utc)
    with Session(security_engine) as db:
        db.execute(text("SET LOCAL ROLE star_oam_migrator"))
        request = db.get(MaterialRequest, request_id)
        person = db.get(Person, request.requester_person_id)
        recipient_user_id = db.scalar(select(User.id).where(User.person_id == person.id))
        recipient_person_id = person.id
        location = db.scalar(select(StockLocation).where(StockLocation.custodian_person_id == person.id, StockLocation.location_type == "personal", StockLocation.status == "active"))
        if location is None:
            parent = StockLocation(id=uuid4(), code=f"PG16-RECEIVE-REGION-{uuid4().hex}", name="PG16 收货区域测试仓", location_type="region", owner_org_id=person.organization_id, status="active")
            db.add(parent); db.flush()
            location = StockLocation(id=uuid4(), code=f"PG16-RECEIVE-{uuid4().hex}", name="PG16 本人收货测试仓", location_type="personal", owner_org_id=person.organization_id, parent_id=parent.id, custodian_person_id=person.id, status="active")
            db.add(location); db.flush()
            db.add(CustodyAssignment(id=uuid4(), location_id=location.id, custodian_person_id=person.id, valid_from=now - timedelta(days=1)))
        location_id = location.id
        db.commit()

    print("PG16 recipient: exact target location prepared", flush=True)

    with Session(api_engine) as db:
        assert db.scalar(text("select current_user")) == "star_oam_api"
        recipient = load_formal_principal(db, recipient_user_id)
        assert recipient.allows(db, "material_request", "receive", target_scope_type="person", target_scope_id=str(recipient_person_id))
        assert not recipient.allows(db, "material_request", "fulfill")
        request = db.get(MaterialRequest, request_id)
        fact = db.get(OutboundPosting, UUID(str(posting_id)))
        serial_ids = tuple(db.scalars(select(OutboundPostingSerial.serial_id).where(OutboundPostingSerial.posting_id == fact.id)).all())
        shipment = shipping.create_shipment(db, actor=load_formal_principal(db, admin_user_id), request_id=request_id, expected_version=request.version, target_location_id=location_id, target_person_id=recipient_person_id, carrier="PG16 人工承运", tracking_no=f"PG16-{uuid4().hex}", shipped_at=(now - timedelta(hours=1)).isoformat(), lines=(SimpleNamespace(outbound_posting_id=fact.id, shipped_qty=fact.outbound_qty, serial_ids=serial_ids),), idempotency_key=f"pg16-receive-shipment-{fact.id}", secret=SECRET, trace_request_id=f"trace-pg16-receive-shipment-{fact.id}")
        db.flush()
        value = MyReceiptIn(expected_request_version=request.version, shipment_id=shipment["shipment_id"], received_at=now.isoformat(), lines=[dict(shipment_line_id=shipment["lines"][0]["shipment_line_id"], accepted_qty=str(fact.outbound_qty), rejected_qty="0.000", condition="normal", accepted_serial_ids=serial_ids)])
        db.commit()

    print("PG16 recipient: API shipment committed", flush=True)
    evidence_id = None
    storage = FakeStorage()
    if serial_ids and reject_serials:
        with Session(api_engine) as db:
            actor = load_formal_principal(db, recipient_user_id)
            uploaded = formal_files.create_file_upload_intent(db, actor=actor,
                command=formal_files.FileUploadIntentInput(purpose="receipt_exception_evidence", original_filename="exception.png", size_bytes=10, mime_type="image/png", sha256="a"*64),
                idempotency_key=f"pg16-receipt-proof-{posting_id}", idempotency_hmac_secret=SECRET,
                trace_request_id=f"trace-proof-{posting_id}", storage=storage, upload_ttl_seconds=600)
            file = db.get(FileObject, uploaded.file_id)
            storage.materialize(file)
            formal_files.complete_file_upload(db, actor=actor, file_id=file.id, trace_request_id=f"complete-proof-{posting_id}", storage=storage)
            evidence_id = file.id
            db.commit()
        original = value.model_dump()
        original["lines"][0].update(accepted_qty="0.000", rejected_qty=original["lines"][0]["accepted_qty"],
            condition="rejected", accepted_serial_ids=(), rejected_serial_ids=serial_ids, exception_evidence_file_id=evidence_id)
        value = MyReceiptIn(**original)
    with Session(api_engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        candidate = my_receipt_candidates(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, shipment_id=value.shipment_id)
        assert candidate.can_receive and candidate.request_version == value.expected_request_version
        assert {s.serial_id for line in candidate.lines for s in line.remaining_serials} == set(serial_ids)
        assert Decimal(candidate.lines[0].unconfirmed_qty) == value.lines[0].accepted_qty + value.lines[0].rejected_qty
    print("PG16 recipient: candidates verified in READ ONLY transaction", flush=True)
    with Session(api_engine) as db:
        assert db.scalar(text("SELECT has_column_privilege(current_user, 'public.material_requests', 'personal_inbound_status', 'UPDATE')"))
        assert not db.scalar(text("SELECT has_table_privilege(current_user, 'public.material_requests', 'UPDATE')"))
        for state in ("accepted", "posted"):
            with pytest.raises(DBAPIError):
                db.execute(text("UPDATE material_requests SET personal_inbound_status = :state, version = version + 1, updated_at = clock_timestamp() WHERE id = :id"), {"state": state, "id": request_id})
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE material_requests SET version = version + 1, updated_at = clock_timestamp() WHERE id = :id"), {"id": request_id})
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        db.rollback()

    def snapshot():
        with Session(api_engine) as db:
            return (tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).order_by(StockBalance.stock_account_id))), db.scalar(select(func.count()).select_from(InventoryTransaction)), db.scalar(select(func.count()).select_from(Receipt)))

    def submit(key):
        with Session(api_engine) as db:
            try:
                result = service.create_my_receipt(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, payload=value, idempotency_key=key, secret=SECRET, trace_request_id=f"trace-{key}")
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    before = snapshot()
    with patch.object(service, "append_audit_event", side_effect=RuntimeError("injected recipient audit failure")):
        with pytest.raises(RuntimeError, match="injected"):
            submit(f"pg16-my-receipt-rollback-{posting_id}")
    assert snapshot() == before
    print("PG16 recipient: late failure rolled back", flush=True)
    barrier = Barrier(2)
    keys = [f"pg16-my-receipt-race-{posting_id}-{n}" for n in range(2)]
    def race(key):
        barrier.wait(timeout=30)
        try:
            return key, submit(key)
        except MaterialRequestReadError as exc:
            return key, exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, keys))
    successes = [(key, result) for key, result in results if not isinstance(result, Exception)]
    failures = [result for _, result in results if isinstance(result, Exception)]
    assert len(successes) == len(failures) == 1
    assert failures[0].category in {"conflict", "precondition_failed"}
    key, result = successes[0]
    assert submit(key).receipt_id == result.receipt_id
    assert snapshot() == (before[0], before[1], before[2] + 1)
    with Session(api_engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        recovered = service.my_receipt_command_status(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, idempotency_key=key, secret=SECRET)
        assert recovered.receipt_id == result.receipt_id and recovered.idempotency_replayed
        traced = service.my_receipt_trace_status(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, trace_request_id=f"trace-{key}")
        assert traced.receipt_id == result.receipt_id and traced.request_hash == result.request_hash
        assert service.my_receipt_command_status(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, idempotency_key=f"unseen-my-receipt-{posting_id}", secret=SECRET) is None
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "my_receipt_registered", AuditEvent.aggregate_id == str(result.receipt_id))) == 1
        candidate = my_receipt_candidates(db, actor=load_formal_principal(db, recipient_user_id), request_id=request_id, shipment_id=value.shipment_id)
        assert not candidate.can_receive and candidate.blocked_reason == "complete"
        assert all(line.unconfirmed_qty == "0.000" and not line.remaining_serials for line in candidate.lines)
    print("PG16 recipient: concurrency, replay and recovery verified", flush=True)
    if evidence_id:
        with Session(api_engine) as db:
            download = formal_files.create_file_download_intent(db, actor=load_formal_principal(db, recipient_user_id), file_id=evidence_id,
                trace_request_id=f"read-proof-{posting_id}", storage=storage, download_ttl_seconds=120)
            assert download.file_id == evidence_id and download.purpose == "receipt_exception_evidence"
            db.commit()
        print("PG16 recipient: formal exception file bound and readable", flush=True)
    from pg16_inbound_gate import assert_inbound_gate
    assert_inbound_gate(api_engine, security_engine, request_id=request_id,
        receipt_id=result.receipt_id, admin_user_id=admin_user_id)
    if serial_ids and reject_serials:
        # The second immutable outbound slice provides an independent accepted
        # SN receipt. Keep the first rejection intact and prove that only the
        # accepted serial moves from in-transit to personal available stock.
        with Session(api_engine) as db:
            remaining = db.scalar(select(OutboundPosting.id).where(
                OutboundPosting.request_id == request_id,
                OutboundPosting.id.not_in(select(ShipmentLine.outbound_posting_id)),
                OutboundPosting.id.in_(select(OutboundPostingSerial.posting_id)),
            ).order_by(OutboundPosting.id).limit(1))
            assert remaining is not None
        assert_my_receipt_gate(api_engine, security_engine, request_id=request_id,
            posting_id=remaining, admin_user_id=admin_user_id, reject_serials=False)
