"""Synthetic identities in the disposable, already-unsealed OpenBao harness.

Hook: identity_isolated_gate:run_identity_contract. All runtime JWTs, AppRole
SecretIDs and Bao tokens remain in memory. No RAM, STS, TLS issuer or app wiring.
API contract: https://openbao.org/docs/api/secret/identity/tokens/
"""
from dataclasses import replace
import json
import time
import jwt

from identity_contract import IdentityContract, IdentityContractError, verify_identity_token

ISSUER_ORIGIN='https://identity-pilot.invalid'
ISSUER=ISSUER_ORIGIN+'/v1/identity/oidc'
AUDIENCE='synthetic-api'
AUTH='identity-pilot'
ROLE='synthetic-api'
KEY='synthetic-signing-key'


def _require(value,step):
    if not value:
        raise IdentityContractError('identity_isolated_'+step)


def _body(response,step,statuses=(200,)):
    status,body=response
    _require(status in statuses,step)
    if not body:
        return {}
    if isinstance(body,bytes):
        body=json.loads(body)
    _require(isinstance(body,dict),step+'_shape')
    return body


def _root(server,method,path,body,step,statuses=(200,204)):
    return _body(server.root(method,'/v1/'+path,body),step,statuses)


def _public(server,path,step):
    return _body(server.client.request('GET','/v1/'+path),step)


def _mint(server,session,role=ROLE):
    body=_body(server.client.request('GET','/v1/identity/oidc/token/'+role,token=session),'mint')
    _require(body['data']['client_id']==AUDIENCE,'mint_audience')
    token=body['data']['token']
    _require(isinstance(token,str),'mint_shape')
    return token


def _rejected(token,jwks,contract,step):
    try:
        verify_identity_token(token,jwks,contract)
    except IdentityContractError:
        return
    _require(False,step)


def run_identity_contract(server):
    """Only call from LocalBao; all output is safe booleans/counts/fixed claims."""
    try:
        return _run(server)
    except IdentityContractError:
        raise
    except Exception:
        raise IdentityContractError('identity_isolated_unexpected_failure') from None


