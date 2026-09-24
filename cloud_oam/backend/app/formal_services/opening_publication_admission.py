"""Runtime-only publication selection and new-opening admission.

No schema-owner DSN, private-table SELECT, fallback or commit. Historical
selection is immutable evidence, and deliberately does not assert freshness.
"""
from datetime import datetime
import re
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from ..opening_stocktake_schemas import OpeningControlLineIn

SQL = '''SELECT public.rsc_opening_control_selection_0125(
    :user,CAST(:version AS bigint),CAST(:region AS uuid),CAST(:publication AS uuid),
    CAST(:source AS uuid),CAST(:run AS uuid),CAST(:current AS boolean))'''
_KEYS = {'publication_id','publication_sha256','actor_person_id','authorization_version','region_org_id',
         'source_system_id','sync_run_id','sync_scope_key','captured_at','valid_until','control_lines'}


def selection(db, *, actor, region, publication=None, source=None, run=None, current=False):
    from .opening_stocktake import OpeningStocktakeError, OpeningControlLineInput
    if db.get_bind().dialect.name != 'postgresql':
        raise OpeningStocktakeError('opening_publication_database_required','service_unavailable','正式发布批次启动需要 PostgreSQL 准入校验')
    try:
        with db.no_autoflush:
            doc=db.scalar(text(SQL),dict(user=actor.user_id,version=actor.authorization_version,region=region,
                publication=publication,source=source,run=run,current=current))
        if not isinstance(doc,dict) or set(doc)!=_KEYS or doc['actor_person_id']!=str(actor.person_id) \
                or type(doc['authorization_version']) is not int or doc['authorization_version']!=actor.authorization_version \
                or doc['region_org_id']!=str(region) or not re.fullmatch('[a-f0-9]{64}',doc['publication_sha256']):
            raise ValueError('invalid selection')
        for key,expected in [('publication_id',publication),('source_system_id',source),('sync_run_id',run)]:
            actual=UUID(doc[key])
            if not actual.int or expected is not None and actual!=expected:raise ValueError('selection coordinate mismatch')
        start=datetime.fromisoformat(doc['captured_at']);end=datetime.fromisoformat(doc['valid_until'])
        if start.tzinfo is None or end.tzinfo is None or end<=start:raise ValueError('invalid time anchors')
        if not isinstance(doc['control_lines'],list):raise ValueError('invalid lines')
        lines=[];seen=set()
        for entry in doc['control_lines']:
            entry=dict(entry);digest=entry.pop('payload_sha256')
            if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('invalid digest')
            row=OpeningControlLineIn.model_validate(entry)
            if row.mapping_status!='resolved' or row.mapping_note or row.external_business_key in seen:raise ValueError('invalid full set')
            seen.add(row.external_business_key)
            lines.append(OpeningControlLineInput(**row.model_dump(),payload_sha256=digest))
        return doc,tuple(lines)
    except DBAPIError as exc:
        code=getattr(exc.orig,'sqlstate',None)
        if code=='23514':
            raise OpeningStocktakeError('control_publication_not_admissible','precondition_failed','控制批次尚未发布、已被替代或当前来源证据不可用，请重新读取') from None
        if code=='42501':
            raise OpeningStocktakeError('control_publication_forbidden','forbidden','当前身份无权使用该区域控制批次') from None
        raise OpeningStocktakeError('control_publication_unavailable','service_unavailable','控制批次准入校验暂不可用') from None
    except (ValueError,TypeError,KeyError,AttributeError):
        raise OpeningStocktakeError('control_publication_invalid','service_unavailable','控制批次证据结构不完整') from None


def require_new_opening(db, *, actor, command):
    # Existing SQLite domain tests remain a nonproduction algorithm reference.
    # PostgreSQL cannot skip this path; its ALWAYS insertion/commit guards also
    # reject direct SQL and legacy callers that omit application validation.
    if db.get_bind().dialect.name=='sqlite':return
    from .opening_stocktake import OpeningStocktakeError
    doc,lines=selection(db,actor=actor,region=command.region_org_id,source=command.control_source_system_id,
                        run=command.control_sync_run_id,current=True)
    if doc['sync_scope_key']!=command.control_sync_scope_key or sorted(lines,key=lambda r:r.external_business_key)!=sorted(command.control_lines,key=lambda r:r.external_business_key):
        raise OpeningStocktakeError('control_publication_lines_mismatch','precondition_failed','控制明细必须与服务端已发布的完整批次一致')
