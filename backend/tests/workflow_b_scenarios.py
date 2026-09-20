"""Subprocess isolation prevents identically-named connector modules contaminating tests."""
import sys
from fastapi.testclient import TestClient
import app as api
import mcp_server

slug = sys.argv[1]
client = TestClient(api.app)
H = {'X-Dev-User-Id':'alice'}
B = {'X-Dev-User-Id':'bob'}
assert client.get('/health').status_code == 200
assert client.delete('/api/data').status_code == 401
assert client.get('/api/anything', headers={'X-Platform-User-Id':'alice'}).status_code == 401
setup_calls=[]
charge_calls=[]
charge_mode=None
def setup_customer(*args, **kwargs):
    assert kwargs.get('idempotency_key')
    setup_calls.append(kwargs)
    return {'customer_id':'cus_test'}
def create_setup(*args, **kwargs):
    return {'setup_url':'https://checkout.stripe.com/test', 'checkout_session_id':'cs_test', 'setup_intent_id':None, 'client_secret':None}
def verify_setup(*args, **kwargs):
    assert kwargs['checkout_session_id']=='cs_test'
    return {'status':'succeeded'}
def charge(*args, **kwargs):
    assert kwargs['consent'] is True
    assert kwargs['checkout_session_id']=='cs_test'
    if charge_mode:
        return {'error':'Payment needs follow-up', 'code':charge_mode, 'payment_intent_id':'pi_test',
                'status':'requires_action' if charge_mode=='payment_action_required' else 'processing',
                'authorization_url':'https://example.test/billing/authenticate#token', 'amount_cents':4900,'currency':'usd'}
    charge_calls.append(kwargs)
    return {'status':'succeeded', 'payment_intent_id':'pi_test', 'amount_label':'$49.00', 'amount_cents':4900}
# Module imports vary, patch wrappers and directly imported references.
api.billing.setup_customer = setup_customer
api.billing.create_card_setup = create_setup
api.billing.retrieve_card_setup = verify_setup
api.billing.charge_fee = charge
api.billing.charge_pack = charge
for name, fn in [('setup_customer',setup_customer),('create_card_setup',create_setup),('charge_fee',charge),('charge_pack',charge)]:
    if hasattr(api,name):setattr(api,name,fn)

def post(path, data=None, headers=H):
    return client.post(path, json=data, headers=headers)
def ok(resp, status=200):
    assert resp.status_code==status, (resp.status_code,resp.text)
    return resp.json()
def locked(path):
    assert client.get(path,headers=B).status_code==404

def setup_and_pay(setup_path, pay_path, setup_extra=None, charge_extra=None, cents=9900):
    assert post(setup_path, setup_extra or {}).status_code==422
    payload={**(setup_extra or {}),'accept_fee_terms':True}
    first=ok(post(setup_path,payload));assert first['setup_url'].startswith('https://checkout.stripe.com/')
    assert first.get('setup_intent_id') is None
    # Reentry returns a usable setup link; no second customer creation.
    before=len(setup_calls);ok(post(setup_path,payload));assert len(setup_calls)==before
    assert post(pay_path, {}).status_code==422
    assert post(pay_path,{'confirm_fee':False,'fee_amount_cents':cents,**(charge_extra or {})}).status_code==422
    assert post(pay_path,{'confirm_fee':True,'fee_amount_cents':cents+1,**(charge_extra or {})}).status_code==409
    global charge_mode
    for code, expected_status in [('payment_action_required',402), ('payment_processing',202), ('billing_unavailable',503)]:
        charge_mode=code
        attempt=post(pay_path,{'confirm_fee':True,'fee_amount_cents':cents,**(charge_extra or {})})
        assert attempt.status_code == expected_status, attempt.text
        assert attempt.json()['code']==code and attempt.json()['authorization_url'].startswith('https://'), attempt.text
    charge_mode=None
    return ok(post(pay_path,{'confirm_fee':True,'fee_amount_cents':cents,**(charge_extra or {})}))

