"""Real DB claim queries, registry parsing and AES; synthetic provider transports."""
import base64
import hashlib
import json
import uuid
import pytest
from sqlalchemy import event
from app.config import Settings
from app.foundation_models import KmsDataKeyPin
from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
from app.openbao_transit_candidate import OpenBaoKeyCoordinate, OpenBaoDecryptResponse, context_b64, associated_data_b64
from app.production_adapters import AliyunKmsEnvelopeKeyLoader, KmsDecryptResult, ProductionAdapterConfigurationError, REGISTRY_SCHEMA, _encryption_context
from app.openbao_registry_candidate import REGISTRY_SCHEMA as BAO_SCHEMA
from app.formal_services.authentication_idempotency import AuthenticationEncryptionKeyUnavailable
from app.formal_services.material_request_contact import protect_material_request_contact, reveal_material_request_contact
from app.kms_readiness import KmsReadinessGate
import app.production_key_runtime as wiring
from test_persisted_key_references import db, NOW, AUTH, CONTACT, add_auth, add_contact, contact_v1, contact_v2

INSTANCE='runtime-test'


@pytest.fixture
def harness(db,tmp_path,monkeypatch):
    root=tmp_path.resolve();root.chmod(0o700)
    bao,ali,calls=[],[],[]
    class Transport:
        fail=False
        def decrypt(self,*,request,timeout_seconds):
            calls.append(('bao',request))
            if self.fail:raise RuntimeError('synthetic private transport detail')
            return OpenBaoDecryptResponse(200,{'data':{'plaintext':base64.b64encode(b'B'*32).decode()}})
    transport=Transport()
    monkeypatch.setattr(wiring,'OpenBaoUnixDecryptTransport',lambda **kw:transport)
    def seed(purpose,version,provider,*,key_id=None):
        if provider=='aliyun_kms':
            blob=base64.b64encode(f'{purpose}:{version}'.encode()*2).decode()
            entry=dict(purpose=purpose,kms_key_id=key_id or f'kms/{purpose}/v{version}',application_key_version=version,kms_key_version_id=f'version-{version:08d}',ciphertext_blob=blob)
            entry["encryption_context"]=_encryption_context(purpose,entry["kms_key_id"],version)
            ali.append(entry)
            pin={k:entry[k] for k in ('purpose','kms_key_id','application_key_version','kms_key_version_id')}
            pin.update(ciphertext_sha256=hashlib.sha256(blob.encode()).hexdigest(),created_at=NOW)
            db.execute(KmsDataKeyPin.__table__.insert(),pin)
        else:
            coord=OpenBaoKeyCoordinate(purpose,'test',INSTANCE,version)
            blob='vault:v2:'+base64.b64encode(hashlib.sha512(f'{purpose}:{version}'.encode()).digest()[:60]).decode()
            entry=dict(purpose=purpose,environment='test',provider_instance_id=INSTANCE,application_key_version=version,key_path=coord.key_path,transit_key_version=2,ciphertext=blob,context_b64=context_b64(coord),associated_data_b64=associated_data_b64(coord))
            bao.append(entry)
            pin={k:entry[k] for k in ('purpose','environment','provider_instance_id','application_key_version','key_path','transit_key_version')}
            pin.update(ciphertext_sha256=hashlib.sha256(blob.encode()).hexdigest(),context_sha256=hashlib.sha256(base64.b64decode(entry['context_b64'])).hexdigest(),associated_data_sha256=hashlib.sha256(base64.b64decode(entry['associated_data_b64'])).hexdigest(),created_at=NOW)
            db.execute(OpenBaoDataKeyPin.__table__.insert(),pin)
        db.execute(ApplicationKeyVersionClaim.__table__.insert(),dict(purpose=purpose,application_key_version=version,provider=provider,ciphertext_sha256=pin['ciphertext_sha256'],created_at=NOW))
    def build(*,auth='openbao_transit_v1',contact='disabled',av=8,cv=8):
        bp=root/'bao.json';bp.write_text(json.dumps(dict(schema=BAO_SCHEMA,provider='openbao_transit_v1',entries=bao)));bp.chmod(0o600)
        ap=root/'ali.json';ap.write_text(json.dumps(dict(schema=REGISTRY_SCHEMA,entries=ali)));ap.chmod(0o600)
        class Client:
            def decrypt(self,*,ciphertext_blob,encryption_context):
                calls.append(('ali',encryption_context))
                entry=next(e for e in ali if e['ciphertext_blob']==ciphertext_blob)
                return KmsDecryptResult(base64.b64encode(b'A'*32).decode(),entry['kms_key_id'],entry['kms_key_version_id'])
        import app.production_adapters as adapters
        monkeypatch.setattr(adapters,'get_configured_kms_loader',lambda _s:AliyunKmsEnvelopeKeyLoader(endpoint='kms.cn-hangzhou.aliyuncs.com',region='cn-hangzhou',registry_path=str(ap),client_factory=lambda **kw:Client()))
        settings=Settings(_env_file=None,environment='test',database_url='sqlite+pysqlite:///:memory:',auth_idempotency_encryption_provider=auth,auth_idempotency_encryption_key_version=av,auth_idempotency_kms_key_id=f'kms/{AUTH}/v{av}',material_request_contact_encryption_provider=contact,material_request_contact_encryption_key_version=cv,material_request_contact_kms_key_id=f'kms/{CONTACT}/v{cv}',openbao_provider_instance_id=INSTANCE,openbao_encrypted_data_key_registry_path=str(bp),openbao_socket_path='/run/bao/bao.sock',openbao_token_file='/run/token/token',openbao_api_uid=1001,openbao_bao_uid=1002,openbao_bao_gid=1003,openbao_shared_gid=1004,openbao_token_projector_uid=1005)
        return settings,wiring.build_production_key_runtime(db,settings,now=NOW)
    return seed,build,transport,calls,bao


