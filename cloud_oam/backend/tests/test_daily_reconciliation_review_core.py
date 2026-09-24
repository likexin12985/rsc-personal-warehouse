from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,localcontext
from uuid import UUID,uuid4
import pytest
from pydantic import ValidationError
from app.formal_access import FormalPrincipal,ScopeGrant,Entitlement
from app.daily_reconciliation import review_core as core
from app.daily_reconciliation.kernel import digest

NOW=datetime(2026,9,21,6,tzinfo=timezone.utc)
REGION=uuid4()

def principal(role='provincial_manager',region=REGION,*,person=None):
    scope,scope_id=('national','*') if role=='admin' else ('organization',str(region))
    g=ScopeGrant(uuid4(),role,scope,scope_id,NOW-timedelta(days=1),NOW+timedelta(days=1))
    entitlements=tuple(Entitlement(g.assignment_id,role,scope,scope_id,'reconciliation',action,'','allow') for action in ['read','create_daily','explain_daily','approve_daily'])
    return FormalPrincipal(str(uuid4()),person or uuid4(),'active','active',1,'active',(g,),entitlements)

@pytest.fixture
def snapshot():
    rows=[dict(warehouse_code='仓 A',material_id=uuid4(),condition='new',external_qty='2.000',local_qty='1.000',difference='1.000',status='difference'),
          dict(warehouse_code='仓 B',material_id=uuid4(),condition='damaged',external_qty='1.000',local_qty='2.000',difference='-1.000',status='difference'),
          dict(warehouse_code='仓 C',material_id=uuid4(),condition='used',external_qty='0.000',local_qty='0.000',difference='0.000',status='matched')]
    return core.CutoffSnapshot(cutoff_id=uuid4(),cutoff_sha256='a'*64,comparison_sha256='b'*64,source_system_id=uuid4(),region_org_id=REGION,
        business_date=NOW.date(),external_snapshot_at=NOW-timedelta(minutes=1),local_ledger_cursor=123,comparison_status='differences',items=rows)

def command(snapshot,operation='open',version=0,**extra):
    return core.REQUEST.validate_python(dict(cutoff_id=snapshot.cutoff_id,expected_cutoff_sha256=snapshot.cutoff_sha256,
        expected_comparison_sha256=snapshot.comparison_sha256,expected_version=version,idempotency_key=uuid4().hex,request_id=uuid4().hex,
        operation=operation,**extra))

def file():return core.Evidence(file_id=uuid4(),sha256='c'*64,size_bytes=10,mime_type='image/png',status='available',accessible=True)

def explain(snapshot,version=1,ordinals=(1,2),item_version=0,f=None):
    f=f or file()
    return command(snapshot,'explain',version,items=[dict(ordinal=i,expected_item_version=item_version,explanation='逐项核对历史在途证据',evidence_file_id=f.file_id,evidence_sha256=f.sha256) for i in ordinals]),f

def accepted(snapshot,history,request,actor,evidence=()):
    result=core.apply(snapshot,history,principal=actor,request=request,now=NOW,evidence=evidence)
    assert not result.replayed and not result.receipt.stock_written
    return history+(result.event,)

def opened(snapshot,actor=None):return accepted(snapshot,(),command(snapshot),actor or principal())

def explained(snapshot,actor=None):
    history=opened(snapshot);request,f=explain(snapshot)
    return accepted(snapshot,history,request,actor or principal(),(f,)),f


def test_full_explanation_review_keeps_comparison_and_stock_facts(snapshot):
    before=snapshot.model_dump_json();regional=principal();hq=principal('admin')
    history=opened(snapshot,regional)
    req,f=explain(snapshot);history=accepted(snapshot,history,req,regional,(f,))
    req=command(snapshot,'approve',2,comment='总部独立核验附件与截止差异')
    history=accepted(snapshot,history,req,hq,(f,))
    state=core.prove_history(snapshot,history)
    assert state.review_status=='approved' and state.version==3
    assert state.approved_by_person_id==hq.person_id and state.explanation_authors==(regional.person_id,)
    run,items=core.projection_plan(snapshot,history)
    assert run['status']=='approved' and run['summary_jsonb']['comparison_sha256']==snapshot.comparison_sha256
    assert [i['status'] for i in items]==['resolved','resolved','matched']
    assert [i['difference'] for i in items]==[Decimal('1'),Decimal('-1'),Decimal('0')]
    assert run['local_ledger_cursor']=='123' and snapshot.model_dump_json()==before


