"""Send the actual mini recovery module's seal payload through FastAPI."""
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from app.stock_operation_models import StockOperationShipment,StockOperationOrder

from test_stock_return_inbound import (db,world,stock,recovered,destination,prepared,parcel,incoming,acceptance,
    inbound_accounts,accepted,application,prefix)


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_actual_mini_seal_request_matches_http_and_only_exact_get_confirms_it(db,accepted):
    script="""const fs=require('node:fs'),{sealPending}=require('./utils/work-order-recovery');
const {validateMarker}=require('./utils/work-order-recovery-store'), c=require('./utils/stock-return-inbound-contract');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const marker=validateMarker({...input,v:1,kind:c.KIND,operation_type:c.ACTION,
trace_request_id:'wxreq-'+ 'b'.repeat(36),request_hash:'a'.repeat(64),plan_hash:'d'.repeat(64)});
let wire,cleared=false;
const store={withLease:async(_,work)=>work({read:()=>({kind:'valid',value:marker}),clearExact:()=>{cleared=true;}})};
const api={request:async()=>{throw Object.assign(new Error('not observed'),{responseReceived:true,status:404,code:'stock_return_inbound_not_observed'});},
postSealNoReplay:async(path,body,options)=>{wire={path,body,options};throw new Error('wire captured before transport');}};
(async()=>{try {await sealPending({api,store,workOrderId:marker.work_order_id,shipmentId:marker.shipment_id,
personId:marker.person_id,authorize:async()=>true,confirm:async()=>true});}catch(error){if(!wire)throw error;}
if(cleared)throw new Error('request cleared before exact readback');process.stdout.write(JSON.stringify({wire,marker}));})();"""
    shipment=db.get(StockOperationShipment,accepted.receipt.shipment_id)
    args=dict(work_order_id=str(db.get(StockOperationOrder,shipment.operation_id).oam_work_order_id),
        shipment_id=str(accepted.receipt.shipment_id),receipt_id=str(accepted.receipt.receipt_id),
        person_id=str(accepted.actor.person_id),authorization_version=accepted.actor.authorization_version)
    folder=Path(__file__).resolve().parents[2]/'miniprogram'
    captured=subprocess.run(['node','-e',script],input=json.dumps(args),text=True,capture_output=True,cwd=folder,timeout=30)
    assert captured.returncode==0,captured.stderr
    result=json.loads(captured.stdout);wire=result['wire'];marker=result['marker']
    assert wire['path']==prefix(accepted).removeprefix('/api')+'/by-request/'+marker['trace_request_id']+'/seal'
    assert wire['body']=={'request_hash':marker['request_hash']}
    with TestClient(application(db,accepted.actor),raise_server_exceptions=False) as client:
        response=client.post('/api'+wire['path'],json=wire['body'],headers={'X-Request-ID':wire['options']['requestId']})
        assert response.status_code==200,response.text
        original=client.get('/api'+wire['path'].removesuffix('/seal'))
        assert original.status_code==200 and original.json()==response.json(),original.text
        state=client.get(prefix(accepted));assert state.status_code==200,state.text
        assert state.json()['status']=='not_posted' and state.json()['inbound'] is None
    validate="""const fs=require('node:fs'),c=require('./utils/stock-return-inbound-contract');
const {raw,marker}=JSON.parse(fs.readFileSync(0,'utf8'));c.validateLookup(raw,marker);"""
    checked=subprocess.run(['node','-e',validate],input=json.dumps(dict(raw=original.json(),marker=marker)),
        text=True,capture_output=True,cwd=folder,timeout=30)
    assert checked.returncode==0,checked.stderr