def test_mixed_runtime_real_crypto_request_scopes_and_no_request_sql(db,harness):
    seed,build,_,calls,_=harness
    for purpose in (AUTH,CONTACT):
        seed(purpose,7,'aliyun_kms');seed(purpose,8,'openbao_transit_v1')
    add_auth(db,7);add_contact(db,contact_v1(7,kms_key_id=f'kms/{CONTACT}/v7'),historical=True)
    settings,runtime=build(contact='openbao_transit_v1')
    assert calls==[]
    db.rollback();sql=[]
    event.listen(db.get_bind(),'before_cursor_execute',lambda *args:sql.append(args[2]))
    first=runtime.authentication_cipher(settings);second=runtime.authentication_cipher(settings)
    assert first is not second
    assert first.active_key_version()==first.active_key_version()==8
    assert len(calls)==1
    second.active_key_version();assert len(calls)==2
    old=first.encrypt(b'history',aad=b'aad',key_version=7)
    assert second.decrypt(old.ciphertext,nonce=old.nonce,aad=b'aad',key_version=7)==b'history'
    cipher=runtime.contact_cipher(settings)
    args=dict(mobile_hmac_secret='h'*40,mobile_hash_version=1,request_id=uuid.uuid4(),requester_person_id=uuid.uuid4())
    envelope=protect_material_request_contact(cipher=cipher,kms_key_id='',name='Synthetic',mobile='13800138000',**args)
    assert envelope['schema']=='rsc.material_request_contact.v2'
    assert reveal_material_request_contact(cipher=cipher,kms_key_id='',envelope=envelope,**args)['mobile']=='13800138000'
    old=protect_material_request_contact(cipher=cipher.legacy_cipher,kms_key_id=f'kms/{CONTACT}/v7',name='Synthetic',mobile='13800138000',**args)
    assert reveal_material_request_contact(cipher=cipher,kms_key_id='',envelope=old,**args)['mobile']=='13800138000'
    assert not sql and not db.in_transaction()