def test_return_targeted_items_then_correct_without_erasing_old_evidence(snapshot):
    regional=principal();history,f=explained(snapshot,regional);old=history[-1]
    history=accepted(snapshot,history,command(snapshot,'request_changes',2,comment='请补第一项的实际签收凭证',ordinals=(1,)),principal('admin'))
    state=core.prove_history(snapshot,history)
    assert state.review_status=='changes_requested' and [i.revision_requested for i in state.items]==[True,False,False]
    run,items=core.projection_plan(snapshot,history);assert [i['status'] for i in items]==['difference','explained','matched']
    with pytest.raises(core.ReviewError,match='unexplained'):
        core.apply(snapshot,history,principal=principal('admin'),request=command(snapshot,'approve',3,comment='不能绕过退回意见'),now=NOW,evidence=(f,))
    new_file=file();req,_=explain(snapshot,3,(1,),2,new_file)
    history=accepted(snapshot,history,req,regional,(new_file,))
    assert history[1]==old and core.prove_history(snapshot,history).review_status=='pending_review'
    history=accepted(snapshot,history,command(snapshot,'approve',4,comment='两项证据均核验完成'),principal('admin'),(f,new_file))
    assert core.prove_history(snapshot,history).review_status=='approved'


@pytest.mark.parametrize('role,operation',[('admin','explain'),('provincial_manager','approve'),('provincial_manager','request_changes'),('technician','open'),('star_headquarters_approver','open')])
def test_exact_roles_not_just_permission_codes(snapshot,role,operation):
    p=principal(role)
    with pytest.raises(core.ReviewError,match='forbidden'):core.authorize(p,snapshot,operation,NOW)

@pytest.mark.parametrize('change',['wrong_region','deny','field_only','grant_mismatch','expired','future','handover','suspended','left','version_zero','no_read'])
def test_live_authority_snapshot_refuses_unusable_grants(snapshot,change):
    p=principal();g=p.assignments[0]
    if change=='wrong_region':p=principal(region=uuid4())
    elif change=='deny':p=replace(p,entitlements=p.entitlements+(replace(p.entitlements[-2],effect='deny'),))
    elif change=='field_only':p=replace(p,entitlements=tuple(replace(e,field_code='quantity') for e in p.entitlements))
    elif change=='grant_mismatch':p=replace(p,entitlements=tuple(replace(e,assignment_id=uuid4()) for e in p.entitlements))
    elif change=='expired':p=replace(p,assignments=(replace(g,valid_to=NOW),))
    elif change=='future':p=replace(p,assignments=(replace(g,valid_from=NOW+timedelta(seconds=1)),))
    elif change=='handover':p=replace(p,access_mode='restricted_handover')
    elif change=='suspended':p=replace(p,account_status='suspended')
    elif change=='left':p=replace(p,employment_status='left')
    elif change=='version_zero':p=replace(p,authorization_version=0)
    else:p=replace(p,entitlements=tuple(e for e in p.entitlements if e.action!='read'))
    with pytest.raises(core.ReviewError,match='forbidden'):core.authorize(p,snapshot,'explain',NOW)


def test_same_person_cannot_review_even_after_new_user_or_replacement(snapshot):
    author=principal();history,f=explained(snapshot,author)
    for operation,extra in [('approve',{}),('request_changes',{'ordinals':(1,)})]:
        req=command(snapshot,operation,2,comment='原解释人不能审核自己',**extra)
        with pytest.raises(core.ReviewError,match='self_review'):
            core.apply(snapshot,history,principal=principal('admin',person=author.person_id),request=req,now=NOW,evidence=(f,) if operation=='approve' else ())
    # A second author replaces current explanations; original authorship remains.
    req,_=explain(snapshot,2,item_version=1,f=f);history=accepted(snapshot,history,req,principal(),(f,))
    with pytest.raises(core.ReviewError,match='self_review'):
        core.apply(snapshot,history,principal=principal('admin',person=author.person_id),request=command(snapshot,'approve',3,comment='保留历史解释人的回避约束'),now=NOW,evidence=(f,))

