"""Family flows: forecasts do not post money or invent income."""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.models.fx_rate import FxRate
from app.models.transfer_schedule import TransferSchedule
from app.services.transfer_schedule_service import occurrences, generate_pending, projections
from tests.test_pockets import setup_account, fund, state


def test_intervals_and_month_end():
    p = SimpleNamespace(start_date=date(2026,1,31), end_date=None, frequency='months', interval=1, day_of_month=31)
    assert list(occurrences(p,date(2026,1,1),date(2026,5,1))) == [date(2026,1,31),date(2026,2,28),date(2026,3,31),date(2026,4,30)]
    p.interval=3
    assert list(occurrences(p,date(2026,1,1),date(2027,1,1))) == [date(2026,1,31),date(2026,4,30),date(2026,7,31),date(2026,10,31)]
    p.frequency,p.interval,p.day_of_month='days',9,None
    p.start_date,p.end_date=date(2026,1,1),date(2026,1,19)
    assert list(occurrences(p,date(2026,1,2),date(2026,2,1))) == [date(2026,1,10),date(2026,1,19)]
    p.frequency,p.interval,p.day_of_month='months',1,20
    p.start_date,p.end_date=date(2026,1,25),None
    assert list(occurrences(p,date(2026,1,1),date(2026,4,1))) == [date(2026,2,20),date(2026,3,20)]


async def create_plan(client, headers):
    a,p=await setup_account(client,headers)
    b,q=await setup_account(client,headers)
    await fund(client,headers,a,p)
    data=dict(from_account_id=a,to_account_id=b,from_pocket_id=p,to_pocket_id=q,description='School transfer',amount='100',currency='USD',frequency='months',interval=3,day_of_month=app_today().day,start_date=str(app_today()))
    response=await client.post('/api/transfer-schedules',headers=headers,json=data)
    assert response.status_code==201,response.text
    return a,p,b,q,response.json()


async def test_materialize_confirm_retry_and_generation(client,auth_headers,session,test_user):
    a,p,b,q,plan=await create_plan(client,auth_headers)
    before=await state(client,auth_headers,a)
    for _ in range(2):
        r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':plan['start_date']})
        assert r.status_code==200,r.text
    pair=r.json()
    assert (await state(client,auth_headers,a))['balance']==before['balance']
    assert Decimal((await state(client,auth_headers,a))['items'][0]['reserved'])==100
    assert Decimal((await state(client,auth_headers,b))['items'][0]['balance'])==0
    rows=list((await session.scalars(select(Transaction).where(Transaction.transfer_pair_id==uuid.UUID(pair['transfer_pair_id'])))).all())
    assert len(rows)==2
    assert not await projections(session,rows[0].workspace_id,app_today(),app_today()+timedelta(days=1),'USD')
    actual=dict(amount=90,destination_amount=90,date=str(app_today()-timedelta(days=1)))
    r=await client.post(f"/api/transfer-schedules/pairs/{pair['transfer_pair_id']}/confirm",headers=auth_headers,json=actual)
    assert r.status_code==400
    actual['date']=str(app_today())
    for _ in range(2):
        r=await client.post(f"/api/transfer-schedules/pairs/{pair['transfer_pair_id']}/confirm",headers=auth_headers,json=actual)
        assert r.status_code==200,r.text
    assert Decimal((await state(client,auth_headers,a))['balance'])==910
    assert Decimal((await state(client,auth_headers,b))['balance'])==1090
    assert Decimal((await state(client,auth_headers,a))['items'][0]['reserved'])==0
    r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':plan['start_date']})
    assert r.json()['transfer_pair_id']==pair['transfer_pair_id']
    assert await generate_pending(session,test_user.id)==0


