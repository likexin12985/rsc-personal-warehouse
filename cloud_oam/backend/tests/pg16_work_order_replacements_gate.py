"""Disposable PostgreSQL 16 replacement proofs; all stock uses the API ledger."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import psycopg
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.demand_models import OamWorkOrder, WorkOrderReplacement
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as service
from app.foundation_models import ExternalObject, SourceSystem
from app.inventory_models import (FormalMaterial, InventorySerial, SerialCurrentPosition,
    StockAccount, StockBalance, StockLocation)
from app.models import User
from pg16_work_order_material_gate import World, _checkpoint, _run, _snapshot


def assert_replacement_migration_roundtrip(gate):
    parameters = gate._connection_parameters(role="star_oam_migrator", password=gate._role_password("star_oam_migrator"))
    signature = "public.rsc_oam_runtime_binding_ready_0044()"
    def catalog():
        with psycopg.connect(**parameters) as db:
            old_functions = db.execute("SELECT oid, proowner, proacl, prosecdef, proconfig, prosrc FROM pg_proc WHERE oid IN ('public.rsc_oam_runtime_binding_ready_0044()'::regprocedure,'public.rsc_require_opening_observation_account_0023()'::regprocedure,'public.rsc_check_work_order_material_transaction_0090(uuid)'::regprocedure,'public.rsc_check_serial_lifecycle_0092(uuid)'::regprocedure) ORDER BY oid").fetchall()
            new_functions = db.execute("SELECT proname, prosrc, proacl FROM pg_proc WHERE proname LIKE '%%_0093' ORDER BY proname").fetchall()
            triggers = db.execute("SELECT tgname, tgenabled, tgtype, tgdeferrable, tginitdeferred FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_%%_0093' ORDER BY tgname").fetchall()
            columns = db.execute("SELECT c.relname, a.attname FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped WHERE c.relnamespace='public'::regnamespace AND (c.relname='work_order_replacements' OR (c.relname IN ('work_order_material_operations','work_order_replacement_pairs') AND a.attname='replacement_id')) ORDER BY c.relname,a.attname").fetchall()
            return old_functions,new_functions,triggers,columns
    before = catalog()
    assert len(before[1]) == 2 and len(before[2]) == 5
    gate._run_alembic("downgrade", "20261002_0092")
    previous = catalog()
    assert [r[:5] for r in previous[0]] == [r[:5] for r in before[0]]
    assert not any(previous[1:])
    with psycopg.connect(**parameters) as db:
        definition = db.execute("SELECT pg_get_functiondef(CAST(%s AS regprocedure))", (signature,)).fetchone()[0]
        db.execute(definition.replace("AS $function$", "AS $function$\n-- isolated 0093 late CAS failure\n"))
    try:
        drifted = catalog()
        blocked = gate._run_alembic("upgrade", "head", expect_success=False)
        assert "work_order_replacement_readiness_0093" in blocked.stdout + blocked.stderr
        assert gate._current_revision() == "20261002_0092" and catalog() == drifted
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(definition)
    gate._run_alembic("upgrade", "head")
    assert catalog() == before
    target = "public.rsc_check_work_order_replacement_0093(uuid)"
    with psycopg.connect(**parameters) as db:
        definition = db.execute("SELECT pg_get_functiondef(CAST(%s AS regprocedure))", (target,)).fetchone()[0]
        db.execute(definition.replace("AS $function$", "AS $function$\n-- isolated 0093 source drift\n"))
    try:
        drifted = catalog()
        blocked = gate._run_alembic("downgrade", "20261002_0092", expect_success=False)
        assert "0093 function source, configuration or ownership drift" in blocked.stdout + blocked.stderr
        assert catalog() == drifted and gate._current_revision() == gate.HEAD_REVISION
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(definition)
    with psycopg.connect(**parameters) as db:
        db.execute(f"GRANT EXECUTE ON FUNCTION {target} TO star_oam_api")
    try:
        drifted = catalog()
        blocked = gate._run_alembic("downgrade", "20261002_0092", expect_success=False)
        assert "0093 function source, configuration or ownership drift" in blocked.stdout + blocked.stderr
        assert catalog() == drifted
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(f"REVOKE EXECUTE ON FUNCTION {target} FROM star_oam_api")
    assert catalog() == before
    with psycopg.connect(**parameters) as db:
        assert db.execute("SELECT has_table_privilege('star_oam_backup','public.work_order_replacements','SELECT')").fetchone()[0]
    print("PG16 0093 roundtrip, late CAS rollback, source/ACL drift rejection and backup read PASS", flush=True)


def replacement_worlds(engine):
    """Use stock left by the preceding gates, including the consumed SN."""
    from types import SimpleNamespace
    from work_order_fixtures import add_order, canonical_source
    worlds = {}
    with Session(engine) as db:
        candidates = db.scalars(select(StockAccount).join(StockBalance,
            StockBalance.stock_account_id == StockAccount.id).join(StockLocation,
            StockLocation.id == StockAccount.location_id).where(StockLocation.location_type == "personal",
                StockAccount.availability_bucket == "available", StockAccount.condition_code == "new",
                StockBalance.quantity >= 1).order_by(StockBalance.quantity.desc(),StockAccount.id)).all()
        source = canonical_source(db)
        for account in candidates:
            serials = tuple(db.scalars(select(SerialCurrentPosition.serial_id).where(
                SerialCurrentPosition.stock_account_id == account.id).order_by(SerialCurrentPosition.serial_id)))
            kind = "serial" if serials else "quantity"
            if kind in worlds:
                continue
            actor_id = db.scalar(select(User.id).where(User.person_id==account.custodian_person_id))
            if actor_id is None:
                continue
            dimensions = {key:getattr(account,key) for key in ("owner_org_id","custodian_person_id","location_id","material_id","condition_code","lot_id")}
            reserved = db.scalar(select(StockAccount.id).filter_by(**dimensions,availability_bucket="reserved"))
            assert reserved is not None, "preceding work-order gates provide reserved dimensions"
            order = add_order(db, SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),
                organization=SimpleNamespace(id=account.owner_org_id)), source)
            removed = db.scalar(select(InventorySerial.id).where(InventorySerial.material_id==account.material_id,
                InventorySerial.lifecycle_status=="consumed").order_by(InventorySerial.id)) if serials else None
            if serials:
                assert removed is not None, "preceding SN gate supplies a terminal SN to recover"
            worlds[kind] = (World(actor_id,account.id,reserved,(order.id,),serials[:1]),removed)
        db.commit()
    assert set(worlds)=={"quantity","serial"}
    return worlds


def replacement_inputs(db, world, removed):
    account = db.get(StockAccount,world.reserved_id)
    sku = db.get(FormalMaterial,account.material_id)
    def proofs(ids):
        return tuple(material.SerialVerificationInput(row.id,sku.sku_code,row.serial_no,row.qr_code)
            for row in db.scalars(select(InventorySerial).where(InventorySerial.id.in_(ids))))
    consumed = (material.WorkOrderMaterialLineInput(account.material_id,account.id,Decimal(1),
        world.serial_ids,account.condition_code,proofs(world.serial_ids)),)
    ids = (removed,) if removed else ()
    recovered = (service.RecoveryLineInput(account.id,account.material_id,Decimal(1),"used",
        lot_id=account.lot_id,serial_ids=ids,serial_verifications=proofs(ids)),)
    pairs = (material.WorkOrderReplacementPairInput(world.serial_ids[0],removed),) if removed else ()
    return dict(consume_lines=consumed,recover_lines=recovered,pairs=pairs)


def run_replacement(db, world, removed, *, key=None):
    key = key or uuid4().hex
    return service.execute_replacement(db,actor=load_formal_principal(db,world.actor_id),
        work_order_id=world.orders[0],**replacement_inputs(db,world,removed),
        idempotency_key=key,request_id="pg16-replace-"+key)


def assert_replacement_rollback_smoke(api_engine, fixture_engine):
    worlds = replacement_worlds(fixture_engine)
    for kind,(world,removed) in worlds.items():
        before = _snapshot(api_engine)
        with Session(api_engine) as db:
            _run(db,world,"occupy"); _checkpoint(db)
            replacement = run_replacement(db,world,removed)
            _checkpoint(db)
            assert replacement.consume_operation_id != replacement.recover_operation_id
            if removed:
                assert db.get(InventorySerial,removed).lifecycle_status=="active"
                assert db.get(InventorySerial,world.serial_ids[0]).lifecycle_status=="consumed"
                target = db.get(SerialCurrentPosition,removed).stock_account_id
                assert db.get(StockAccount,target).condition_code=="used"
            db.rollback()
        assert _snapshot(api_engine)==before
        print("PG16 0093 "+kind+" consume/recovery paired posting and complete rollback PASS",flush=True)
    return worlds


def replacement_snapshot(engine):
    with engine.connect() as db:
        parent_counts = tuple(db.execute(text("SELECT count(*) FROM " + table)).scalar_one()
            for table in ("work_order_replacements","work_order_replacement_pairs"))
    return _snapshot(engine),parent_counts


def assert_replacement_failures_and_replay(api_engine,worlds):
    from dataclasses import replace
    from unittest.mock import patch
    from sqlalchemy.exc import DBAPIError
    from app.foundation_models import OutboxEvent
    from app.formal_services.work_order_replacement_read import replacement_result
    from app.demand_models import WorkOrderReplacementPair
    for kind,(world,removed) in worlds.items():
        before=replacement_snapshot(api_engine)
        with Session(api_engine) as db:
            _run(db,world,"occupy");_checkpoint(db)
            key=uuid4().hex
            result=run_replacement(db,world,removed,key=key);_checkpoint(db)
            actor=load_formal_principal(db,world.actor_id)
            read=replacement_result(db,replacement=result,actor=actor)
            again=run_replacement(db,world,removed,key=key)
            assert again.id==read.replacement_id
            changed=replacement_inputs(db,world,removed)
            changed["recover_lines"]=(replace(changed["recover_lines"][0],condition_before="damaged"),)
            try:
                service.execute_replacement(db,actor=actor,work_order_id=world.orders[0],**changed,
                    idempotency_key=key,request_id="pg16-replace-"+key)
            except material.InventoryPostingError as exc:
                assert exc.code=="idempotency_conflict"
            else:
                raise AssertionError("same replacement key accepted changed body")
            db.rollback()
        assert replacement_snapshot(api_engine)==before

        # All callbacks deliberately omit part of a fresh group before SQL commit.
        def attempt(mode):
            with Session(api_engine) as db:
                try:
                    _run(db,world,"occupy");_checkpoint(db)
                    if mode=="half":
                        with patch.object(material,"execute_recover_operation",return_value=(None,None)):
                            run_replacement(db,world,removed)
                    elif mode=="outbox":
                        add=db.add
                        def skip(row,*args,**kwargs):
                            if isinstance(row,OutboxEvent) and row.event_type=="work_order_material_replacement_posted":
                                return
                            add(row,*args,**kwargs)
                        with patch.object(db,"add",side_effect=skip):
                            run_replacement(db,world,removed)
                    elif mode=="audit":
                        with patch.object(service,"append_audit_event",return_value=None):
                            run_replacement(db,world,removed)
                    elif mode=="pair":
                        add_all=db.add_all
                        def skip_pairs(rows,*args,**kwargs):
                            add_all([row for row in rows if not isinstance(row,WorkOrderReplacementPair)],*args,**kwargs)
                        with patch.object(db,"add_all",side_effect=skip_pairs), patch.object(service.inventory,"has_serial_recovery_command",return_value=True):
                            run_replacement(db,world,removed)
                    _checkpoint(db)
                except DBAPIError as exc:
                    assert exc.orig.sqlstate in {"23503","23514"},str(exc.orig)
                    if mode in {"audit","outbox"}:
                        assert "0093 replacement command audit or outbox mismatch" in str(exc.orig)
                else:
                    raise AssertionError("database committed incomplete replacement: "+mode)
                finally:
                    db.rollback()
            assert replacement_snapshot(api_engine)==before
        for mode in ("half","outbox","audit")+(('pair',) if removed else ()):
            attempt(mode)
        print("PG16 0093 "+kind+" original-result proof/replay, changed-key body and missing half/audit/outbox/pair rollback PASS",flush=True)


def assert_replacement_concurrency_and_http(api_engine,worlds):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_material as api
    from app.formal_services.work_order_replacement_read import replacement_result
    from app.formal_services.inventory_query import list_inventory_accounts, inventory_transaction_detail
    from sqlalchemy.exc import DBAPIError
    winners=[]
    for kind,(world,removed) in worlds.items():
        with Session(api_engine) as db:
            _run(db,world,"occupy"); db.commit()
        before=replacement_snapshot(api_engine)
        barrier=Barrier(2)
        def worker(key):
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                barrier.wait(timeout=10)
                try:
                    result=run_replacement(db,world,removed,key=key)
                    output=replacement_result(db,replacement=result,actor=load_formal_principal(db,world.actor_id))
                    db.commit()
                    return "posted",output,key
                except material.InventoryPostingError as exc:
                    db.rollback()
                    return exc.code,None,key
        keys=[uuid4().hex for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker,key) for key in keys]
            results=[future.result(timeout=40) for future in futures]
        assert sorted(row[0] for row in results)==["posted","work_order_reservation_insufficient"]
        winner=next(row for row in results if row[0]=="posted")
        after=replacement_snapshot(api_engine)
        assert after[1][0]==before[1][0]+1 and after[1][1]==before[1][1]+(1 if removed else 0)
        # Two simultaneous retries return the same original object and no new facts.
        barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker,winner[2]) for _ in range(2)]
            replays=[future.result(timeout=40) for future in futures]
        assert all(row[0]=="posted" and row[1]==winner[1] for row in replays)
        assert replacement_snapshot(api_engine)==after
        with Session(api_engine) as db:
            actor=load_formal_principal(db,world.actor_id)
            app=FastAPI();app.include_router(api.router,prefix="/api")
            app.dependency_overrides[get_db]=lambda:db
            for route in api.router.routes:
                for dependency in route.dependant.dependencies:
                    if dependency.name=="principal":
                        app.dependency_overrides[dependency.call]=lambda:actor
            with TestClient(app) as client:
                path=f"/api/v1/work-orders/{world.orders[0]}/material-replacements"
                response=client.get(path+"/by-request/pg16-replace-"+winner[2])
                assert response.status_code==200,response.text
                fact=db.get(WorkOrderReplacement,winner[1].replacement_id)
                assert response.json()=={**winner[1].model_dump(mode="json"),
                    "operator_person_id":str(actor.person_id),"request_id":fact.request_id,"request_hash":fact.request_hash}
                assert response.headers["cache-control"]=="private, no-store"
                original=db.get(WorkOrderReplacement,winner[1].replacement_id).command_jsonb
                body={key:value for key,value in original.items() if key!="work_order_id"}
                body["consume_lines"]=[{key:value for key,value in line.items() if key!="target_stock_account_id"} for line in original["consume_lines"]]
                body.update(idempotency_key=winner[2],request_id="pg16-replace-"+winner[2])
                response=client.post(path,json=body)
                assert response.status_code==200,response.text
                assert response.json()==winner[1].model_dump(mode="json")
                absent=client.get(path+"/by-request/pg16-unknown-request")
                assert absent.status_code==404
            page=list_inventory_accounts(db,actor=actor,limit=100)
            assert page.items
            detail=inventory_transaction_detail(db,actor=actor,transaction_id=winner[1].recover_transaction_id)
            assert detail.movements[0].from_account_id is None
        assert replacement_snapshot(api_engine)==after
        # Neither API nor owner may overwrite or erase a committed replacement.
        with Session(api_engine) as db:
            try:
                db.execute(text("UPDATE work_order_replacements SET request_hash = repeat('0',64) WHERE id=:id"),{"id":winner[1].replacement_id})
            except DBAPIError as exc:
                assert exc.orig.sqlstate=="42501"
            else:
                raise AssertionError("API could overwrite an immutable replacement")
            finally:
                db.rollback()
        winners.append(winner[1])
        print("PG16 0093 "+kind+" different-key one-winner, simultaneous replay, original request HTTP/read inventory PASS",flush=True)
    return winners


def assert_work_order_replacements_gate(api_engine,fixture_engine):
    worlds=assert_replacement_rollback_smoke(api_engine,fixture_engine)
    assert_replacement_failures_and_replay(api_engine,worlds)
    return assert_replacement_concurrency_and_http(api_engine,worlds)


def assert_replacement_history_gate(api_engine):
    """Both stock transactions are proven through GET in a READ ONLY session."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.demand_models import WorkOrderMaterialOperation
    from app.routers import formal_work_order_material as api
    from app.formal_services.work_order_replacement_read import lookup_replacement
    from app.inventory_models import InventoryTransaction
    baseline=replacement_snapshot(api_engine)
    kinds=set()
    with Session(api_engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        facts=tuple(db.scalars(select(WorkOrderReplacement).order_by(WorkOrderReplacement.id)))
        assert len(facts)>=2
        for original in facts:
            operation=db.get(WorkOrderMaterialOperation,original.consume_operation_id)
            transaction=db.get(InventoryTransaction,operation.posting_transaction_id)
            actor=load_formal_principal(db,transaction.actor_user_id)
            output=lookup_replacement(db,actor=actor,work_order_id=original.oam_work_order_id,request_id=original.request_id)
            assert output.replacement_id==original.id and output.request_hash==original.request_hash
            app=FastAPI();app.include_router(api.router,prefix="/api")
            app.dependency_overrides[get_db]=lambda:db
            for route in api.router.routes:
                for dependency in route.dependant.dependencies:
                    if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:actor
            with TestClient(app) as client:
                response=client.get(f"/api/v1/work-orders/{original.oam_work_order_id}/material-replacements/by-request/{original.request_id}")
                assert response.status_code==200,response.text
                assert response.json()==output.model_dump(mode="json")
                assert response.headers["cache-control"]=="private, no-store"
                assert "qr_code" not in response.text and "command_jsonb" not in response.text
            kinds.add("serial" if original.command_jsonb["consume_lines"][0]["serial_ids"] else "quantity")
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert kinds=={"quantity","serial"}
    assert replacement_snapshot(api_engine)==baseline
    print("PG16 quantity/SN replacement request/hash, both audit chains and READ ONLY HTTP proof PASS",flush=True)