@pytest.mark.parametrize('oldprovider',['aliyun_kms','openbao_transit_v1'])
def test_auth_aliyun_active_uses_exact_historical_cmk_or_provider(db,harness,oldprovider):
    seed,build,_,calls,_=harness
    seed(AUTH,7,oldprovider);seed(AUTH,8,'aliyun_kms');add_auth(db,7)
    settings,runtime=build(auth='aliyun_kms')
    cipher=runtime.authentication_cipher(settings);assert cipher.active_key_version()==8
    old=cipher.encrypt(b'history',aad=b'aad',key_version=7)
    assert cipher.decrypt(old.ciphertext,nonce=old.nonce,aad=b'aad',key_version=7)==b'history'
    assert len(calls)==2
    assert calls[0][0]=='ali' and calls[1][0]==('ali' if oldprovider=='aliyun_kms' else 'bao')


def test_unknown_version_and_failed_provider_have_no_fallback(db,harness):
    seed,build,transport,calls,_=harness
    seed(AUTH,7,'aliyun_kms');seed(AUTH,8,'openbao_transit_v1');add_auth(db,7)
    settings,runtime=build();cipher=runtime.authentication_cipher(settings)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):cipher.encrypt(b'x',aad=b'a',key_version=999)
    assert not calls
    transport.fail=True
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):cipher.active_key_version()
    assert [c[0] for c in calls]==['bao']


@pytest.mark.parametrize('field,value',[('openbao_provider_instance_id','other'),('auth_idempotency_encryption_key_version',9),('openbao_token_file','/different/token'),('environment','production'),('material_request_writes_enabled',True)])
def test_runtime_rejects_settings_drift(db,harness,field,value):
    seed,build,_,calls,_=harness;seed(AUTH,8,'openbao_transit_v1');settings,runtime=build()
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):runtime.authentication_cipher(settings.model_copy(update={field:value}))
    assert not calls


def test_readiness_full_binding_and_no_dek_cache(db,harness):
    seed,build,_,calls,_=harness
    seed(AUTH,7,'aliyun_kms');seed(AUTH,8,'openbao_transit_v1');add_auth(db,7)
    settings,runtime=build()
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):runtime.probe(AUTH,'0'*64,8)
    assert not calls
    gate=KmsReadinessGate(loader=runtime.probe)
    try:
        assert gate.is_ready(runtime.required_coordinates) and gate.is_ready(runtime.required_coordinates)
        assert len(calls)==2
        runtime.authentication_cipher(settings).active_key_version();assert len(calls)==3
    finally:gate.close()


def test_contact_reverse_provider_migration_is_blocked(db,harness):
    seed,build,_,calls,_=harness
    seed(AUTH,8,'aliyun_kms');seed(CONTACT,8,'aliyun_kms');seed(CONTACT,7,'openbao_transit_v1')
    add_contact(db,contact_v2(7,provider_instance_id=INSTANCE),historical=True)
    with pytest.raises(ProductionAdapterConfigurationError):build(auth='aliyun_kms',contact='aliyun_kms')
    assert not calls


@pytest.mark.parametrize('kind',['missing','active_provider','active_cmk','registry_digest','extra_registry'])
def test_startup_fails_closed_without_exception_chains_or_provider_calls(db,harness,kind):
    seed,build,_,calls,entries=harness
    if kind!='missing':seed(AUTH,8,'openbao_transit_v1')
    if kind=='registry_digest':entries[0]['ciphertext']='vault:v2:'+base64.b64encode(b'Z'*64).decode()
    if kind=='extra_registry':seed(CONTACT,8,'openbao_transit_v1')
    opts={}
    if kind=='active_provider':opts['auth']='aliyun_kms'
    if kind=='active_cmk':seed(AUTH,9,'aliyun_kms',key_id='kms/auth/wrong');opts.update(auth='aliyun_kms',av=9)
    with pytest.raises(ProductionAdapterConfigurationError) as exc:build(**opts)
    assert exc.value.__cause__ is exc.value.__context__ is None
    assert not calls