@pytest.mark.parametrize('change',['missing_file','wrong_sha','changed_size','changed_mime','extra_file'])
def test_approval_rechecks_latest_evidence_observations(snapshot,change):
    history,f=explained(snapshot)
    evidence={'missing_file':(), 'wrong_sha':(f.model_copy(update={'sha256':'d'*64}),),
        'changed_size':(f.model_copy(update={'size_bytes':11}),),'changed_mime':(f.model_copy(update={'mime_type':'application/pdf'}),),'extra_file':(f,file())}[change]
    with pytest.raises(core.ReviewError,match='evidence_changed'):
        core.apply(snapshot,history,principal=principal('admin'),request=command(snapshot,'approve',2,comment='核验原始附件不可被替换'),now=NOW,evidence=evidence)

@pytest.mark.parametrize('change',['matched','unknown','stale_case','stale_item','missing_file','wrong_sha','duplicate_file','invalid_file'])
def test_explanation_failure_leaves_input_history_unchanged(snapshot,change):
    history=opened(snapshot);before=digest([e.model_dump(mode='json') for e in history]);req,f=explain(snapshot,ordinals=(3,) if change=='matched' else (4,) if change=='unknown' else (1,))
    evidence=(f,)
    if change=='stale_case':req=req.model_copy(update={'expected_version':0})
    elif change=='stale_item':req=req.model_copy(update={'items':tuple(i.model_copy(update={'expected_item_version':2}) for i in req.items)})
    elif change=='missing_file':evidence=()
    elif change=='wrong_sha':evidence=(f.model_copy(update={'sha256':'d'*64}),)
    elif change=='duplicate_file':evidence=(f,f)
    elif change=='invalid_file':evidence=(f.model_copy(update={'status':'quarantined'}),)
    with pytest.raises((core.ReviewError,ValidationError)):
        core.apply(snapshot,history,principal=principal(),request=req,now=NOW,evidence=evidence)
    assert digest([e.model_dump(mode='json') for e in history])==before


def test_exact_replay_and_recovery_do_not_revalidate_historical_file_availability(snapshot):
    actor=principal();history=opened(snapshot);req,f=explain(snapshot)
    result=core.apply(snapshot,history,principal=actor,request=req,now=NOW,evidence=(f,));history+=(result.event,)
    replay=core.apply(snapshot,history,principal=actor,request=req,now=NOW+timedelta(seconds=1))
    assert replay.replayed and replay.receipt==result.receipt
    reader=replace(actor,entitlements=tuple(e for e in actor.entitlements if e.action=='read'))
    assert core.recover(snapshot,history,principal=reader,request=req,now=NOW)==result.receipt
    with pytest.raises(core.ReviewError,match='forbidden'):core.apply(snapshot,history,principal=reader,request=req,now=NOW)
    assert core.recover(snapshot,history,principal=principal(),request=req,now=NOW) is None
    for changed in [req.model_copy(update={'request_id':uuid4().hex}),req.model_copy(update={'expected_version':2})]:
        with pytest.raises(core.ReviewError,match='request_conflict'):core.recover(snapshot,history,principal=actor,request=changed,now=NOW)


def test_approved_case_is_immutable_but_exact_approval_receipt_is_recoverable(snapshot):
    history,f=explained(snapshot);hq=principal('admin');req=command(snapshot,'approve',2,comment='审核完毕保留原始差额')
    history=accepted(snapshot,history,req,hq,(f,))
    assert core.apply(snapshot,history,principal=hq,request=req,now=NOW).replayed
    changed,_=explain(snapshot,3,item_version=1,f=f)
    with pytest.raises(core.ReviewError,match='already_approved'):core.apply(snapshot,history,principal=principal(),request=changed,now=NOW,evidence=(f,))