async def test_projections_pnl_and_goal_progress(client,auth_headers,session):
    a,p,b,q,plan=await create_plan(client,auth_headers)
    r=await client.post('/api/goals',headers=auth_headers,json=dict(name='School',target_amount=600,currency='USD',tracking_type='pockets'))
    assert r.status_code==201,r.text
    goal=r.json()['id']
    for pocket in (p,q):
        assert (await client.patch(f'/api/pockets/{pocket}',headers=auth_headers,json={'goal_id':goal})).status_code==200
    ws=(await session.get(TransferSchedule,uuid.UUID(plan['id']))).workspace_id
    projected=await projections(session,ws,app_today(),app_today()+timedelta(days=1),'USD')
    assert len(projected)==2
    assert projected[0]['transfer_pair_id']==projected[1]['transfer_pair_id']
    assert sum(r['amount_primary']*(1 if r['type']=='credit' else -1) for r in projected)==0
    assert len(await projections(session,ws,app_today(),app_today()+timedelta(days=1),'USD',[uuid.UUID(b)]))==1
    from app.services.dashboard_service import _get_recurring_projections
    assert not await _get_recurring_projections(session,ws,app_today(),app_today()+timedelta(days=1))
    r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':str(app_today())})
    assert r.status_code==200,r.text
    goals=(await client.get('/api/goals',headers=auth_headers)).json()
    assert Decimal(next(g for g in goals if g['id']==goal)['current_amount'])==600


async def test_cross_currency_and_immutable_occurrence(client,auth_headers,session):
    a,p=await setup_account(client,auth_headers)
    b,q=await setup_account(client,auth_headers,currency='IDR')
    session.add(FxRate(base_currency='USD',quote_currency='IDR',date=app_today(),rate=Decimal('16000'),source='test'))
    await session.commit()
    on=app_today()
    data=dict(from_account_id=a,to_account_id=b,from_pocket_id=p,to_pocket_id=q,description='Kindergarten',amount=21000000,currency='IDR',frequency='months',interval=3,day_of_month=on.day,start_date=str(on))
    r=await client.post('/api/transfer-schedules',headers=auth_headers,json=data)
    assert r.status_code==201,r.text
    plan=r.json()
    r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':str(on)})
    assert r.status_code==200,r.text
    pair=r.json()['transfer_pair_id']
    r=await client.post(f'/api/transfer-schedules/pairs/{pair}/confirm',headers=auth_headers,json=dict(amount=1320,destination_amount=21000000,date=str(on)))
    assert r.status_code==200,r.text
    rows=list((await session.scalars(select(Transaction).where(Transaction.transfer_pair_id==uuid.UUID(pair)))).all())
    assert {r.status for r in rows}=={'posted'}
    assert next(r for r in rows if r.type=='debit').amount==1320
    assert next(r for r in rows if r.type=='credit').amount==21000000
    assert rows[0].amount_primary==rows[1].amount_primary
    for row in rows:
        row.date=on+timedelta(days=1)
    await session.commit()
    assert not await projections(session,rows[0].workspace_id,on,on+timedelta(days=1),'USD')


@pytest.mark.parametrize('patch',[{'interval':0},{'from_account_id':str(uuid.uuid4())},{'currency':'EUR'},{'frequency':'days','day_of_month':20}])
async def test_invalid_endpoints_and_rules(client,auth_headers,patch):
    _,_,_,_,plan=await create_plan(client,auth_headers)
    data={k:v for k,v in plan.items() if k!='id'}
    data.update(patch)
    r=await client.put(f"/api/transfer-schedules/{plan['id']}",headers=auth_headers,json=data)
    assert r.status_code in (400,422)


