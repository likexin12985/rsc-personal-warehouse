"""Real PG16 deferred posting-edge checks, using explicit component parents."""
import hashlib
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.return_condition_guards import statements
from pg16_return_condition_invariants_gate import snapshot
from return_condition_posting_fixture import PostingSource, posting_schema


def run(owner, api):
    meta = posting_schema()
    meta.create_all(owner)
    ddl = statements(posting=True)
    with owner.begin() as db:
        for sql in ddl:
            db.execute(text(sql))
        for name in meta.tables:
            db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON public.'+name+' TO star_oam_api')
    rejected, positive = [], []

    def source(mode):
        s = PostingSource(meta,mode)
        with owner.begin() as db:
            s.seed(db)
        return s

    def refuse(name, action, *, message, at_commit=True, engine=None, sqlstate='23514'):
        before = snapshot(owner,meta)
        done = False
        try:
            with (engine or api).begin() as db:
                action(db)
                done = True
        except DBAPIError as error:
            assert error.orig.sqlstate==sqlstate, (name,str(error.orig))
            assert done==at_commit, (name,str(error.orig),'wrong failure phase')
            assert message in str(error.orig), (name,str(error.orig))
            assert snapshot(owner,meta)==before, name+': rollback changed facts'
            rejected.append(dict(name=name,sqlstate=sqlstate,atCommit=done,allRowsPreserved=True))
        else:
            raise AssertionError(name+': invalid posting committed')

    def update(db, name, identifier, **values):
        table=meta.tables[name]
        db.execute(table.update().where(table.c.id==identifier).values(**values))

    for mode in ('none','lot','serial','lot_and_serial'):
        s = source(mode)
        with api.begin() as db:
            initial=s.claim(db)
            withdrawn=s.action(db,initial,'withdraw','cancelled_pending_release')
            s.action(db,withdrawn,'release','released_cancelled')
            again=s.claim(db,seq=4,event_first=True)
            regional=s.action(db,again,'verify_region','awaiting_headquarters',actor='region')
            approved=s.action(db,regional,'approve_hq','approved',actor='hq')
            s.action(db,approved,'execute','executed')
        positive.append(mode+':release_reclaim_independent_review_execute')
        s = source(mode)
        tx_changes = [dict(movement_type='transfer'),dict(source_document_type='unrelated'),
            dict(source_document_id=str(uuid4())),dict(actor_user_id=s.actors['other'][0]),
            dict(status='draft'),dict(ledger_cursor=1),dict(reversed_transaction_id=s.ids['original_tx']),
            dict(effective_at=s.origin_time),dict(posted_at=s.origin_time)]
        for change in tx_changes:
            def tamper(db, values=change):
                e=s.claim(db)
                update(db,'inventory_transactions',e['posting_transaction_id'],**values)
            refuse(mode+':tx_'+next(iter(change)),tamper,
                message='ledger order mismatch' if 'ledger_cursor' in change else 'single posting edge')
        for change in (dict(line_no=2),dict(external_boundary_code='fake'),dict(created_at=s.origin_time)):
            def tamper(db,values=change):
                e=s.claim(db)
                update(db,'inventory_movements',e['posting_movement_id'],**values)
            refuse(mode+':movement_'+next(iter(change)),tamper,message='single posting edge')
        for key in ('owner_org_id','custodian_person_id','location_id','material_id','lot_id'):
            def tamper(db,field=key):
                s.claim(db)
                update(db,'stock_accounts',s.ids['frozen'],**{field:uuid4()})
            refuse(mode+':frozen_'+key,tamper,message='account dimensions mismatch')
        for change in (dict(condition_code='used'),dict(availability_bucket='reserved')):
            def tamper(db,values=change):
                s.claim(db)
                update(db,'stock_accounts',s.ids['source'],**values)
            refuse(mode+':source_'+next(iter(change)),tamper,message='account dimensions mismatch')
        for change in (dict(quantity=2),dict(line_no=2),dict(to_account_id=s.ids['frozen'])):
            def tamper(db,values=change):
                s.claim(db)
                update(db,'inventory_movements',s.ids['original_move'],**values)
            refuse(mode+':original_'+next(iter(change)),tamper,message='original movement')
        # Commit a claim once, then attack from parent tables only. There is no
        # new business event whose trigger could accidentally save the check.
        with api.begin() as db:
            initial=s.claim(db)
        def extra_movement(db):
            original=db.execute(select(meta.tables['inventory_movements']).where(
                meta.tables['inventory_movements'].c.id==initial['posting_movement_id'])).mappings().one()
            db.execute(meta.tables['inventory_movements'].insert(),dict(original)|dict(id=uuid4(),line_no=2))
        refuse(mode+':late_extra_movement',extra_movement,message='single posting edge')
        refuse(mode+':late_actor_change',lambda db: update(db,'inventory_transactions',initial['posting_transaction_id'],
            actor_user_id=s.actors['other'][0]),message='single posting edge')
        refuse(mode+':late_frozen_dimension',lambda db: update(db,'stock_accounts',s.ids['frozen'],
            owner_org_id=uuid4()),message='account dimensions mismatch')
        def orphan(db):
            row=db.execute(select(meta.tables['inventory_transactions']).where(
                meta.tables['inventory_transactions'].c.id==initial['posting_transaction_id'])).mappings().one()
            db.execute(meta.tables['inventory_transactions'].insert(),dict(row)|dict(id=uuid4(),ledger_cursor=999))
        refuse(mode+':orphan_tagged_transaction',orphan,message='requires exact event')
        def proof_then_mutate(db):
            db.execute(text('SELECT public.rsc_condition_check_posting(:id)'),{'id':initial['case_id']})
            update(db,'inventory_transactions',initial['posting_transaction_id'],actor_user_id=s.actors['other'][0])
        refuse(mode+':prior_proof_cannot_survive_later_write',proof_then_mutate,engine=owner,message='single posting edge')
        refuse(mode+':api_cannot_call_private_proof',lambda db: db.execute(
            text('SELECT public.rsc_condition_check_posting(:id)'),{'id':initial['case_id']}),
            at_commit=False,sqlstate='42501',message='permission denied')
        with api.begin() as db:
            regional=s.action(db,initial,'verify_region','awaiting_headquarters',actor='region')
            approved=s.action(db,regional,'approve_hq','approved',actor='hq')
        for change in (dict(condition_code='new'),dict(availability_bucket='frozen'),dict(owner_org_id=uuid4()),
                       dict(custodian_person_id=s.actors['other'][1]),dict(material_id=uuid4()),dict(lot_id=uuid4())):
            def wrong_target(db,values=change):
                s.action(db,approved,'execute','executed')
                update(db,'stock_accounts',s.ids['damaged'],**values)
            refuse(mode+':target_'+next(iter(change)),wrong_target,message='single posting edge')
        if s.tracked:
            def missing_sn(db):
                t=meta.tables['inventory_movement_serials']
                db.execute(t.delete().where(t.c.movement_id==initial['posting_movement_id']))
            refuse(mode+':late_missing_sn',missing_sn,message='serial set mismatch')
            def extra_sn(db):
                db.execute(meta.tables['inventory_movement_serials'].insert(),dict(
                    movement_id=initial['posting_movement_id'],transaction_id=initial['posting_transaction_id'],
                    serial_id=s.serials[1],created_at=initial['created_at']))
            refuse(mode+':late_extra_sn',extra_sn,message='serial set mismatch')
            refuse(mode+':serial_material',lambda db:
                update(db,'inventory_serials',s.serials[0],material_id=uuid4()),message='serial material or lot mismatch')
        # Insert an unrelated movement earlier in the ledger after the claim
        # already committed. This forces reverse checking of source/SN history.
        def interposed(db):
            txid,moveid=uuid4(),uuid4()
            tx=dict(s.transactions[initial['posting_transaction_id']])|dict(id=txid,ledger_cursor=2,
                source_document_type='unrelated',source_document_id=str(uuid4()),posting_key='unrelated:'+str(txid))
            db.execute(meta.tables['inventory_transactions'].insert(),tx)
            db.execute(meta.tables['inventory_movements'].insert(),dict(id=moveid,transaction_id=txid,line_no=1,
                from_account_id=s.ids['source'],to_account_id=s.ids['frozen'],quantity=1,
                external_boundary_code=None,created_at=initial['created_at']))
            if s.tracked:
                db.execute(meta.tables['inventory_movement_serials'].insert(),dict(movement_id=moveid,
                    transaction_id=txid,serial_id=s.serials[0],created_at=initial['created_at']))
        refuse(mode+':interposed_outgoing',interposed,
            message='previous movement mismatch' if s.tracked else 'later outgoing requires reconciliation')
    return dict(passed=True,rejected=rejected,positive=positive,
        ddlSha256=hashlib.sha256('\n'.join(ddl).encode()).hexdigest(),
        externalParents='minimal inventory and acceptance rows, not complete old business guards',
        realApiRoleSql=True,reverseParentTriggersTested=True,completeHistoricalProof=False,
        authorityAuditOutboxRequestProof=False,formalMigration=False,productionAcceptance=False)