@pytest.mark.parametrize('change',['hash','version','previous','scope','time','quantity'])
def test_history_tamper_refused_even_when_event_digest_is_recomputed(snapshot,change):
    history,f=explained(snapshot);e=history[-1]
    if change=='hash':e=e.model_copy(update={'sha256':'f'*64})
    elif change=='version':e=e.model_copy(update={'version':99})
    elif change=='previous':e=e.model_copy(update={'previous_sha256':'f'*64})
    elif change=='scope':e=e.model_copy(update={'actor':e.actor.model_copy(update={'scope_id':str(uuid4())})})
    elif change=='time':e=e.model_copy(update={'occurred_at':NOW-timedelta(seconds=1)})
    elif change=='quantity':snapshot=snapshot.model_copy(update={'items':(snapshot.items[0].model_copy(update={'difference':'2.000'}),)+snapshot.items[1:]})
    if change!='hash':e=e.model_copy(update={'sha256':core.event_digest(e)})
    with pytest.raises((core.ReviewError,ValidationError)):core.prove_history(snapshot,history[:-1]+(e,))


def test_matched_zero_report_has_separate_explicit_review(snapshot):
    value=snapshot.model_copy(update={'items':(),'comparison_status':'matched'});history=opened(value,principal('admin'))
    assert core.prove_history(value,history).review_status=='pending_review'
    history=accepted(value,history,command(value,'approve',1,comment='总部核对空覆盖与零数量'),principal('admin'))
    run,items=core.projection_plan(value,history)
    assert run['status']=='approved' and items==() and history[-1].request.expected_comparison_sha256==value.comparison_sha256


def test_quantities_do_not_depend_on_global_decimal_precision(snapshot):
    row=snapshot.items[0].model_dump();row.update(external_qty='999999999999999.999',local_qty='0.001',difference='999999999999999.998')
    with localcontext() as c:
        c.prec=4
        assert core.Comparison(**row).difference=='999999999999999.998'

@pytest.mark.parametrize('field,value',[('expected_version',True),('expected_version',1.2),('unexpected','payload'),('expected_cutoff_sha256','x')])
def test_http_command_shapes_reject_extra_or_coerced_fields(snapshot,field,value):
    request=command(snapshot).model_dump();request[field]=value
    with pytest.raises(ValidationError):core.REQUEST.validate_python(request)


def test_public_commands_never_accept_quantities_actor_or_file_observation(snapshot):
    for field,value in [('actor',principal()),('external_qty','3.000'),('evidence',()),('source_system_id',uuid4())]:
        request=command(snapshot).model_dump();request[field]=value
        with pytest.raises(ValidationError):core.REQUEST.validate_python(request)


def test_one_file_id_cannot_hide_conflicting_historical_evidence_versions(snapshot):
    actor=principal();history=opened(snapshot);req,f=explain(snapshot,ordinals=(1,))
    history=accepted(snapshot,history,req,actor,(f,))
    changed=f.model_copy(update={'sha256':'d'*64})
    req,_=explain(snapshot,2,ordinals=(2,),f=changed)
    history=accepted(snapshot,history,req,actor,(changed,))
    with pytest.raises(core.ReviewError,match='evidence_changed'):
        core.apply(snapshot,history,principal=principal('admin'),request=command(snapshot,'approve',3,comment='同一附件的历史版本必须一致'),now=NOW,evidence=(changed,))


def test_history_is_bound_to_complete_projection_source_not_just_claimed_hashes(snapshot):
    history=opened(snapshot)
    row=snapshot.items[0].model_copy(update={'external_qty':'3.000','local_qty':'2.000'})
    changed=snapshot.model_copy(update={'items':(row,)+snapshot.items[1:]})
    with pytest.raises(core.ReviewError,match='source_snapshot_changed'):core.prove_history(changed,history)
    same=snapshot.model_copy(update={'external_snapshot_at':snapshot.external_snapshot_at.astimezone(timezone(timedelta(hours=8)))})
    assert core.prove_history(same,history)==core.prove_history(snapshot,history)


def test_projection_does_not_rewrite_untouched_item_timestamps(snapshot):
    actor=principal();history=opened(snapshot);created=history[0].occurred_at
    request,f=explain(snapshot,ordinals=(1,))
    event=core.apply(snapshot,history,principal=actor,request=request,now=NOW+timedelta(seconds=1),evidence=(f,)).event
    run,items=core.projection_plan(snapshot,history+(event,))
    assert run['updated_at']==NOW+timedelta(seconds=1)
    assert [row['updated_at'] for row in items]==[NOW+timedelta(seconds=1),created,created]