if slug=='subscription-slayer':
    assert post('/api/scan',{'source':'gmail'}).status_code==501
    demo=ok(post('/api/scan',{'source':'fixtures'}));assert demo['demo']
    assert ok(client.get('/api/subscriptions',headers=H))['count']==0
    receipts=[{'sender':'billing@netflix.com','subject':'monthly receipt $1000.00','date':'2026-08-01'},
              {'sender':'billing@netflix.com','subject':'monthly receipt $1000.00','date':'2026-09-01'}]
    data=ok(post('/api/receipts',{'receipts':receipts}),201)
    assert data['estimated_annual_cost_by_currency']=={'USD':12000.0}
    sid=data['detected'][0]['subscription_id']
    locked(f'/api/subscriptions/{sid}/cancel-pack')
    assert post('/api/subscriptions',{'merchant':'Bad','amount':0}).status_code==422
    ok(client.patch(f'/api/subscriptions/{sid}',json={'status':'cancelled','savings_monthly':1000},headers=H))
    setup=ok(post('/api/billing/setup',{'name':'Alice','accept_fee_terms':True}))
    assert setup['checkout_session_id']=='cs_test'
    assert ok(client.get('/api/billing/status',headers=H))['card_state']=='ready'
    assert post('/api/savings/confirmed',{'subscription_ids':[sid,sid],'confirm_fee':True,'fee_amount_cents':2000}).status_code==422
    quote=ok(post('/api/savings/fee-quote',{'subscription_ids':[sid]}));assert quote['fee_amount_cents']==1000
    assert post('/api/savings/confirmed',{'subscription_ids':[sid],'confirm_fee':True,'fee_amount_cents':1001}).status_code==409
    ok(client.patch(f'/api/subscriptions/{sid}',json={'notes':'not locked by incorrect fee'},headers=H))
    charge_mode='payment_action_required'
    failed=post('/api/savings/confirmed',{'subscription_ids':[sid],'confirm_fee':True,'fee_amount_cents':1000});assert failed.status_code==402 and failed.json()['authorization_url']
    charge_mode=None
    paid=ok(post('/api/savings/confirmed' ,{'subscription_ids':[sid],'confirm_fee':True,'fee_amount_cents':1000}))
    assert paid['fee_charged']==10
    assert post('/api/savings/confirmed',{'subscription_ids':[sid],'confirm_fee':True,'fee_amount_cents':1000}).status_code==409
    assert len(charge_calls)==1
    evt=ok(post('/api/life-events',{'event_type':'recurring_charge_detected','payload':{'merchant':'Other','amount':10}}))
    locked(f'/api/subscriptions/{evt["subscription"]["id"]}/cancel-pack')

elif slug=='unclaimed-property':
    assert client.get('/api/searches/missing',headers=H).status_code==404
    created=ok(post('/api/searches',{'full_legal_name':'Alice Example','email':'alice@example.com','states_of_residence':[{'abbr':'NY'}], 'dob':'1980-01-01','dob_consent':True}),201)
    sid=created['search_id'];locked(f'/api/searches/{sid}')
    ok(client.patch(f'/api/searches/{sid}',json={'states_of_residence':[{'abbr':'CA','years':'2010-2020'}], 'dob_consent':False},headers=H))
    record=api.store.get_search(sid,'dev:alice');assert record['dob'] is None and not record['dob_consent']
    assert record['states_of_residence']==[{'abbr':'CA','years':'2010-2020'}]
    pack=ok(client.get(f'/api/searches/{sid}/claim-pack?state=CA',headers=H));assert pack['cover_sheet']['state_of_residence_years']=='2010-2020'
    assert post(f'/api/searches/{sid}/billing/setup',{'accept_fee_terms':True}).status_code==409
    assert not setup_calls
    draft=ok(post('/api/life-events',{'event_type':'move','payload':{'from_state':'TX','to_state':'CA'}}),201)
    locked(f'/api/searches/{draft["search_id"]}')
    unowned=api.store.create_search('Private',[],'',None,False,[{'abbr':'TX'}],draft=True)
    assert client.get(f'/api/searches/{unowned}',headers=H).status_code==404

