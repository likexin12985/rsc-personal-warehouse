"""Actual parent seal/post races and durable proof on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as inventory
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as replacements
from app.formal_services import work_order_command_seal as ordinary
from app.formal_services import work_order_replacement_seal as seals
from app.inventory_models import InventoryMovement, InventorySerial, FormalMaterial, StockAccount, StockBalance
from pg16_work_order_material_gate import _checkpoint, _snapshot
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_replacements_gate import replacement_snapshot


def _case(db, user_id, order, line):
    actor=load_formal_principal(db,user_id)
    occupied,_=material.execute_occupy_operation(db,actor=actor,work_order_id=order,lines=(line,),
        idempotency_key=uuid4().hex,request_id=uuid4().hex)
    _checkpoint(db)
    reserved=db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id==occupied.posting_transaction_id))
    account=db.get(StockAccount,reserved);sku=db.get(FormalMaterial,line.material_id)
    removed=db.scalar(select(InventorySerial).where(InventorySerial.material_id==line.material_id,
        InventorySerial.lot_id==account.lot_id,InventorySerial.lifecycle_status=="consumed")
        .order_by(InventorySerial.id).limit(1)) if line.serial_ids else None
    assert removed is not None or not line.serial_ids
    recovered=replacements.RecoveryLineInput(reserved,line.material_id,line.quantity,"damaged",lot_id=account.lot_id,
        serial_ids=(removed.id,) if removed else (),serial_verifications=(material.SerialVerificationInput(
            removed.id,sku.sku_code,removed.serial_no,removed.qr_code),) if removed else ())
    args=dict(actor=actor,work_order_id=order,consume_lines=(replace(line,stock_account_id=reserved),),
        recover_lines=(recovered,),pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0],removed.id),) if removed else (),
        idempotency_key=uuid4().hex,request_id="wxreq-"+uuid4().hex+uuid4().hex[:4])
    payload=replacements.replacement_request_payload(operator_person_id=actor.person_id,
        **{key:args[key] for key in ("work_order_id","consume_lines","recover_lines","pairs")})
    sealing={key:args[key] for key in ("actor","work_order_id","request_id")} | {"request_hash":replacements._hash(payload)}
    return args,sealing


def assert_replacement_seal_atomic_gate(api_engine, fixture_engine):
    for kind,(_,user_id,orders,line) in query_worlds(fixture_engine).items():
        before=replacement_snapshot(api_engine)
        with Session(api_engine) as db:
            args,sealing=_case(db,user_id,orders[0],line)
            posted=replacements.execute_replacement(db,**args);_checkpoint(db)
            proof=seals.seal_replacement(db,**sealing)
            assert proof.replacement_id==posted.id and proof.consume_transaction_id!=proof.recover_transaction_id
            db.rollback()
        for first in ("seal","post"):
            try:
                with Session(api_engine) as db:
                    args,sealing=_case(db,user_id,orders[0],line)
                    def post():
                        with patch.object(ordinary,"require_unsealed_request",lambda *a,**k:None):
                            replacements.execute_replacement(db,**args)
                    def seal():
                        with patch.object(seals,"lookup_replacement_result",lambda *a,**k:None):
                            seals.seal_replacement(db,**sealing)
                    (seal if first=="seal" else post)()
                    (post if first=="seal" else seal)()
                    _checkpoint(db)
            except DBAPIError as exc:
                assert getattr(exc.orig,"sqlstate",None)=="23514" and "0095" in str(exc.orig)
            else:raise AssertionError("parent posting and seal coexisted")
        assert replacement_snapshot(api_engine)==before
        print(f"PG16 {kind} complete parent proof and both seal/post insertion orders PASS",flush=True)


def ensure_seal_stock(api_engine,fixture_engine,worlds,admin_user_id):
    """Keep earlier gate evidence intact; replace consumed synthetic test stock
    through the same admitted API-ledger fixture boundary, never balance edits.
    """
    plans=[]
    with Session(fixture_engine) as db:
        for kind,(account_id,_,_,_) in worlds.items():
            account=db.get(StockAccount,account_id)
            if db.get(StockBalance,account_id).quantity>=1:continue
            amount=Decimal(1)-db.get(StockBalance,account_id).quantity
            identifiers=tuple(uuid4() for _ in range(int(amount))) if kind=="serial" else ()
            now=datetime.now(timezone.utc)
            db.add_all(InventorySerial(id=identifier,material_id=account.material_id,lot_id=account.lot_id,
                serial_no="PG16-SEAL-"+identifier.hex,qr_code="PG16-SEAL-QR-"+identifier.hex,
                lifecycle_status="active",created_at=now,updated_at=now) for identifier in identifiers)
            plans.append((account_id,amount,identifiers))
        if plans:assert admin_user_id,"synthetic stock supplementation requires the fixture administrator"
        db.commit()
    if not plans:return
    with Session(api_engine) as db:
        actor=load_formal_principal(db,admin_user_id)
        for account_id,amount,identifiers in plans:
            key="pg16-replacement-seal-fixture-"+uuid4().hex
            inventory.post_inventory_transaction(db,actor=actor,
                command=inventory.InventoryPostingCommand(transaction_no=key,movement_type="inbound",
                    source_document_type="pg16_work_order_fixture",source_document_id=key,posting_key=key,
                    effective_at=datetime.now(timezone.utc),movements=(inventory.InventoryMovementCommand(
                        None,account_id,amount,identifiers,"PG16_WORK_ORDER_FIXTURE"),)),idempotency_key=key,request_id=key)
        _checkpoint(db);db.commit()


def assert_replacement_seal_commit_gate(api_engine,fixture_engine,worlds,admin_user_id=None):
    ensure_seal_stock(api_engine,fixture_engine,worlds,admin_user_id)
    for kind,(account_id,user_id,orders,line) in query_worlds(fixture_engine).items():
        # Establish real own reservations before the race, so the late parent
        # is otherwise executable. Return them through a real release afterward.
        with Session(api_engine) as db:
            args,sealing=_case(db,user_id,orders[0],line)
            db.commit()
        baseline=_snapshot(api_engine)
        started=Event();identity={}
        def late_post():
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout='10s'"))
                current=load_formal_principal(db,user_id)
                identity["pid"]=db.scalar(text("SELECT pg_backend_pid()"));started.set()
                try:replacements.execute_replacement(db,**{**args,"actor":current,"idempotency_key":uuid4().hex})
                except material.InventoryPostingError as exc:return exc.code
                raise AssertionError("late sealed parent executed")
        with Session(api_engine) as db,ThreadPoolExecutor(max_workers=1) as worker:
            current=load_formal_principal(db,user_id)
            original=seals.seal_replacement(db,**{**sealing,"actor":current});_checkpoint(db)
            future=worker.submit(late_post);assert started.wait(timeout=5)
            blocked=False;until=monotonic()+3
            with fixture_engine.connect() as probe:
                while monotonic()<until:
                    blocked=bool(probe.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),identity))
                    if blocked:break
                    sleep(.02)
            assert blocked,"late parent did not reach the held ledger lock"
            db.commit();assert future.result(timeout=12)=="work_order_request_sealed"
        with Session(api_engine) as db:
            current=load_formal_principal(db,user_id)
            result=seals.lookup_replacement_result(db,actor=current,work_order_id=orders[0],request_id=args["request_id"])
            assert result.seal.seal_id==original.seal.seal_id
            assert seals.seal_replacement(db,**{**sealing,"actor":current}).seal.seal_id==original.seal.seal_id
            db.rollback()
        assert_parent_sealed_http(api_engine,current,original)
        for sql in ("UPDATE public.work_order_command_seals SET request_hash=request_hash WHERE id=:id",
                    "DELETE FROM public.work_order_command_seals WHERE id=:id","TRUNCATE public.work_order_command_seals"):
            for engine in (api_engine,fixture_engine):
                try:
                    with engine.begin() as connection:connection.execute(text(sql),{"id":original.seal.seal_id})
                except DBAPIError as exc:assert getattr(exc.orig,"sqlstate",None) in {"42501","55000"}
                else:raise AssertionError("parent seal was mutable")
        after=_snapshot(api_engine)
        assert after[0][:8]==baseline[0][:8] and after[0][9:]==baseline[0][9:]
        assert after[0][8]==baseline[0][8]+1 and after[1:5]==baseline[1:5]
        before_heads={row[0]:tuple(row[1:]) for row in baseline[5]};after_heads={row[0]:tuple(row[1:]) for row in after[5]}
        assert after_heads["material_request"][0]==before_heads["material_request"][0]+1
        assert {k:v for k,v in before_heads.items() if k!="material_request"}=={k:v for k,v in after_heads.items() if k!="material_request"}
        with Session(api_engine) as db:
            material.execute_release_operation(db,actor=load_formal_principal(db,user_id),work_order_id=orders[0],
                lines=(replace(args["consume_lines"][0],target_stock_account_id=account_id),),
                idempotency_key=uuid4().hex,request_id=uuid4().hex)
            _checkpoint(db);db.commit()
        print(f"PG16 {kind} durable parent seal, valid delayed POST exclusion, HTTP proof and immutable retention PASS",flush=True)


def assert_parent_sealed_http(api_engine,actor,original):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_material as router
    app=FastAPI();app.include_router(router.router,prefix="/api")
    def connection():
        with Session(api_engine) as db:yield db
    app.dependency_overrides[get_db]=connection
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:actor
    seal=original.seal
    path=f"/api/v1/work-orders/{seal.work_order_id}/material-replacements/by-request/{seal.request_id}"
    with TestClient(app) as client:
        result=client.get(path)
        assert result.status_code==200 and result.json()==original.model_dump(mode="json")
        assert result.headers["cache-control"]=="private, no-store"
        replay=client.post(path+"/seal",json={"operator_person_id":str(actor.person_id),"request_hash":seal.request_hash},
            headers={"X-Request-ID":seal.request_id})
        assert replay.status_code==200 and replay.json()==result.json()
        from pg16_work_order_replacement_preview_gate import _mini_contract
        assert _mini_contract({"response":result.json(),"authorizationVersion":actor.authorization_version},
            "work-order-replacement-seal-pg16.cjs")=={"validated":True}
