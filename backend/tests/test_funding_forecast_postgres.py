# ruff: noqa: F811 -- fixtures intentionally shadow imported PostgreSQL fixtures
"""Isolated PostgreSQL checks: retries, overlapping concurrent rules, migration."""

import asyncio
import uuid
from pathlib import Path
import importlib.util
from sqlalchemy import select, func, inspect, text
from app.models.account import Account
from app.models.forecast import ForecastSetting
from app.services.forecast_service import save_setting
from tests.test_reports_postgres import session, client, clean_db  # noqa: F401
from tests.test_pockets import setup_account


async def payload(client, auth_headers, session):
    aid, _ = await setup_account(client, auth_headers)
    cat = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Everyday", "icon": "utensils", "color": "#F59E0B"},
    )
    assert cat.status_code == 201, cat.text
    account = await session.get(Account, uuid.UUID(aid))
    return account.workspace_id, dict(
        id=str(uuid.uuid4()),
        name="Food",
        account_id=aid,
        amount="100",
        currency="USD",
        category_ids=[cat.json()["id"]],
        start_date="2026-01-01",
    )


async def test_concurrent_same_uuid_is_one_setting(
    client, auth_headers, session, postgres_sessions
):
    wid, data = await payload(client, auth_headers, session)

    async def save():
        async with postgres_sessions() as connection:
            return await save_setting(connection, wid, "expense_rule", data)

    rows = await asyncio.gather(save(), save())
    assert rows[0].id == rows[1].id
    assert await session.scalar(select(func.count()).select_from(ForecastSetting)) == 1


async def test_concurrent_overlapping_matchers_cannot_both_commit(
    client, auth_headers, session, postgres_sessions
):
    wid, data = await payload(client, auth_headers, session)

    async def save(id):
        async with postgres_sessions() as connection:
            return await save_setting(connection, wid, "expense_rule", {**data, "id": id})

    result = await asyncio.gather(
        save(str(uuid.uuid4())), save(str(uuid.uuid4())), return_exceptions=True
    )
    assert sum(isinstance(x, ValueError) for x in result) == 1, result
    assert await session.scalar(select(func.count()).select_from(ForecastSetting)) == 1


async def test_f007_round_trip_only_planning_table(postgres_sessions):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        "funding_migration", Path(__file__).parents[1] / "alembic/versions/f007_funding_forecast.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with postgres_sessions() as connection:
        conn = await connection.connection()

        def check(sync):
            schema = sync.get_execution_options()["schema_translate_map"][None]
            sync.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            before = set(inspect(sync).get_table_names(schema=schema))
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                assert set(inspect(sync).get_table_names(schema=schema)) == before - {
                    "forecast_settings"
                }
                migration.upgrade()
                assert set(inspect(sync).get_table_names(schema=schema)) == before

        await conn.run_sync(check)
        await connection.commit()
