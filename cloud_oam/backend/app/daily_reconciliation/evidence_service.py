"""Daily evidence access follows every historical reference's current region."""
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select,text,bindparam,Uuid
from ..formal_services.formal_files import _fail,_require_uploader_continuity
from ..foundation_models import Organization
from . import review_core as core
from .query_service import REPORTS,read_scope

PURPOSE='daily_reconciliation_evidence'

def require_upload_permission(db,actor):
    now=db.scalar(text('SELECT clock_timestamp()')) if db.bind.dialect.name=='postgresql' else datetime.now(timezone.utc)
    regions=set()
    for grant in actor.assignments:
        if grant.role_code=='provincial_manager' and grant.scope_type=='organization':
            try:regions.add(UUID(grant.scope_id))
            except (TypeError,ValueError):continue
    for region in regions:
        target=db.get(Organization,region,populate_existing=True)
        if target is None or target.status!='active' or target.org_type!='region_company':continue
        try:core.authorize(actor,SimpleNamespace(region_org_id=region),'explain',now)
        except core.ReviewError:continue
        return
    _fail('file_purpose_forbidden','forbidden','当前账号不能上传日终对账证据')

def authorize_download(db,*,actor,row):
    now=db.scalar(text('SELECT clock_timestamp()')) if db.bind.dialect.name=='postgresql' else datetime.now(timezone.utc)
    try:scope=read_scope(actor,now)
    except Exception:_fail('file_download_forbidden','forbidden','当前账号不能下载该对账证据')
    if db.bind.dialect.name=='postgresql':
        sql="SELECT DISTINCT e.cutoff_id FROM daily_review_events e WHERE e.payload_jsonb->'evidence' @> CAST(:evidence AS jsonb) LIMIT 101"
        import json
        params={'evidence':json.dumps([{'file_id':str(row.id)}])}
    else:
        sql="SELECT DISTINCT e.cutoff_id FROM daily_review_events e,json_each(e.payload_jsonb,'$.evidence') f WHERE json_extract(f.value,'$.file_id')=:file LIMIT 101"
        params={'file':str(row.id)}
    ids=tuple(UUID(str(x)) for x in db.scalars(text(sql),params))
    if len(ids)>100:_fail('file_binding_invalid','conflict','证据关联范围需要人工核验')
    if not ids:
        if row.uploaded_by!=actor.user_id:_fail('file_download_forbidden','forbidden','当前账号不能下载该对账证据')
        _require_uploader_continuity(row,row.metadata_jsonb,actor);require_upload_permission(db,actor)
        return {'binding_type':PURPOSE,'binding_count':0,'access':'uploader_current_authority'}
    rows=db.execute(select(REPORTS.c.cutoff_id,REPORTS.c.region_org_id).where(REPORTS.c.cutoff_id.in_(ids))).all()
    if len(rows)!=len(ids) or any(scope is not None and region not in scope for _,region in rows):
        _fail('file_download_forbidden','forbidden','当前账号不能下载该对账证据')
    return {'binding_type':PURPOSE,'binding_count':len(ids)}
