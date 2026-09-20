"""The remote acceptance harness is exercised only against isolated local apps."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('deployment_verifier', ROOT/'tools/verify_deployment.py')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('method,path,payload', [
    ('DELETE','/api/data',None),
    ('POST','/api/cases/a/billing/setup',{'accept_fee_terms':True}),
    ('POST','/api/cases/a/reduction-confirmed',{'confirm_fee':True,'fee_amount_cents':1}),
    ('POST','/mcp',{'method':'tools/call'}),
    ('POST','/api/scan',{'source':'fixtures'}),
    ('GET','//other.example/path',None),
])
def test_rejects_effectful_or_unsafe_calls(method,path,payload):
    with pytest.raises(MODULE.CheckFailure):
        MODULE.validate_operation(method,path,payload)


def test_key_and_url_validation_does_not_expose_secret(tmp_path):
    keyfile=tmp_path/'key'
    secret='secret with spaces which must never be printed'
    keyfile.write_text(secret)
    with pytest.raises(MODULE.CheckFailure) as exc:
        MODULE.load_key(keyfile)
    assert secret not in str(exc.value)
    with pytest.raises(MODULE.CheckFailure):
        MODULE.validate_base_url('https://user:password@example.com')
    assert MODULE.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example') is None


@pytest.mark.parametrize('slug',MODULE.SERVICES)
def test_acceptance_harness_against_local_service(slug,tmp_path):
    script = r'''
import hashlib, importlib.util, json, os, sys
from pathlib import Path
from fastapi.testclient import TestClient
slug=sys.argv[1]
repo=Path(os.environ['QULL_TEST_REPO'])
keys=['synthetic-review-a-'+('a'*32),'synthetic-review-b-'+('b'*32)]
registry=Path(os.environ['DATA_DIR'])/'registry.json'
registry.write_text(json.dumps({'version':1,'keys':[{'sha256':hashlib.sha256(k.encode()).hexdigest(),'owner_id':f'review-{i}'} for i,k in enumerate(keys)]}))
registry.chmod(0o600)
os.environ['QULL_API_KEYS_FILE']=str(registry)
import app, mcp_server
spec=importlib.util.spec_from_file_location('remote_checks',repo/'tools/verify_deployment.py')
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
with TestClient(app.app,base_url='https://example.test') as rest, TestClient(mcp_server.create_mcp_app(),base_url='https://example.test') as mcp:
 class LocalVerifier(mod.Verifier):
  def request(self,service,method,path,payload=None,identity='a',extra_headers=None):
   mod.validate_operation(method,path,payload)
   assert service==slug
   headers={'Accept':'application/json'}
   if identity:headers['Authorization']='Bearer '+self._keys[identity]
   headers.update(extra_headers or {})
   resp=(mcp if path=='/mcp' else rest).request(method,path,json=payload,headers=headers)
   return mod.Reply(resp.status_code,dict(resp.headers),resp.content)
 verifier=LocalVerifier('https://example.test',keys[0],keys[1])
 report=verifier.run([slug])
 assert report['failed']==0,report
 assert report['blocked']==0,report
 assert report['created_fixtures'],report
 assert len(report['checks'])==5
'''
    env={**os.environ, 'QULL_TEST_REPO':str(ROOT), 'PYTHONPATH':str(ROOT/'backend')+os.pathsep+str(ROOT/'backend'/slug),
         'DATA_DIR':str(tmp_path), 'ENV':'test', 'ALLOW_DEV_IDENTITY':'0',
         'STRIPE_SECRET_KEY':'sk_test_SYNTHETIC_NOT_A_REAL_KEY', 'STRIPE_MODE':'test',
         'STRIPE_PUBLISHABLE_KEY':'pk_test_SYNTHETIC_NOT_A_REAL_KEY', 'PUBLIC_BASE_URL':'https://example.test/'+slug}
    result=subprocess.run([sys.executable,'-c',script,slug],cwd=ROOT/'backend'/slug,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
