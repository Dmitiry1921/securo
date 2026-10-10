from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.services.transfer_schedule_service import occurrences


def test_arbitrary_month_and_day_intervals_clamp_short_month_then_recover():
    plan = SimpleNamespace(start_date=date(2026, 1, 31), end_date=None,
                           frequency='months', interval=3, day_of_month=31)
    assert list(occurrences(plan, date(2026, 1, 1), date(2027, 1, 1))) == [
        date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31), date(2026, 10, 31)]
    plan.frequency, plan.interval, plan.day_of_month = 'days', 9, None
    plan.start_date, plan.end_date = date(2026, 1, 1), date(2026, 1, 19)
    assert list(occurrences(plan, date(2026, 1, 2), date(2026, 2, 1))) == [
        date(2026, 1, 10), date(2026, 1, 19)]


def test_monthly_schedule_skips_first_month_if_start_is_after_scheduled_day():
    plan = SimpleNamespace(start_date=date(2026, 1, 25), end_date=None,
                           frequency='months', interval=1, day_of_month=20)
    assert list(occurrences(plan, date(2026, 1, 1), date(2026, 4, 1))) == [
        date(2026, 2, 20), date(2026, 3, 20)]


async def test_pending_transfer_api_pair_and_confirmation(client, auth_headers):
    from tests.test_pockets import setup_account, fund, state
    from app.core.app_clock import app_today

    source, source_pocket = await setup_account(client, auth_headers)
    destination, destination_pocket = await setup_account(client, auth_headers)
    await fund(client, auth_headers, source, source_pocket, amount='500')
    on = app_today().isoformat()
    response = await client.post('/api/transfer-schedules', headers=auth_headers, json={
        'from_account_id': source, 'to_account_id': destination,
        'from_pocket_id': source_pocket, 'to_pocket_id': destination_pocket,
        'description': 'Quarterly school funds', 'amount': 100, 'currency': 'USD',
        'frequency': 'months', 'interval': 3, 'day_of_month': app_today().day,
        'start_date': on,
    })
    assert response.status_code == 201, response.text
    schedule_id = response.json()['id']
    before = await state(client, auth_headers, source)
    materialized = await client.post(
        f'/api/transfer-schedules/{schedule_id}/materialize', headers=auth_headers,
        json={'date': on},
    )
    assert materialized.status_code == 200, materialized.text
    pair_id = materialized.json()['transfer_pair_id']
    after = await state(client, auth_headers, source)
    assert after['balance'] == before['balance']
    assert Decimal(after['items'][0]['reserved']) == 100
    pending = await client.get('/api/transfer-schedules/pending', headers=auth_headers)
    assert any(p['id'] == pair_id for p in pending.json())
    debit = (await client.get('/api/transactions', headers=auth_headers)).json()
    source_row = next(tx for tx in debit['items'] if tx.get('transfer_pair_id') == pair_id and tx['type'] == 'debit')
    blocked = await client.patch(f"/api/transactions/{source_row['id']}", headers=auth_headers, json={'status': 'posted'})
    assert blocked.status_code == 400

    confirm = {'amount': 95, 'destination_amount': 95, 'date': on}
    for _ in range(2):
        result = await client.post(
            f'/api/transfer-schedules/pairs/{pair_id}/confirm', headers=auth_headers,
            json=confirm,
        )
        assert result.status_code == 200, result.text
    assert Decimal((await state(client, auth_headers, source))['balance']) == 905
    assert Decimal((await state(client, auth_headers, destination))['balance']) == 1095
    assert Decimal((await state(client, auth_headers, destination))['items'][0]['balance']) == 95
