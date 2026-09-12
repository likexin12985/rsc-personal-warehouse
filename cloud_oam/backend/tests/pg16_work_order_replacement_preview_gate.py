"""Quantity/SN scan and batch preview agree with actual paired HTTP posting."""
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as replacements
from app.inventory_models import InventoryMovement, InventorySerial, FormalMaterial, StockAccount
from app.routers import formal_work_order_query, formal_work_order_material
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_replacements_gate import replacement_snapshot
from pg16_work_order_query_gate import query_worlds


def _mini_contract(fixture,script_name="work-order-replacement-pg16.cjs"):
    node=shutil.which("node")
    assert node,"Node is required for the paired replacement client contract"
    script=Path(__file__).resolve().parents[2]/"miniprogram/tests/fixtures"/script_name
    result=subprocess.run([node,str(script)],input=json.dumps(fixture),text=True,capture_output=True,
        timeout=30,env={"PATH":os.environ.get("PATH","")})
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def assert_replacement_preview_gate(api_engine, fixture_engine):
    for kind, (_account, user_id, orders, line) in query_worlds(fixture_engine).items():
        baseline=replacement_snapshot(api_engine)
        with Session(api_engine) as db:
            actor=load_formal_principal(db,user_id)
            occupied,_=material.execute_occupy_operation(db,actor=actor,work_order_id=orders[0],lines=(line,),
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
            pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0],removed.id),) if removed else ()
            command=replacements.replacement_request_payload(work_order_id=orders[0],operator_person_id=actor.person_id,
                consume_lines=(replace(line,stock_account_id=reserved),),recover_lines=(recovered,),pairs=pairs)
            body={key:value for key,value in command.items() if key!="work_order_id"}
            body["consume_lines"]=[{key:value for key,value in item.items() if key!="target_stock_account_id"} for item in body["consume_lines"]]
            scan={"operator_person_id":str(actor.person_id),"basis_stock_account_id":str(reserved),
                "sku_code":sku.sku_code,"condition_before":"damaged",
                "serial_no":removed.serial_no if removed else None,"qr_code":removed.qr_code if removed else None}
            mini_fixture={"input":{"workOrderId":str(orders[0]),"personId":str(actor.person_id),
                "consumeLines":body["consume_lines"],"recoverLines":body["recover_lines"],"pairs":body["replacement_pairs"]},
                "scan":scan,"expectedHash":replacements._hash(command)}
            built=_mini_contract({**mini_fixture,"mode":"build"})
            assert built["body"]==body and built["scan"]=={**scan,"lot_no":None}
            # Submit the body constructed by the actual mini-program code.
            body=built["body"];scan=built["scan"]
            app=FastAPI()
            app.dependency_overrides[get_db]=lambda:db
            for router in (formal_work_order_query.router,formal_work_order_material.router):
                app.include_router(router,prefix="/api")
                for route in router.routes:
                    for dependency in route.dependant.dependencies:
                        if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:actor
            # Snapshot the same uncommitted transaction; previews must not add
            # accounts, balances, stock, SN, audits, pairs or outbox rows.
            local=SimpleNamespace(connect=lambda:nullcontext(db.connection()))
            before_preview=replacement_snapshot(local)
            path=f"/api/v1/work-orders/{orders[0]}/material-replacements"
            with TestClient(app) as client:
                scanned=client.post(path+"/removed-part",json=scan)
                assert scanned.status_code==200,scanned.text
                assert scanned.headers["cache-control"]=="private, no-store"
                assert scanned.json()["material_id"]==str(line.material_id) and "qr_code" not in scanned.text
                preview=client.post(path+"/preview",json=body)
                assert preview.status_code==200,preview.text
                assert preview.json()["request_hash"]==replacements._hash(command)
                assert preview.headers["cache-control"]=="private, no-store" and "qr_code" not in preview.text
                if removed:
                    assert client.post(path+"/removed-part",json={**scan,"qr_code":"WRONG"}).status_code==412
                assert replacement_snapshot(local)==before_preview
                assert not db.new and not db.dirty and not db.deleted
                trace="wxreq-"+uuid4().hex+uuid4().hex[:4]
                with patch.object(db,"commit",side_effect=lambda:_checkpoint(db)):
                    posted=client.post(path,json={**body,"idempotency_key":uuid4().hex,"request_id":trace},headers={"X-Request-ID":trace})
                assert posted.status_code==200,posted.text
                assert posted.json()["consume_transaction_id"]!=posted.json()["recover_transaction_id"]
                original=client.get(path+"/by-request/"+trace)
                assert original.status_code==200,original.text
                assert original.json()["request_hash"]==preview.json()["request_hash"]
                assert _mini_contract({**mini_fixture,"mode":"validate","scan":scan,
                    "context":{"authorizationVersion":actor.authorization_version,
                        "sourceVersion":preview.json()["source_version"],"ledgerCursor":scanned.json()["ledger_cursor"]},
                    "preview":preview.json(),"removed":scanned.json(),"result":original.json(),"trace":trace})=={"validated":True}
            db.rollback()
        assert replacement_snapshot(api_engine)==baseline
        print(f"PG16 {kind} mini parent hash/scan/preview/recovery, paired HTTP post and full rollback PASS",flush=True)
