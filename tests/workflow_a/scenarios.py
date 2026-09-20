"""Executable isolated service integration checks (no provider/network access)."""
import base64
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path.cwd()))
from fastapi.testclient import TestClient
import app as rest
import mcp_server

slug = os.environ['TEST_SERVICE']
A = {'Authorization': 'Bearer ' + 'a' * 64}
B = {'Authorization': 'Bearer ' + 'b' * 64}
import hashlib
registry = Path(os.environ['DATA_DIR']) / 'test-keys.json'
registry.write_text(json.dumps({'version':1,'keys':[{'sha256':hashlib.sha256((letter*64).encode()).hexdigest(),'owner_id':owner} for letter,owner in [('a','alice'),('b','bob')]]}))
registry.chmod(0o600)
os.environ['QULL_API_KEYS_FILE'] = str(registry)
os.environ.pop('ALLOW_DEV_IDENTITY', None)
from datetime import date

def checked(response, code=200):
    assert response.status_code == code, (response.status_code, response.text)
    return response.json()

# Fail any unintended external socket use; TestClient invokes ASGI in process.
import socket
socket.create_connection = Mock(side_effect=AssertionError('Unexpected network access'))

billing = rest.core.billing if slug == 'class-action-cash' else rest.billing if hasattr(rest, 'billing') else __import__('src.billing', fromlist=[''])
setup_customer = Mock(return_value={'customer_id': 'cus_owned'})
create_card_setup = Mock(return_value={'setup_url': 'https://checkout.stripe.com/test-session', 'checkout_session_id': 'cs_owned', 'setup_intent_id': None, 'client_secret': None})
retrieve_card_setup = Mock(return_value={'status': 'requires_confirmation', 'error': 'Card setup incomplete'})
charge_fee = Mock(return_value={'error': 'Authenticate this fee', 'code': 'payment_action_required', 'status': 'requires_action', 'payment_intent_id': 'pi_owned', 'authorization_url': 'https://example.test/billing/authorize/token'})
for name, stub in [('setup_customer', setup_customer), ('create_card_setup', create_card_setup), ('retrieve_card_setup', retrieve_card_setup), ('charge_fee', charge_fee)]:
    setattr(billing, name, stub)
    if hasattr(rest, name):
        setattr(rest, name, stub)