elif slug=='moving-concierge':
    payload={'name':'Alice','email':'alice@example.com','old_address':'One St','new_address':'Two St','move_date':'2026-10-01','state':'CA'}
    move=ok(post('/api/moves',payload));sid=move['move_id'];locked(f'/api/moves/{sid}')
    before=ok(client.get(f'/api/moves/{sid}',headers=H));assert before['pack_locked'] and before['items']==[]
    assert client.get(f'/api/moves/{sid}/pack',headers=H).status_code==402
    setup_and_pay(f'/api/moves/{sid}/billing/setup',f'/api/moves/{sid}/pay',cents=4900)
    pack=ok(client.get(f'/api/moves/{sid}/pack',headers=H));assert 'complete each change yourself' in pack['markdown']
    pdf=client.get(f'/api/moves/{sid}/pack?format=pdf',headers=H);assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    items=ok(client.get(f'/api/moves/{sid}',headers=H))['items']
    result=ok(client.patch(f'/api/moves/{sid}/checklist',headers=H,json={'items':[{'item_id':i['id'],'status':'na'} for i in items]}));assert result['checklist']['remaining']==0
    assert post('/api/moves',{**payload,'draft_id':sid}).status_code==409
    draft=ok(post('/api/life-events',{'event_type':'move','payload':{'to_state':'TX'}}));locked(f'/api/moves/{draft["move_id"]}')

elif slug=='401k-match':
    payload={'name':'Alice','salary':120000,'pay_frequency':'biweekly','current_contrib_pct':4,'match_pct':50,'match_cap_pct':6}
    plan=ok(post('/api/plans',payload),201);sid=plan['plan_id'];locked(f'/api/plans/{sid}')
    assert plan['uncaptured_match_annual']==1200
    assert post('/api/plans',{**payload,'salary':400000}).status_code==422
    setup_and_pay(f'/api/plans/{sid}/billing/setup',f'/api/plans/{sid}/pay',cents=9900)
    pack=ok(client.get(f'/api/plans/{sid}/pack',headers=H));assert pack['math']['irs_elective_deferral_limit']==24500
    from src.calc import calculate
    high=calculate(300000,'monthly',8,200,10);assert high['max_annual_employer_match']==48000
    assert high['new_annual_employee_contrib']+high['new_employer_match']<=72000
    draft=ok(post('/api/life-events',{'event_type':'job_change','payload':{}}))
    assert post(f'/api/plans/draft/{draft["draft_id"]}/answer',{'answer':'120k and4%'},headers=B).status_code==404
    d=ok(post('/api/plans/draft'),201);ok(post(f'/api/plans/draft/{d["draft_id"]}/answer',{'answer':'120k and4%'}))
    answer=ok(post(f'/api/plans/draft/{d["draft_id"]}/answer',{'answer':'50% up to6%'}));assert 'plan_id' in answer