def _run(server):
    _root(server,'POST','identity/oidc/config',{'issuer':ISSUER_ORIGIN},'configure')
    _root(server,'POST','identity/oidc/key/'+KEY,{
        'algorithm':'RS256','allowed_client_ids':[AUDIENCE],
        'rotation_period':'1h','verification_ttl':'1h'},'create_key')
    for role,ttl in ((ROLE,'5m'),('synthetic-expiring','3s')):
        _root(server,'POST','identity/oidc/role/'+role,
              {'key':KEY,'client_id':AUDIENCE,'ttl':ttl},'create_role')
    policy='\n'.join('path "identity/oidc/token/'+role+'" { capabilities = ["read"] }'
                     for role in (ROLE,'synthetic-expiring'))
    _root(server,'PUT','sys/policies/acl/synthetic-identity',{'policy':policy},'policy')
    _root(server,'POST','sys/auth/'+AUTH,{'type':'approle'},'auth_mount')
    auth=_root(server,'GET','sys/auth',None,'auth_read')['data'][AUTH+'/']
    _root(server,'POST','auth/'+AUTH+'/role/'+ROLE,{
        'token_policies':['synthetic-identity'],'token_no_default_policy':True,
        'token_ttl':'5m','token_max_ttl':'5m','secret_id_ttl':'5m'},'approle')
    role_id=_root(server,'GET','auth/'+AUTH+'/role/'+ROLE+'/role-id',None,'role_id')['data']['role_id']
    entity=_root(server,'POST','identity/entity',{'name':'synthetic-api-entity'},'entity')['data']['id']
    _root(server,'POST','identity/entity-alias',
          {'name':role_id,'canonical_id':entity,'mount_accessor':auth['accessor']},'alias')
    contract=IdentityContract(ISSUER,AUDIENCE,entity)
    secret=_root(server,'POST','auth/'+AUTH+'/role/'+ROLE+'/secret-id',{},'secret_id')['data']
    login=_body(server.client.request('POST','/v1/auth/'+AUTH+'/login',
                {'role_id':role_id,'secret_id':secret['secret_id']}),'login')['auth']
    _require(login['entity_id']==entity,'entity_binding')
    _require(set(login['policies'])=={'synthetic-identity'},'policies_exact')
    session=login['client_token']

    discovery=_public(server,'identity/oidc/.well-known/openid-configuration','discovery')
    _require(discovery['issuer']==ISSUER,'discovery_issuer')
    _require(discovery['jwks_uri']==ISSUER+'/.well-known/keys','discovery_keys')
    role=_root(server,'GET','identity/oidc/role/'+ROLE,None,'role_read')['data']
    _require(role['client_id']==AUDIENCE and role['ttl']==300 and role['key']==KEY,'role_contract')
    original=_mint(server,session)
    jwks=_public(server,'identity/oidc/.well-known/keys','jwks')
    first=verify_identity_token(original,jwks,contract)
    _require(first.expires_at-first.issued_at==300,'ttl_exact')

    # The caller cannot change its role/key configuration or request another role.
    denied=0
    for method,path,body in (
        ('POST','identity/oidc/config',{'issuer':'https://other.invalid'}),
        ('POST','identity/oidc/key/'+KEY+'/rotate',{}),
        ('POST','identity/oidc/role/'+ROLE,{'ttl':'1h'}),
        ('GET','identity/oidc/token/synthetic-other',None),
        ('GET','sys/policies/acl/synthetic-identity',None),
    ):
        status,_=server.client.request(method,'/v1/'+path,body,token=session)
        _require(status==403,'acl_denied');denied+=1
    _require(server.client.request('GET','/v1/identity/oidc/token/'+ROLE)[0]==403,'unauth_mint_denied')

    tokens=[original];key_ids={first.key_id}
    for _ in range(2):
        _root(server,'POST','identity/oidc/key/'+KEY+'/rotate',{},'rotate')
        tokens.append(_mint(server,session))
        jwks=_public(server,'identity/oidc/.well-known/keys','rotated_jwks')
        latest=verify_identity_token(tokens[-1],jwks,contract)
        _require(latest.key_id not in key_ids,'new_key_id')
        key_ids.add(latest.key_id)
        for token in tokens:
            verify_identity_token(token,jwks,contract)
    for altered in (
        replace(contract,issuer='https://other.invalid/v1/identity/oidc'),
        replace(contract,audience='synthetic-other'),
        replace(contract,subject='00000000-0000-4000-8000-000000000002'),
    ):
        _rejected(tokens[-1],jwks,altered,'wrong_claim_accepted')
    parts=tokens[-1].split('.')
    signature=bytearray(jwt.utils.base64url_decode(parts[2]));signature[0]^=1
    parts[2]=jwt.utils.base64url_encode(bytes(signature)).decode()
    _rejected('.'.join(parts),jwks,contract,'bad_signature_accepted')

    short=_mint(server,session,'synthetic-expiring')
    short_contract=replace(contract,minimum_remaining_seconds=0)
    short_verified=verify_identity_token(short,jwks,short_contract)
    wait=short_verified.expires_at-time.time()+0.1
    _require(0<wait<4,'expiry_wait_bound')
    time.sleep(wait)
    _rejected(short,jwks,short_contract,'expired_accepted')

    # Destroying bootstrap material stops a new login. Existing authenticated
    # sessions need their own revocation; neither step retracts a signed JWT.
    _root(server,'POST','auth/'+AUTH+'/role/'+ROLE+'/secret-id-accessor/destroy',
          {'secret_id_accessor':secret['secret_id_accessor']},'bootstrap_revoke')
    status,_=server.client.request('POST','/v1/auth/'+AUTH+'/login',
                                  {'role_id':role_id,'secret_id':secret['secret_id']})
    _require(status in (400,403),'bootstrap_login_denied')
    _root(server,'POST','auth/token/revoke',{'token':session},'session_revoke')
    status,_=server.client.request('GET','/v1/identity/oidc/token/'+ROLE,token=session)
    _require(status==403,'revoked_mint_denied')
    verify_identity_token(tokens[-1],jwks,contract)

    return {
        'real_isolated_openbao_identity':True,'synthetic_entities_only':True,
        'signed_jwt_claims_verified':True,'algorithm':'RS256','audience':AUDIENCE,
        'issuer':ISSUER,'entity_uuid_independently_bound':True,'ttl_seconds':300,
        'rotations':2,'distinct_signing_keys':len(key_ids),'old_new_keys_verified':True,
        'wrong_claims_rejected':3,'tampered_signature_rejected':True,'expired_rejected':True,
        'acl_denials':denied,'unauthenticated_mint_denied':True,
        'bootstrap_revoked_login_denied':True,'revoked_session_mint_denied':True,
        'already_signed_token_remains_valid_until_expiry':True,
        'runtime_credentials_persisted':False,'real_ram_sts_verified':False,
        'public_tls_issuer_verified':False,'production_provider_enabled':False,
    }