with TestClient(rest.app) as client:
    assert client.get('/api/me/data').status_code == 401
    assert client.get('/api/me/data', headers={'X-Platform-User-Id': 'alice'}).status_code == 401
    assert client.post('/api/life-events', json={'event_type': 'move', 'payload': {}}).status_code == 401
    if slug == 'deposit-recovery':
        intake = dict(tenant_name='Alice', tenant_forwarding_address='1 New Street', state='CA', move_out='2026-01-01', deposit=1200,
                      landlord_name='Landlord', landlord_address='2 Old Road', rental_address='3 Rental Street')
        bad = {**intake, 'tenant_name': '   '}
        checked(client.post('/api/cases', json=bad, headers=A), 422)
        record = checked(client.post('/api/cases', json=intake, headers=A)); cid = record['case_id']; base = '/api/cases/' + cid
        assert record['deadline'] == '2026-01-22'
        assert record['penalty_multiple'] is None
        checked(client.post(base+'/letter-status', json={'status':'approved'}, headers=A), 409)
        pdf = checked(client.post(base+'/demand-letter', headers=A))
        assert base64.b64decode(pdf['pdf_base64']).startswith(b'%PDF')
        checked(client.post(base+'/letter-status', json={'status':'sent-confirmed-by-user'}, headers=A))
        ct = checked(client.post('/api/cases', json={**intake,'state':'CT','tenancy_end':'2026-01-01','forwarding_date':'2026-01-20'}, headers=A))
        assert ct['deadline'] == '2026-02-04'
        no_end = checked(client.post('/api/cases',json={**intake,'state':'CT','forwarding_date':'2026-01-20'},headers=A))
        assert no_end['status']=='needs_review' and no_end['deadline'] is None
        state_list=checked(client.get('/api/state-laws',headers=A))['states']
        assert next(s for s in state_list if s['abbr']=='AZ')['deadline_days'] is None
        az = checked(client.post('/api/cases', json={**intake,'state':'AZ'}, headers=A)); assert az['status']=='needs_review' and az['deadline'] is None
        assert 'pdf_base64' in checked(client.post('/api/cases/'+az['case_id']+'/demand-letter', headers=A))
        draft = checked(client.post('/api/life-events', json={'event_type':'move','payload':{'state':'CA'}},headers=A))
        did = draft['draft_id']; checked(client.get('/api/drafts/'+did,headers=B),404)
        checked(client.post('/api/drafts/'+did+'/promote',json={},headers=A),400)
        promoted = checked(client.post('/api/drafts/'+did+'/promote',json=intake,headers=A)); assert promoted['case_id']
        checked(client.post('/api/drafts/'+did+'/promote',json=intake,headers=A),404)
        unowned='legacy-unowned';rest.db.insert_draft(unowned,{'event_type':'move','payload':{}},owner_id=None)
        checked(client.get('/api/drafts/'+unowned,headers=A),404)
        fee_path=base+'/recovery-confirmed';fee_body={'amount_recovered':1200,'confirm_fee':True,'fee_amount_cents':30000}
        status_col='billing_status'; table='cases';paid='fee_charged'
    elif slug == 'eu261-flight-comp':
        intake=dict(passenger_name='Alice', passenger_email='alice@example.com', flight_number='AF123', flight_date='2026-01-01',airline='Air France',from_iata='CDG',to_iata='JFK',disruption='delay',arrival_delay_h=4)
        record=checked(client.post('/api/claims',json=intake,headers=A),201);cid=record['claim_id'];base='/api/claims/'+cid
        assert record['compensation_eur']==600 and record['assessment']=='preliminary_eligible'
        pdf=checked(client.post(base+'/claim-pack',headers=A)); assert base64.b64decode(pdf['pdf_base64']).startswith(b'%PDF')
        assert client.get(base+'/claim-pack.pdf',headers=A).content.startswith(b'%PDF')
        checked(client.post(base+'/confirm',json={**intake,'passenger_name':'Alice Corrected'},headers=A))
        checked(client.get(base+'/claim-pack.pdf',headers=A),404)
        checked(client.post(base+'/claim-pack',headers=A))
        checked(client.get(base+'/claim-pack.pdf',headers=B),404)
        c=checked(client.post('/api/claims',json={**intake,'disruption':'cancellation','arrival_delay_h':None},headers=A),201)
        assert c['assessment']=='needs_review' and c['compensation_eur']==0
        mixed={**intake,'arrival_delay_h':None,'scheduled_arrival':'2026-01-01T12:00:00','actual_arrival':'2026-01-01T17:00:00+00:00'}
        checked(client.post('/api/claims',json=mixed,headers=A),422)
        draft=checked(client.post('/api/life-events',json={'event_type':'flight_delayed','payload':{'from_iata':'CDG','to_iata':'JFK'}},headers=A)); checked(client.get('/api/claims/'+draft['claim_id'],headers=B),404)
        checked(client.post('/api/claims/'+draft['claim_id']+'/confirm',json=intake,headers=A))
        from src import eligibility
        # A known intra-EEA route >3500 km still uses EUR400. Input distance can't override known airports.
        orig=eligibility.great_circle_km;eligibility.great_circle_km=lambda *args:4000
        assert eligibility.verdict({**intake,'to_iata':'ATH'})['compensation_eur']==400
        eligibility.great_circle_km=orig
        assert billing.CURRENCY=='eur'
        fee_path=base+'/payout-confirmed';fee_body={'amount_eur':300,'confirm_fee':True,'fee_amount_cents':9000}
        status_col='billing_status';table='claims';paid='fee_charged'
    elif slug == 'bill-negotiator':
        intake=dict(provider='Comcast',service_type='internet',current_monthly_bill=100,user_name='Alice')
        record=checked(client.post('/api/cases',json=intake,headers=A),201);cid=record['case_id'];base='/api/cases/'+cid
        assert checked(client.get(base+'/script',headers=A))['script']
        checked(client.post(base+'/outcome',json={'success':True},headers=A),422)
        outcome=checked(client.post(base+'/outcome',json={'success':True,'new_monthly_bill':80,'months_locked':36},headers=A))
        assert outcome['documented_savings']==240 and outcome['months_locked']==12
        bad=client.post('/api/life-events',json={'event_type':'bill_spike','payload':{'provider':'X'}},headers=A);assert bad.status_code==422
        draft=checked(client.post('/api/life-events',json={'event_type':'bill_spike','payload':intake},headers=A));checked(client.get('/api/cases/'+draft['case_id'],headers=B),404)
        legacy=rest.db.create_case('X','internet',100,None,None,None,owner_id=None);checked(client.get('/api/cases/'+legacy['id'],headers=A),404)
        fee_path=base+'/savings-confirmed';fee_body={'confirm_fee':True,'fee_amount_cents':8400}
        status_col='billing_status';table='cases';paid='fee_charged'
    elif slug == 'final-paycheck':
        intake=dict(employee_name='Alice',employer_name='Employer',state='NV',last_day_worked='2026-01-01',termination_type='fired',wages_owed=1000)
        record=checked(client.post('/api/cases',json=intake,headers=A),201);cid=record['id'];base='/api/cases/'+cid
        assert record['deadline']=='2026-01-01'
        assert client.post(base+'/demand-letter',headers=A).content.startswith(b'%PDF')
        checked(client.post(base+'/confirm',json={'wages_owed':1},headers=A),409)
        ca=checked(client.post('/api/cases',json={**intake,'state':'CA'},headers=A),201); assert ca['status']=='needs_review'
        assert client.post('/api/cases/'+ca['id']+'/demand-letter',headers=A).content.startswith(b'%PDF')
        draft=checked(client.post('/api/life-events',json={'event_type':'job_change','payload':{'state':'NV'}},headers=A));did=draft['case_id']
        checked(client.get('/api/cases/'+did,headers=B),404)
        checked(client.post('/api/cases/'+did+'/confirm',json={},headers=A),422)
        checked(client.post('/api/cases/'+did+'/confirm',json=intake,headers=A))
        legacy=rest.db.create_case({'state':'NV','is_draft':1},owner_id=None);checked(client.get('/api/cases/'+legacy['id'],headers=A),404)
        fee_path=base+'/recovery-confirmed';fee_body={'amount':1000,'confirm_fee':True,'fee_amount_cents':25000}
        status_col='fee_status';table='cases';paid='charged'
    else:
        class ReviewDate(date):
            @classmethod
            def today(cls):
                return cls(2026,9,20)
        rest.core.date=ReviewDate
        settlements=checked(client.get('/api/settlements',headers=A));assert len(settlements['settlements'])==2
        assert all(s['official_claim_url_verified'] for s in settlements['settlements'])
        # Freeze only data freshness in this test to its reviewed date, keeping later runtime expiry tests meaningful.
        receipts=[{'id':'r1','sender':'Kroger','subject':'Rx receipt','snippet':'Kroger prescription receipt in 2020'}, {'id':'r2','sender':'Kroger','subject':'Second Rx','snippet':'Kroger pharmacy receipt'}]
        record=checked(client.post('/api/scan',json={'receipts':receipts},headers=A));assert len(record['matches'])==1
        cid=record['matches'][0]['id'];base='/api/matches/'+cid
        repeat=checked(client.post('/api/scan',json={'receipts':receipts},headers=A));assert repeat['matches'][0]['id']==cid
        assert len(checked(client.get('/api/matches',headers=A))['matches'])==1
        checked(client.post('/api/scan',json={'source':'gmail'},headers=A),422)
        checked(client.post('/api/scan',json={'source':'fixtures'},headers=A),400)
        checked(client.post(base+'/claim-pack',json={},headers=A),422)
        assert checked(client.post(base+'/claim-pack',json={'eligibility_confirmed':True},headers=A))['official_claim_url']=='https://www.krogersavingsclubsettlement.com'
        checked(client.post(base+'/claim-pack',json={'eligibility_confirmed':True},headers=B),404)
        fee_path=base+'/payout-confirmed';fee_body={'amount':100,'currency':'USD','confirm_fee':True,'fee_amount_cents':2000}
        status_col='status';table='billing';paid='billed'
    if slug!='class-action-cash':checked(client.get(base,headers=B),404)
    setup_body={'accept_fee_terms':True}
    if slug=='class-action-cash':setup_body.update(name='Alice',email='alice@example.com')
    checked(client.post(base+'/billing/setup',json={},headers=A),422)
    checked(client.post(base+'/billing/setup',json=setup_body,headers=B),404)
    setup=checked(client.post(base+'/billing/setup',json=setup_body,headers=A));assert setup['setup_url'].startswith('https://checkout.stripe.com/')
    assert setup_customer.call_args.kwargs['idempotency_key']
    status=checked(client.get(base+'/billing/status',headers=A));assert status['card_state']=='pending'
    assert 'saved and ready' not in status['user_message']
    retrieve_card_setup.return_value={'status':'succeeded','card_saved':True,'setup_intent_id':'seti_owned'}
    assert checked(client.get(base+'/billing/status',headers=A))['card_state']=='ready'
    assert client.post(fee_path,json={k:v for k,v in fee_body.items() if k!='confirm_fee'},headers=A).status_code==422
    assert client.post(fee_path,json={**fee_body,'confirm_fee':False},headers=A).status_code==422
    if slug == 'bill-negotiator':
        quote = checked(client.get(base+'/billing/quote', headers=A))
    else:
        quote = checked(client.post(base+'/billing/quote', headers=A, json={k:v for k,v in fee_body.items() if k in ('amount','amount_recovered','amount_eur')}))
    assert quote['fee_amount_cents'] == fee_body['fee_amount_cents'] and quote['charged'] is False
    wrong=client.post(fee_path,json={**fee_body,'fee_amount_cents':1},headers=A); assert wrong.status_code in (400,409)
    assert charge_fee.call_count==0
    result=client.post(fee_path,json=fee_body,headers=A)
    assert result.status_code == 402, (result.status_code, result.text)
    body=result.json();assert body.get('authorization_url')=='https://example.test/billing/authorize/token',(result.status_code,body)
    assert charge_fee.call_args.kwargs['consent'] is True and charge_fee.call_args.kwargs['checkout_session_id']=='cs_owned'
    exported=checked(client.get('/api/me/data',headers=A))['records']
    assert not any(row.get(status_col)==paid for row in exported[table])
    charge_fee.return_value={'payment_intent_id':'pi_owned','status':'succeeded'}
    paid_response=checked(client.post(fee_path,json=fee_body,headers=A));assert paid_response.get('payment_intent_id')=='pi_owned'
    # Paid billing setup may not reset the paid marker.
    assert client.post(base+'/billing/setup',json=setup_body,headers=A).status_code in (400,409)
    before=charge_fee.call_count
    again=client.post(fee_path,json=fee_body,headers=A);assert again.status_code in (400,409) or again.json().get('error')
    assert charge_fee.call_count==before
    if slug=='bill-negotiator':checked(client.post(base+'/outcome',json={'success':False},headers=A),409)
    checked(client.request('DELETE','/api/me/data',headers=A,json={}),422)
    # Bob deletion cannot delete Alice's data.
    checked(client.request('DELETE','/api/me/data',headers=B,json={'confirm_delete':True}))
    assert any(checked(client.get('/api/me/data',headers=A))['records'].values())
    checked(client.request('DELETE','/api/me/data',headers=A,json={'confirm_delete':True}))
    assert not any(checked(client.get('/api/me/data',headers=A))['records'].values())
    # Files in owner folders disappear as part of local deletion.
    if slug in ('deposit-recovery','eu261-flight-comp','final-paycheck'):
        assert not list(Path(os.environ['DATA_DIR']).rglob('*.pdf'))

# MCP transport rejects anonymous requests and keeps no cross-customer session.
with TestClient(mcp_server.create_mcp_app(), base_url="http://127.0.0.1") as mcp:
    body={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
    headers={'Accept':'application/json, text/event-stream',**A}
    assert mcp.post('/mcp',json=body,headers={'Accept':'application/json, text/event-stream'}).status_code==401
    init=mcp.post('/mcp',json=body,headers=headers)
    assert init.status_code==200,init.text
    assert not init.headers.get('mcp-session-id')
    listed = mcp.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},headers=headers)
    assert listed.status_code==200,listed.text
    assert any(tool['name']=='quote_fee' for tool in listed.json()['result']['tools'])
    exported = mcp.post('/mcp',json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'export_my_data','arguments':{}}},headers=headers)
    assert exported.status_code==200,exported.text
    assert not exported.json()['result'].get('isError'), exported.text
    assert 'records' in exported.text and 'authenticated owner' not in exported.text
print(slug, 'passed workflow, isolation, consent, payment-result and deletion checks')
