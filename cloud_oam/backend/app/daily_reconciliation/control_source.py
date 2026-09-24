"""Internal historical control reader candidate, never an API authorization.

Use only a dedicated read-only capture connection. This reconstructs preserved
publication/staging/material evidence; it does not approve warehouse mappings,
refresh source credentials, perform provider calls or write inventory.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext
from uuid import UUID
import re, time
from sqlalchemy import select, text, event
from sqlalchemy.orm import Session
from app import inventory_control_preparation as preparation
from app import inventory_control_projection as publication
from app import inventory_control_projection_plan as planner
from app import inventory_control_normalization as normalization
from app import inventory_control_authority as authority
from app import inventory_control_admission as admission
from app import inventory_control_attestation as attestation
from app import inventory_control_mapping as mapping
from app import material_projection as materials
from app.inventory_control_evidence import reconstruct_inventory_control_evidence, _canonical, _sha
from app.inventory_control_models import InventoryControlPreparation as Preparation, InventoryControlSourceBinding as Binding, InventoryControlCatalogVersion as Catalog, InventoryControlCaptureChain as Chain
from app.inventory_control_projection_models import ControlProjectionPublication as Publication, ControlProjectionLine as Line, ControlProjectionOrigin as Origin
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as AuthorityDecision
from app.inventory_control_mapping_models import InventoryControlMappingDecision as MappingDecision
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Attestation
from app.material_projection_models import MaterialProjectionPublication as MaterialPublication, MaterialProjectionLine as MaterialLine
from app.models import ExternalSyncSnapshot

from .capture_role_contract import CONTROL_ROLE as ROLE, CONTROL_TABLES as TABLES
from .capture_security import validate_capture_roles

class ControlSourceError(RuntimeError):pass
def require(ok,code):
    if not ok:raise ControlSourceError('daily_control_'+code)
def stamp(value):return preparation._aware(value).isoformat()
def identifier(value):
    require(isinstance(value,UUID) and value.int!=0,'identifier_invalid');return value

def historical_authority(db,root,chain,pub,auth,prepared):
    """Reprove authority/receipt history as of original publication creation.

    Later revocation cannot rewrite history. This does not admit a new capture
    or claim the historical source is currently authorized.
    """
    at=preparation._aware(pub.created_at)
    rows=tuple(db.scalars(select(AuthorityDecision).where(AuthorityDecision.binding_id==root.binding_id,
        AuthorityDecision.created_at<=at).order_by(AuthorityDecision.created_at,AuthorityDecision.id)))
    for row in rows:authority._prove(db,row)
    require(_sha([[str(r.id),r.payload_sha256] for r in rows])==auth['authority_cursor_sha256'],'authority_cursor_mismatch')
    revoked={r.revoked_grant_id for r in rows if r.action=='revoke'}
    captured=[];receipts=[]
    for index,evidence in enumerate(chain.evidence_jsonb['snapshots'],1):
        start=min(attestation.instant(w['started_at']) for w in evidence['warehouses'])
        end=max(attestation.instant(w['completed_at']) for w in evidence['warehouses'])
        spans,_=admission._coverage(rows,root.catalog_id,revoked,start,end)
        captured.append(dict(snapshot_id=evidence['manifest']['snapshot_id'],authorization_spans=spans))
        snapshot=db.scalars(select(ExternalSyncSnapshot).where(ExternalSyncSnapshot.source_instance==evidence['source_instance'],
            ExternalSyncSnapshot.snapshot_id==evidence['manifest']['snapshot_id'])).one()
        receipt=db.scalars(select(Attestation).where(Attestation.snapshot_ref_id==snapshot.id)).one_or_none()
        require(receipt is not None,'attestation_missing')
        body=attestation.CaptureAttestationIn.model_validate(receipt.payload_jsonb).model_dump(mode='json')
        require(attestation._snapshot_matches(snapshot,body) and receipt.source_instance==body['source_instance']
            and receipt.key_id==body['key_id'] and receipt.entity_type=='inventory'
            and receipt.request_id==body['snapshot_id']+'-capture'
            and receipt.payload_sha256==receipt.body_sha256==_sha(body)
            and re.fullmatch('[a-f0-9]{64}',receipt.key_fingerprint) is not None
            and abs(preparation._aware(receipt.created_at)-preparation._aware(receipt.signed_at))<=timedelta(minutes=5), 'attestation_invalid')
        require(body['source_binding_sha256']==prepared['source_binding_sha256'] and body['catalog_sha256']==prepared['catalog_sha256']
            and body['capture_chain_sha256']==_sha(dict(schema_version=chain.evidence_jsonb['schema_version'],snapshots=chain.evidence_jsonb['snapshots'][:index]))
            and attestation.instant(body['capture_started_at'])==start and attestation.instant(body['capture_completed_at'])==end
            and end<=preparation._aware(snapshot.snapshot_at)<=preparation._aware(receipt.created_at)<=at
            and preparation._aware(receipt.created_at)-start<=timedelta(minutes=45),'attestation_claim_invalid')
        receipts.append(dict(attestation_id=str(receipt.id),payload_sha256=receipt.payload_sha256,key_id=receipt.key_id,key_fingerprint=receipt.key_fingerprint))
    require(captured==auth['captures'] and _sha(receipts)==auth['attestation_cursor_sha256'],'capture_authority_mismatch')
    require(admission._proof_pair(admission._pair_at(rows,root.catalog_id,revoked,at))==auth['current_authority'],'publication_authority_mismatch')
    return dict(authority_cursor_sha256=auth['authority_cursor_sha256'],attestation_cursor_sha256=auth['attestation_cursor_sha256'])


def _read(db,*,publication_id,expected_hash,source_id,region_id,maximum_origins):
    require(not (db.new or db.dirty or db.deleted),'pending_writes')
    pub=db.get(Publication,publication_id,populate_existing=True)
    require(pub is not None,'publication_missing')
    require(pub.payload_sha256==expected_hash and pub.source_system_id==source_id and pub.region_org_id==region_id,'publication_coordinate_mismatch')
    require(0<=pub.origin_count<=maximum_origins and 0<=pub.record_count<=maximum_origins,'row_budget_exceeded')
    publication.prove_control_publication(db,pub)
    prepared=preparation.read_inventory_control_preparation(db,preparation_id=pub.preparation_id)
    root=db.get(Preparation,pub.preparation_id);binding=db.get(Binding,root.binding_id)
    catalog=db.get(Catalog,root.catalog_id);chain=db.get(Chain,root.capture_chain_id)
    plan=pub.payload_jsonb['review']['plan'];basis=plan['basis'];review=basis['normalization_evidence'];auth=basis['authorization_evidence']
    require(prepared['source_system_id']==source_id and prepared['region_org_id']==region_id,'preparation_scope_mismatch')
    for k,actual in [('preparation_id',str(root.id)),('binding_id',str(binding.id)),('catalog_id',str(catalog.id)),
        ('capture_chain_id',str(chain.id)),('preparation_sha256',root.control_manifest_sha256),('capture_chain_sha256',chain.capture_chain_sha256)]:
        require(plan[k]==actual,'plan_binding_mismatch')
    for key in ['lines','transport','basis']:require(plan[key+'_sha256']==_sha(plan[key]),'plan_digest_mismatch')
    require(basis['authorization_evidence_sha256']==_sha(auth) and basis['normalization_evidence_sha256']==_sha(review),'basis_digest_mismatch')
    for name in ['source_binding_sha256','catalog_sha256','capture_chain_sha256','control_manifest_sha256']:
        require(auth[name]==prepared[name],'authority_binding_mismatch')
    historical=historical_authority(db,root,chain,pub,auth,prepared)
    decision=mapping._prove(db,db.get(MappingDecision,pub.mapping_decision_id))
    mapped=review['approved_mapping'];at=preparation._aware(pub.created_at)
    require(decision.action=='grant' and decision.binding_id==root.binding_id and decision.catalog_id==root.catalog_id
        and decision.payload_sha256==mapped['decision_sha256'] and decision.rules_sha256==mapped['rules_sha256']
        and decision.rules_jsonb==review['rules']==mapped['rules'] and mapped['decision_id']==str(decision.id)
        and preparation._aware(decision.valid_from)<=at and (decision.valid_to is None or at<preparation._aware(decision.valid_to)), 'mapping_history_invalid')
    require(db.scalar(select(MappingDecision.id).where(MappingDecision.revoked_grant_id==decision.id,MappingDecision.created_at<=at)) is None,'mapping_revoked_at_publication')
    report,expected,records=reconstruct_inventory_control_evidence(expected_json=_canonical(dict(schema_version='rsc.inventory_control_coverage.v1',
        binding=binding.binding_jsonb,**catalog.catalog_jsonb)),evidence_json=_canonical(chain.evidence_jsonb),checked_at=preparation._aware(root.checked_at))
    require(len(records)<=maximum_origins,'feed_row_budget_exceeded')
    targets={(w.warehouse_code,p.position_code) for w in expected.warehouses for p in w.positions if p.region_code==expected.target_region_code}
    normalized=normalization.normalize_records(records=records,target_positions=targets,region_org_id=region_id,
        masters=review['master_observations'],rules=decision.rules_jsonb)
    require(all(normalized[k]==review[k] for k in normalized),'normalization_history_mismatch')
    require(normalized['normalization_complete'] and not normalized['blocked_record_count'],'normalization_blocked')
    latest,transport=planner._latest_origins(db,root,chain)
    require(transport==plan['transport'],'transport_history_mismatch')
    targets_by_key={key:(raw,staged,link) for key,(raw,staged,link) in latest.items() if (raw['data']['warehouseCode'],raw['data']['positionCode']) in targets}
    rows=db.scalars(select(Origin).where(Origin.publication_id==pub.id).order_by(Origin.external_business_key)).all()
    require(len(rows)==pub.origin_count==report.target_record_count==plan['target_record_count']
        and {r.external_business_key for r in rows}==set(targets_by_key),'origin_set_incomplete')
    line_map={r.id:r for r in db.scalars(select(Line).where(Line.publication_id==pub.id))}
    normalized_rows={r['external_business_key']:r for r in normalized['rows']}
    output=[];material_proofs={};sums={}
    with localcontext() as context:
        context.prec=40
        for row in rows:
            origin=row.payload_jsonb['origin'];raw,staged,link=targets_by_key[row.external_business_key]
            line=line_map[row.line_id];norm=normalized_rows[row.external_business_key]
            require(row.staging_record_id==staged.id and row.capture_snapshot_id==link.id
                and origin['raw_record_sha256']==_sha(raw) and origin['warehouse_code']==raw['data']['warehouseCode']
                and origin['position_code']==raw['data']['positionCode'] and origin['control_qty']==norm['control_qty']
                and origin['locked_qty']==norm['locked_qty'] and str(line.material_id)==norm['material_id']
                and line.condition_code==norm['condition_code'],'origin_value_mismatch')
            material=db.get(MaterialLine,row.material_line_id)
            require(material is not None and material.sku_code==raw['data']['materialCode']
                and material.material_id==line.material_id and str(material.publication_id)==origin['material_publication_id']
                and material.payload_sha256==origin['material_line_sha256'],'material_origin_mismatch')
            if material.publication_id not in material_proofs:
                material_pub=materials._prove(db,db.get(MaterialPublication,material.publication_id))
                material_proofs[material.publication_id]=material_pub.payload_sha256
            qty=Decimal(origin['control_qty']);sums[line.id]=sums.get(line.id,Decimal(0))+qty
            output.append(dict(origin_id=str(row.id),source_business_key=row.external_business_key,warehouse_code=origin['warehouse_code'],
                position_code=origin['position_code'],material_id=str(line.material_id),condition=line.condition_code,
                quantity=format(qty,'.3f'),locked_quantity=format(Decimal(origin['locked_qty']),'.3f'),origin_sha256=row.payload_sha256))
        require(all(sums.get(line.id,Decimal(0))==line.control_qty for line in line_map.values()),'warehouse_sum_mismatch')
    last=chain.evidence_jsonb['snapshots'][-1]
    require(stamp(pub.captured_at)==stamp(attestation.instant(last['manifest']['snapshot_at'])),'capture_time_mismatch')
    coverage=[]
    for warehouse in expected.warehouses:
        positions=sorted(p.position_code for p in warehouse.positions if p.region_code==expected.target_region_code)
        capture=next(w for w in last['warehouses'] if w['query']['warehouseCode']==warehouse.warehouse_code)
        coverage.append(dict(warehouse_code=warehouse.warehouse_code,target_positions=positions,
            source_total=capture['pages'][0]['source_total'],page_count=len(capture['pages']),
            started_at=capture['started_at'],completed_at=capture['completed_at'],capture_sha256=_sha(capture)))
    return dict(schema='rsc.daily_published_control_capture_candidate.v1',publication_id=str(pub.id),publication_sha256=pub.payload_sha256,
        source_system_id=str(source_id),region_id=str(region_id),captured_at=stamp(pub.captured_at),published_at=stamp(pub.created_at),
        publication_valid_until=stamp(pub.valid_until),catalog_sha256=catalog.catalog_sha256,origin_count=len(output),rows=output,
        covered_warehouses=sorted({w for w,p in targets}),coverage=coverage,preparation_sha256=root.control_manifest_sha256,
        capture_chain_sha256=chain.capture_chain_sha256,material_publications=[dict(id=str(k),sha256=v) for k,v in sorted(material_proofs.items())],
        historical_authority=historical,historical_graph_verified=True,operator_authority_verified=False,current_source_authorized=False,
        key_lifecycle_verified=False,warehouse_mapping_approved=False,persisted=False,stock_written=False)


def capture_published_control(engine,*,publication_id,expected_hash,source_id,region_id,maximum_origins,maximum_seconds=10):
    for value in (publication_id,source_id,region_id):identifier(value)
    require(type(expected_hash) is str and re.fullmatch('[a-f0-9]{64}',expected_hash) is not None,'hash_invalid')
    require(type(maximum_origins) is int and 0<=maximum_origins<=100000,'row_budget_invalid')
    require(type(maximum_seconds) is int and 1<=maximum_seconds<=60,'time_budget_invalid')
    require(engine.dialect.name=='postgresql','postgresql_required');started=time.monotonic()
    with engine.connect() as connection,connection.begin():
        def deadline(conn,cursor,statement,parameters,context,many):
            remaining=int((maximum_seconds-(time.monotonic()-started))*1000)
            require(remaining>0,'deadline_exceeded')
            # Integer-only utility command; no snapshot or recursive SQLAlchemy event.
            cursor.execute('SET LOCAL statement_timeout = '+str(remaining))
        event.listen(connection,'before_cursor_execute',deadline)
        connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.exec_driver_sql('SET LOCAL statement_timeout = '+str(maximum_seconds*1000))
        connection.exec_driver_sql('SET LOCAL lock_timeout = 500')
        connection.exec_driver_sql('SET LOCAL idle_in_transaction_session_timeout = '+str(maximum_seconds*1000))
        connection.exec_driver_sql('LOCK TABLE '+','.join('public.'+n for n in TABLES)+' IN ACCESS SHARE MODE')
        observed=connection.execute(text("SELECT current_user AS role,session_user AS session_role,current_setting('server_version_num')::int AS version,pg_current_snapshot()::text AS snapshot,clock_timestamp() AS observed_at")).mappings().one()
        require(observed['role']==observed['session_role']==ROLE and 160000<=observed['version']<170000,'role_or_version_invalid')
        role=connection.execute(text("SELECT rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS memberships FROM pg_roles r WHERE rolname=current_user")).one()
        require(not any(role),'privileged_role')
        checks=connection.execute(text("SELECT c.relname,c.relkind,c.relrowsecurity,pg_has_role(current_user,c.relowner,'USAGE') AS owner,has_table_privilege(current_user,c.oid,'SELECT') AS readable,has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS writable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(:tables)"),dict(tables=list(TABLES))).mappings().all()
        require(len(checks)==len(TABLES) and all(r['relkind']=='r' and r['readable'] and not r['owner'] and not r['writable'] for r in checks),'table_privileges_invalid')
        policies=connection.execute(text("SELECT c.relname,p.polname,p.polcmd,p.polpermissive,pg_get_expr(p.polqual,p.polrelid) AS expression,p.polwithcheck IS NULL AS no_check,p.polroles=ARRAY[r.oid]::oid[] AS only_role,(0=ANY(p.polroles) OR r.oid=ANY(p.polroles)) AS applicable FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_roles r WHERE n.nspname='public' AND c.relname=ANY(:tables) AND r.rolname=current_user AND p.polcmd IN ('r','*')"),dict(tables=list(TABLES))).mappings().all()
        for table in checks:
            if table['relrowsecurity']:
                chosen=[p for p in policies if p['relname']==table['relname']]
                require(all(p['polpermissive'] for p in chosen),'restrictive_policy')
                active=[p for p in chosen if p['applicable']]
                require(len(active)==1 and all(p['polcmd']=='r' and p['polname']=='rsc_control_capture_select' and p['only_role'] and p['expression']=='true' and p['no_check'] for p in active),'full_read_policy_missing')
        validate_capture_roles(connection)
        with Session(bind=connection,autoflush=False) as db:
            result=_read(db,publication_id=publication_id,expected_hash=expected_hash,source_id=source_id,region_id=region_id,maximum_origins=maximum_origins)
            require(not (db.new or db.dirty or db.deleted),'unexpected_writes')
        require(time.monotonic()-started<=maximum_seconds,'deadline_exceeded')
        result['database_observation']=dict(snapshot=observed['snapshot'],observed_at=stamp(observed['observed_at']),role=ROLE,read_only=True,repeatable_read=True)
        result['content_sha256']=_sha(result)
    return result
