"""Synthetic-only focused tests. Run directly for code-only failure reporting.

Keys and credentials are generated in memory. Only synthetic JWTs are projected
into temporary private test directories. Network is blocked in the SDK test.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import identity_contract as identity


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.key=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.key.update(kid='synthetic-current',alg='RS256',use='sig')
        cls.jwks={'keys':[cls.key]}
        cls.contract=identity.IdentityContract('https://identity-pilot.invalid/v1/identity/oidc',
            'synthetic-api','00000000-0000-4000-8000-000000000001')

    def claims(self):
        now=int(time.time())
        return dict(iss=self.contract.issuer,aud=self.contract.audience,sub=self.contract.subject,
                    iat=now,exp=now+300)

    def token(self, claims=None, key=None, headers=None):
        return jwt.encode(claims or self.claims(),key or self.private,algorithm='RS256',
                          headers=headers or {'kid':'synthetic-current'})

    def reject(self,token,jwks=None,contract=None):
        with self.assertRaises(identity.IdentityContractError) as caught:
            identity.verify_identity_token(token,jwks if jwks is not None else self.jwks,
                                           contract or self.contract)
        self.assertRegex(str(caught.exception),r'^identity_[a-z_]+$')
        self.assertIsNone(caught.exception.__cause__)

    def test_good_signature_and_fixed_claims(self):
        verified=identity.verify_identity_token(self.token(),self.jwks,self.contract)
        self.assertTrue(verified.subject==self.contract.subject)
        self.assertTrue(verified.expires_at-verified.issued_at==300)

    def test_missing_required_claims(self):
        for name in ('iss','aud','sub','iat','exp'):
            with self.subTest(field=name):
                claims=self.claims();del claims[name]
                self.reject(self.token(claims))

    def test_wrong_identity_coordinates(self):
        for name,value in (('iss','https://other.invalid/v1/identity/oidc'),
                           ('aud','synthetic-other'),('aud',['synthetic-api']),
                           ('sub','00000000-0000-4000-8000-000000000002')):
            with self.subTest(field=name):
                claims=self.claims();claims[name]=value
                self.reject(self.token(claims))

    def test_strict_numeric_dates(self):
        for name in ('iat','exp'):
            for value in (True,None,'1900000000',1.5,-1,0,{},[]):
                with self.subTest(field=name,kind=type(value).__name__):
                    claims=self.claims();claims[name]=value
                    self.reject(self.token(claims))

    def test_time_and_lifetime_boundaries(self):
        now=int(time.time())
        for start,end in ((now-600,now),(now+60,now+300),(now,now+601),
                          (now,now+30),(now,now),(now,now-1)):
            claims=self.claims();claims.update(iat=start,exp=end)
            self.reject(self.token(claims))

    def test_bad_signature_algorithms_and_headers(self):
        self.reject(self.token(key=self.other))
        for algorithm in ('none','HS256'):
            token=jwt.encode(self.claims(),'' if algorithm=='none' else os.urandom(32),
                             algorithm=algorithm,headers={'kid':'synthetic-current'})
            self.reject(token)
        for header in ({'kid':'unknown'},{'kid':'synthetic-current','jku':'https://other.invalid'},
                       {'kid':'synthetic-current','crit':['unexpected']},
                       {'kid':'synthetic-current','typ':'JOSE'}):
            self.reject(self.token(headers=header))
        self.reject(jwt.encode(self.claims(),self.private,algorithm='RS256'))

    def test_malformed_jwt_is_safe(self):
        for token in (None,'','a.b.c','a.b.c.d','x'*20001,'\u2603.\u2603.\u2603'):
            self.reject(token)

    def test_duplicate_json_fields_are_rejected(self):
        token=self.token();parts=token.split('.')
        for index,raw in ((0,b'{"alg":"RS256","alg":"none","kid":"synthetic-current"}'),
                          (1,b'{"sub":"one","sub":"two"}')):
            modified=parts[:];modified[index]=jwt.utils.base64url_encode(raw).decode()
            self.reject('.'.join(modified))

    def test_jwks_invalid_or_ambiguous(self):
        for jwks in ({},{'keys':[]},{'keys':None},{'keys':[self.key,self.key]},
                     {'keys':[dict(self.key,kid=[])]},{'keys':[None]},None,[]):
            with self.assertRaises(identity.IdentityContractError):
                identity.verify_identity_token(self.token(),jwks,self.contract)
        for field,value in (('kty','oct'),('alg','HS256'),('use','enc'),('d','synthetic'),
                            ('n','!'),('e',''),('kid','unknown')):
            self.reject(self.token(),{'keys':[dict(self.key,**{field:value})]})

    def test_weak_rsa_key_rejected(self):
        private=rsa.generate_private_key(public_exponent=65537,key_size=1024)
        key=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        key.update(kid='synthetic-current',alg='RS256',use='sig')
        self.reject(self.token(key=private),{'keys':[key]})

    def test_rotation_accepts_both_then_retired_key_rejected(self):
        other=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.other.public_key()))
        other.update(kid='synthetic-next',alg='RS256',use='sig')
        rotated={'keys':[self.key,other]}
        old=self.token();new=self.token(key=self.other,headers={'kid':'synthetic-next'})
        identity.verify_identity_token(old,rotated,self.contract)
        identity.verify_identity_token(new,rotated,self.contract)
        self.reject(old,{'keys':[other]})

    def test_invalid_contract(self):
        for changes in ({'issuer':'http://identity-pilot.invalid/v1/identity/oidc'},
                        {'issuer':'https://u:p@identity-pilot.invalid/v1/identity/oidc'},
                        {'issuer':self.contract.issuer+'?query=x'},
                        {'audience':['synthetic-api']},{'audience':'*'},{'audience':'a b'},
                        {'subject':'api-name'},{'maximum_ttl_seconds':601},
                        {'maximum_ttl_seconds':True},{'minimum_remaining_seconds':600}):
            with self.assertRaises(identity.IdentityContractError):
                replace(self.contract,**changes)

    def project(self,directory,token=None):
        return identity.project_identity_token(directory,token or self.token(),self.jwks,self.contract)

    def test_private_atomic_projection_and_directory_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);directory.chmod(0o700)
            first=self.token();self.project(directory,first)
            target=directory/'oidc-token'
            with target.open() as old_inode:
                claims=self.claims();claims['exp']-=1
                second=self.token(claims);self.project(directory,second)
                self.assertTrue(target.read_text()==second)
                self.assertTrue(old_inode.read()==first)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
            self.assertEqual(target.stat().st_nlink,1)
            self.assertEqual([p.name for p in directory.iterdir()],['oidc-token'])

    def test_projection_invalid_token_preserves_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);first=self.token();self.project(directory,first)
            claims=self.claims();claims['aud']='wrong'
            with self.assertRaises(identity.IdentityContractError):self.project(directory,self.token(claims))
            self.assertTrue((directory/'oidc-token').read_text()==first)

    def test_projection_rejects_unsafe_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);directory.chmod(0o755)
            with self.assertRaises(identity.IdentityContractError):self.project(directory)
            directory.chmod(0o700)
            link=directory/'link';link.symlink_to(directory,target_is_directory=True)
            with self.assertRaises(identity.IdentityContractError):self.project(link)

    def test_projection_rejects_target_permission_symlink_and_hardlink(self):
        for kind in ('permissions','symlink','hardlink','directory'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);target=directory/'oidc-token';other=directory/'other'
                if kind=='permissions':target.touch(mode=0o644)
                if kind=='symlink':other.touch(mode=0o600);target.symlink_to(other)
                if kind=='hardlink':other.touch(mode=0o600);os.link(other,target)
                if kind=='directory':target.mkdir()
                with self.assertRaises(identity.IdentityContractError):self.project(directory)

    def test_projection_wrong_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(identity.os,'geteuid',return_value=os.geteuid()+1):
            with self.assertRaises(identity.IdentityContractError):self.project(Path(directory))

    def test_projection_replace_failure_preserves_old_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);first=self.token();self.project(directory,first)
            with patch.object(identity.os,'replace',side_effect=OSError('synthetic-private-detail')):
                with self.assertRaisesRegex(identity.IdentityContractError,'^identity_projection_failed$'):
                    self.project(directory)
            self.assertTrue((directory/'oidc-token').read_text()==first)
            self.assertEqual([p.name for p in directory.iterdir()],['oidc-token'])

    def test_projection_rechecks_expiry_before_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);first=self.token();self.project(directory,first)
            verified=identity.verify_identity_token(first,self.jwks,self.contract)
            with patch.object(identity,'verify_identity_token',side_effect=[verified,
                    identity.IdentityContractError('identity_token_remaining_too_short')]):
                with self.assertRaises(identity.IdentityContractError):self.project(directory)
            self.assertTrue((directory/'oidc-token').read_text()==first)
            self.assertEqual([p.name for p in directory.iterdir()],['oidc-token'])

    def test_synthetic_sdk_rereads_token_file_on_refresh_without_network(self):
        # Import under an empty environment, use an explicit provider and fake
        # response. This does not test default-chain selection or real RAM/STS.
        with patch.dict(os.environ,{},clear=True),patch.object(socket.socket,'connect',
                side_effect=RuntimeError('synthetic_network_forbidden')):
            from alibabacloud_credentials.provider.oidc import OIDCRoleArnCredentialsProvider, TeaCore
            expected=[None];calls=[0]
            def synthetic_sts(request,*_):
                if request.query.get('OIDCToken')!=expected[0]:
                    raise RuntimeError('synthetic_token_reread_failed')
                if request.query.get('Action')!='AssumeRoleWithOIDC':
                    raise RuntimeError('synthetic_sts_action_invalid')
                identity.verify_identity_token(request.query['OIDCToken'],self.jwks,self.contract)
                calls[0]+=1
                credentials={name:os.urandom(24).hex() for name in
                             ('AccessKeyId','AccessKeySecret','SecurityToken')}
                credentials['Expiration']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()+3600))
                return SimpleNamespace(status_code=200,body=json.dumps({'Credentials':credentials}).encode())
            with tempfile.TemporaryDirectory() as temporary,patch.object(TeaCore,'do_action',side_effect=synthetic_sts):
                directory=Path(temporary)
                provider=OIDCRoleArnCredentialsProvider(role_arn='acs:ram::0000000000000000:role/synthetic',
                    oidc_provider_arn='acs:ram::0000000000000000:oidc-provider/synthetic',
                    oidc_token_file_path=str(directory/'oidc-token'),role_session_name='synthetic-session',
                    sts_endpoint='sts.invalid',duration_seconds=3600)
                for offset in (0,1):
                    claims=self.claims();claims['exp']-=offset
                    expected[0]=self.token(claims);self.project(directory,expected[0])
                    refreshed=provider._refresh_credentials()
                    self.assertTrue(refreshed.value().get_expiration()>time.time())
                self.assertEqual(calls[0],2)
                (directory/'oidc-token').unlink()
                with self.assertRaises(FileNotFoundError):provider._refresh_credentials()
                self.assertEqual(calls[0],2)


if __name__=='__main__':
    result=unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(IdentityTests).run(result)
    # No unittest tracebacks or assertion values can expose test JWTs/STS.
    print(json.dumps({'evidence':'synthetic_only','tests_run':result.testsRun,
                      'passed':result.wasSuccessful(),'failures':len(result.failures),
                      'errors':len(result.errors),'failed_tests':[
                          case.id() for case,_ in result.failures+result.errors]},sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