def test_contact_v1_history_routes_multiple_different_cmks(db,harness):
    seed,build,_,calls,_=harness
    seed(AUTH,8,'openbao_transit_v1');seed(CONTACT,8,'openbao_transit_v1')
    for version in (6,7):
        seed(CONTACT,version,'aliyun_kms')
        add_contact(db,contact_v1(version,kms_key_id=f'kms/{CONTACT}/v{version}'),historical=True)
    settings,runtime=build(contact='openbao_transit_v1')
    cipher=runtime.contact_cipher(settings)
    from app.production_adapters import _MaterialRequestContactCipherRing
    args=dict(mobile_hmac_secret='h'*40,mobile_hash_version=1,request_id=uuid.uuid4(),requester_person_id=uuid.uuid4())
    for version in (6,7):
        writer=_MaterialRequestContactCipherRing(environment='test',loader=runtime._aliyun_loader,active_kms_key_id=f'kms/{CONTACT}/v{version}',active_version=version)
        old=protect_material_request_contact(cipher=writer,kms_key_id=f'kms/{CONTACT}/v{version}',name='Synthetic',mobile='13800138000',**args)
        assert reveal_material_request_contact(cipher=cipher,kms_key_id='',envelope=old,**args)['name']=='Synthetic'
    assert [kind for kind,_ in calls]==['ali']*4


def test_runtime_rejects_mutated_wrapped_or_aliyun_material(db,harness):
    from dataclasses import replace
    seed,build,_,calls,_=harness
    seed(AUTH,7,'aliyun_kms');seed(AUTH,8,'openbao_transit_v1');add_auth(db,7)
    settings,runtime=build()
    entry=runtime._wrapped_entries[0]
    corrupt=replace(entry,ciphertext='vault:v2:'+base64.b64encode(b'Z'*60).decode())
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):replace(runtime,_wrapped_entries=(corrupt,)).authentication_cipher(settings)
    ali=runtime._aliyun_loader
    coordinate=next(iter(ali._entries));original=ali._entries[coordinate]
    ali._entries[coordinate]=replace(original,ciphertext_blob='Z'*32)
    cipher=runtime.authentication_cipher(settings)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):cipher.encrypt(b'x',aad=b'a',key_version=7)
    assert not calls


def test_required_claims_are_select_only_and_never_autoflush(db,harness):
    from app.persisted_key_references import read_required_key_claims
    seed,_,_,_,_=harness;seed(AUTH,8,'openbao_transit_v1')
    db.add(ApplicationKeyVersionClaim(purpose='invalid-unflushed',application_key_version=3,provider='invalid',ciphertext_sha256='bad'))
    sql=[]
    event.listen(db.get_bind(),'before_cursor_execute',lambda *args:sql.append(args[2]))
    catalog=read_required_key_claims(db,environment='test',required=frozenset({(AUTH,8)}),openbao_provider_instance_id=INSTANCE)
    assert len(catalog.openbao)==1
    assert len(db.new)==1 and len(sql)==1 and sql[0].lstrip().upper().startswith('SELECT')


@pytest.mark.parametrize('bad',[frozenset(),frozenset({(AUTH,True)}),frozenset({('unknown',8)}),frozenset({(AUTH,v) for v in range(1,130)})])
def test_required_claim_reader_rejects_bad_request_without_sql(db,bad):
    from app.persisted_key_references import read_required_key_claims,PersistedKeyReferenceUnavailable
    sql=[];event.listen(db.get_bind(),'before_cursor_execute',lambda *args:sql.append(args[2]))
    with pytest.raises(PersistedKeyReferenceUnavailable) as exc:read_required_key_claims(db,environment='test',required=bad)
    assert not sql and exc.value.__context__ is exc.value.__cause__ is None


def test_runtime_provider_failure_has_no_sensitive_exception_chain(db,harness,monkeypatch):
    seed,build,transport,calls,_=harness
    seed(AUTH,7,'aliyun_kms');seed(AUTH,8,'openbao_transit_v1');add_auth(db,7)
    _,runtime=build()
    secret_marker='SYNTHETIC_PROVIDER_RESPONSE_DO_NOT_RETAIN'
    def failed(*args,**kwargs):raise RuntimeError(secret_marker)
    monkeypatch.setattr(runtime._aliyun_loader,'_resolve',failed)
    transport.fail=True
    for version in (7,8):
        with pytest.raises(AuthenticationEncryptionKeyUnavailable) as exc:runtime._resolve(runtime._pin(AUTH,version))
        assert exc.value.__cause__ is exc.value.__context__ is None
        assert secret_marker not in str(exc.value)
