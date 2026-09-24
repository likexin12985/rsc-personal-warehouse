"""Real local PG16 checks using only the factory's freshly owned engines."""
from source_configuration_file_fixtures import source_evidence
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select,text
from sqlalchemy.orm import Session

from app import inventory_control_authority as authority,material_source_authority as service
from app import material_capture_ingress as ingress
from app.foundation_models import AuditChainHead,FileObject,Role,SourceSystem
from app.formal_access import load_formal_principal
from app.material_capture_models import MaterialCaptureBinding as Binding
from app.models import AuthSession
from app.security import create_access_token
from app.database_security import validate_production_database_security
from app.edge_database_security import verify_edge_database_boundary
from app.formal_services import formal_files
from app.material_source_authority_models import MaterialSourceAuthorityDecision
from pg16_material_projection_gate import assert_material_projection_gate,snapshot
from pg16_material_source_proof_gate import assert_material_source_proof_gate
from pg16_inventory_control_normalization_gate import assert_inventory_control_normalization_gate
from test_formal_access import make_organization,make_user,assign
from test_material_source_authority import command
from test_material_capture_ingress import SOURCE,KEY,SECRET
from pg16_release_gate_diagnostics import run_with_sanitized_database_diagnostics


def _prepare(owner):
    with Session(owner) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM public.material_projection_publications)'))
        org=make_organization(db,name='Synthetic native PG16 HQ')
        user,_=make_user(db,org,name='Synthetic native material reviewer')
        role=db.scalars(select(Role).where(Role.code=='admin')).one()
        now=authority._now(db)
        assign(db,user,role,scope_type='national',scope_id='*',valid_from=now)
        source=SourceSystem(code='oam',name='Synthetic native OAM source',mode='read_only',enabled=True)
        db.add(source);db.flush()
        now=authority._now(db)
        binding=Binding(source_system_id=source.id,source_instance=SOURCE,key_id=KEY+'-rotated',
            key_fingerprint=ingress.key_fingerprint(SECRET,SOURCE),created_at=now,valid_from=now,valid_to=now+timedelta(hours=1))
        db.add(binding);db.flush()
        db.commit()
        file, _ = source_evidence(db, user.id)
        session=AuthSession(user_id=user.id,refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',device_id=uuid4().hex,
            ip_address='hmac:1:'+'a'*64,created_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
        db.add_all([file,session])
        if db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key=='authorization')) is None:db.add(AuditChainHead(stream_key='authorization',version=0))
        db.commit()
        actor=load_formal_principal(db,user.id)
        world=SimpleNamespace(actor=actor,file=file.id,binding=binding.id,source=source.id)
        login=dict(access_token=create_access_token(user.id,session.id),expected_authorization_version=actor.authorization_version)
        cmd=command(db,world)
        review=service.preview_material_source_authority(db,**login,command=cmd)
        grant=service.execute_material_source_authority(db,**login,command=cmd,review_sha256=review['review_sha256']);db.commit()
        # The common integration checker independently grants current authority;
        # retain this initial source decision and its explicit revocation.
        cmd=command(db,world,action='revoke',valid_to=None,revoked_grant_id=grant['decision_id'],expected_subject_sha256=grant['payload_sha256'])
        review=service.preview_material_source_authority(db,**login,command=cmd)
        service.execute_material_source_authority(db,**login,command=cmd,review_sha256=review['review_sha256']);db.commit()


def run(engines):
    owner=engines['star_oam_migrator'];api=engines['star_oam_api'];edge=engines['edge_inbox']
    projector=engines['star_oam_projector'];backup=engines['star_oam_backup']
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version'))=='20261106_0127'
    run_with_sanitized_database_diagnostics(owner,lambda:_prepare(owner),replace_unlinked_database_failure_when=None)
    # Exercise the real restricted API role, not just the schema owner, for
    # uploads and downloads. The adapter materializes synthetic HEAD evidence.
    with Session(owner) as db:
        actor_id=db.scalars(select(MaterialSourceAuthorityDecision.actor_user_id)).first()
    with Session(api) as db:
        file,storage=source_evidence(db,actor_id)
        actor=load_formal_principal(db,actor_id)
        assert formal_files.create_file_download_intent(db,actor=actor,file_id=file.id,
            trace_request_id=uuid4().hex,storage=storage,download_ttl_seconds=60).file_id==file.id
        db.commit()
    run_with_sanitized_database_diagnostics(owner,
        lambda:assert_material_projection_gate(owner,api,edge,projector,backup),replace_unlinked_database_failure_when=None)
    run_with_sanitized_database_diagnostics(owner,
        lambda:assert_material_source_proof_gate(owner,api,edge,projector,backup),replace_unlinked_database_failure_when=None)
    controls=run_with_sanitized_database_diagnostics(owner,
        lambda:assert_inventory_control_normalization_gate(owner,edge,api,projector,backup,publication_check=True),replace_unlinked_database_failure_when=None)
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    try:
        verify_edge_database_boundary(edge)
    except Exception:
        from app.oam_sync_scope_security import read_oam_sync_scope_boundary
        with edge.connect() as db:
            scope=read_oam_sync_scope_boundary(db,expected_role='edge_inbox',expected_migration_role='star_oam_migrator')
        print('Edge RLS failed check names: '+str(scope.get('boundary_failures') if scope else 'missing_evidence'),flush=True)
        raise
    with owner.connect() as revision_connection:
        migration_head=revision_connection.scalar(text('SELECT version_num FROM alembic_version'))
    return dict(status='passed',scope='local-native-pg16-opening-actor-commit',migrationHead=migration_head,
        nonzeroPg16InventoryAndMaterialIntegration=True,controlObservations=controls,
        actualPostgreSQL16=True,githubReleaseGate=False,publicationCount=len(snapshot(owner)[0]),
        checked=['0126 immutable actor version, role expiry and scheduled deny at commit, private capability and drift refusal','migration to head as schema owner','empty 0122/0123/0124/0125/0126 downgrade/upgrade','API file upload/completion/download',
                 '0125 server-owned start, raw admission, current evidence locks, commit expiry rollback, read-only recovery and private-role/migration drift refusal',
                 '0124 restricted API batch summaries, exact region and global deny, no private table access, migration drift refusal',
                 'current HQ source grant and revocation','signed edge capture','production API startup security validation',
                 'concurrent exact publication replay','immutable A/B/A version history','current API catalogue',
                 'graph SQL rejection','API/edge/projector privilege denial','exact source and policy locks','backup snapshot',
                 'current source proof bound to exact source system and collector instance',
                 'current proof owner and READ COMMITTED boundary',
                 'current proof blocks same-SKU publication and mutable evidence changes',
                 'signed inventory full/incremental/zero with approved mapping and current material proof',
                 'combined normalization locks and evidence revocation/file refusal; unchanged inventory transactions and balances',
                 'authenticated control projection plans with exact immutable origin and material line pointers; no writes',
                 'actual control publication full/delta/zero/reappearance, concurrent exact replay, opening consumer and retained history',
                 'control SQL graph refusal and private roles',
                 'actual API role concurrently starts proven-zero opening exactly once; empty unproven batch refused',
                 'original zero-opening request recovers after later control publication; no duplicate task or start outbox',
                 'existing edge deployment ACL provisioning and production edge startup validation'],
        fullReleaseGate=False,productionAcceptance=False)