async def test_filtered_calendar_and_cash_flow(client,auth_headers,session,test_user):
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()
    a,p,b,q,plan=await create_plan(client,auth_headers)
    on=app_today()+timedelta(days=2)
    data={k:v for k,v in plan.items() if k!='id'}
    data.update(start_date=str(on),frequency='days',interval=10,day_of_month=None)
    r=await client.put(f"/api/transfer-schedules/{plan['id']}",headers=auth_headers,json=data)
    assert r.status_code==200,r.text
    ws=(await session.get(TransferSchedule,uuid.UUID(plan['id']))).workspace_id
    from app.services.transaction_calendar_service import get_transaction_calendar
    cal=await get_transaction_calendar(session,ws,test_user.id,month=on.replace(day=1),account_ids=[uuid.UUID(b)])
    day=next(d for d in cal.days if d.date==on)
    assert day.projected_transfer_net>0
    assert day.income==0 and day.expense==0
    from app.services.report_service import get_cash_flow_report
    # Fixtures default to BRL; equal USD legs still cancel in that currency.
    before=await get_cash_flow_report(session,ws,test_user.id,months=1,account_ids=[uuid.UUID(b)])
    point=next(d for d in before.trend if d.date==str(on))
    prior=next(d for d in before.trend if d.date==str(on-timedelta(days=1)))
    assert point.value>prior.value
    assert point.breakdowns['inflow']==0 and point.breakdowns['outflow']==0
    r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':str(on)})
    assert r.status_code==200,r.text
    after=await get_cash_flow_report(session,ws,test_user.id,months=1,account_ids=[uuid.UUID(b)])
    assert next(d for d in after.trend if d.date==str(on)).value==point.value


async def test_native_transfer_pending_status_cascades(client,auth_headers):
    a,_=await setup_account(client,auth_headers)
    b,_=await setup_account(client,auth_headers)
    r=await client.post('/api/transactions/transfer',headers=auth_headers,json=dict(from_account_id=a,to_account_id=b,amount=100,date=str(app_today()),description='Weekly food',status='pending'))
    assert r.status_code==201,r.text
    pair=r.json()
    assert pair['debit']['status']==pair['credit']['status']=='pending'
    r=await client.patch(f"/api/transactions/{pair['credit']['id']}",headers=auth_headers,json={'status':'posted'})
    assert r.status_code==200,r.text
    assert (await client.get(f"/api/transactions/{pair['debit']['id']}",headers=auth_headers)).json()['status']=='posted'


async def test_mcp_preview_and_apply(client,auth_headers,session,test_user,test_workspace):
    from mcp_server.auth import CallContext
    from mcp_server.tools.transfers import propose_transfer_schedule,list_transfer_schedules,propose_transfer_occurrence,list_pending_transfers,propose_confirm_transfer
    a,_,b,_,plan=await create_plan(client,auth_headers)
    ctx=CallContext(user_id=test_user.id,workspace_id=test_workspace.id,external=True)
    data={k:v for k,v in plan.items() if k!='id'}
    data['description']='MCP transfer'
    before=await list_transfer_schedules(session=session,ctx=ctx)
    result=await propose_transfer_schedule(session=session,ctx=ctx,data=data,apply=False)
    assert 'applied' not in result
    assert len(await list_transfer_schedules(session=session,ctx=ctx))==len(before)
    result=await propose_transfer_schedule(session=session,ctx=ctx,data=data,apply=True)
    assert result['applied']
    result=await propose_transfer_occurrence(session=session,ctx=ctx,schedule_id=result['result']['id'],date=data['start_date'],apply=True)
    assert result['applied']
    assert len(await list_pending_transfers(session=session,ctx=ctx))==1
    result=await propose_confirm_transfer(session=session,ctx=ctx,transfer_pair_id=result['transfer_pair_id'],data=dict(amount=100,destination_amount=100,date=data['start_date']),apply=True)
    assert result['applied']
    assert await list_pending_transfers(session=session,ctx=ctx)==[]


async def test_ignored_internal_transfer_does_not_inflate_goal(client,auth_headers,session):
    a,p,b,q,plan=await create_plan(client,auth_headers)
    goal=(await client.post('/api/goals',headers=auth_headers,json=dict(name='Ignore test',target_amount=600,currency='USD',tracking_type='pockets'))).json()['id']
    for pocket in (p,q):
        await client.patch(f'/api/pockets/{pocket}',headers=auth_headers,json={'goal_id':goal})
    r=await client.post(f"/api/transfer-schedules/{plan['id']}/materialize",headers=auth_headers,json={'date':str(app_today())})
    for tx_id in r.json()['transaction_ids']:
        tx=await session.get(Transaction,uuid.UUID(tx_id))
        tx.is_ignored=True
    await session.commit()
    goals=(await client.get('/api/goals',headers=auth_headers)).json()
    assert Decimal(next(g for g in goals if g['id']==goal)['current_amount'])==600