else:
    payload={'patient_name':'Alice','provider_name':'Clinic <img>','bill_date':'2026-09-01','billed_patient_responsibility':500,'line_items':[{'code':'99213','description':'Visit','amount':250},{'code':'99213','description':'Visit','amount':250}]}
    data=ok(post('/api/cases',payload),201);sid=data['case_id'];locked(f'/api/cases/{sid}')
    assert all(f['severity']!='error' for f in data['findings'])
    for typ in ('dispute','itemized','assistance','negotiate'):
        pdf=client.get(f'/api/cases/{sid}/pack?type={typ}',headers=H);assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    assert post(f'/api/cases/{sid}/outcome',{'reduction_amount':501}).status_code==400
    out=ok(post(f'/api/cases/{sid}/outcome',{'reduction_amount':100}));assert out['fee_cents']==2500
    assert ok(client.get(f'/api/cases/{sid}/fee-quote',headers=H))['fee_amount_cents']==2500
    setup_and_pay(f'/api/cases/{sid}/billing/setup',f'/api/cases/{sid}/reduction-confirmed',cents=2500)
    assert post(f'/api/cases/{sid}/outcome',{'reduction_amount':50}).status_code==400
    assert post('/api/life-events',{'event_type':'medical_bill_received','payload':{'total':'abc'}}).status_code==422
    evt=ok(post('/api/life-events',{'event_type':'medical_bill_received','payload':{'total':100}}));locked(f'/api/cases/{evt["case_id"]}')
    draft=ok(post('/api/cases/draft',{'patient_name':'Alice','provider_name':'Clinic','bill_date':'2026-09-01','total':500,'SSN':'must-not-store'}),201)
    state=api.store.get_case(draft['case_id'],'dev:alice');assert 'SSN' not in str(state)
    for answer in ['skip','skip','no','no']:
        response=ok(post(f'/api/cases/{draft["case_id"]}/answer',{'answer':answer}))
    assert response['done']
# Deletion belongs only to this user, succeeds with financial retention disclosure.
assert ok(client.delete('/api/data',headers=H))['deleted']
# Create Bob's private record, then exercise MCP with Alice and Bob on separate requests.
if slug == 'subscription-slayer':
    private = ok(post('/api/subscriptions', {'merchant':'Bob Only','amount':11}, headers=B),201)
    tool, arguments = 'get_cancel_pack', {'subscription_id':private['subscription']['id']}
elif slug == 'unclaimed-property':
    private = ok(post('/api/searches', {'full_legal_name':'Bob Private','email':'bob@example.com','states_of_residence':[{'abbr':'TX'}]}, headers=B),201)
    tool, arguments = 'get_search', {'search_id':private['search_id']}
elif slug == 'moving-concierge':
    private = ok(post('/api/moves', {'name':'Bob','email':'bob@example.com','old_address':'A','new_address':'B','move_date':'2026-10-01','state':'TX'}, headers=B))
    tool, arguments = 'get_move', {'move_id':private['move_id']}
elif slug == '401k-match':
    private = ok(post('/api/plans', {'name':'Bob','salary':100000,'pay_frequency':'monthly','current_contrib_pct':4,'match_pct':50,'match_cap_pct':6}, headers=B),201)
    tool, arguments = 'get_plan_summary', {'plan_id':private['plan_id']}
else:
    private = ok(post('/api/cases', {'patient_name':'Bob','provider_name':'Clinic','bill_date':'2026-09-01','billed_patient_responsibility':100,'line_items':[{'code':'SUMMARY','description':'Bill','amount':100}]}, headers=B),201)
    tool, arguments = 'get_case', {'case_id':private['case_id']}
# Public API describes actual user bearer authentication, corrected base path and consent fields.
schema=api.app.openapi();assert schema['security']==[{'UserApiKey':[]}]
assert schema['servers'][0]['url'].endswith(slug)
# MCP request isolation is enforced even before initialization.
with TestClient(mcp_server.create_mcp_app(), base_url="http://127.0.0.1:8000") as mcp:
    msg={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
    assert mcp.post('/mcp',json=msg).status_code==401
    resp=mcp.post('/mcp',json=msg,headers={**H,'Accept':'application/json, text/event-stream'})
    assert resp.status_code==200, resp.text
    assert 'mcp-session-id' not in resp.headers
    call = {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':tool,'arguments':arguments}}
    alice = mcp.post('/mcp',json=call,headers={**H,'Accept':'application/json, text/event-stream'})
    assert alice.status_code == 200, alice.text
    assert '404' in alice.text and 'Bob Private' not in alice.text, alice.text
    bob = mcp.post('/mcp',json=call,headers={**B,'Accept':'application/json, text/event-stream'})
    assert bob.status_code == 200 and '404' not in bob.text, bob.text
    assert not bob.json()['result'].get('isError'), bob.text
print(slug, 'workflow passed')
