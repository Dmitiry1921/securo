# ruff: noqa: F811 -- pytest fixture imports are deliberately shadowed by parameters
"""PostgreSQL row locks prevent two workers materializing the same occurrence."""
import asyncio
import uuid
from datetime import date
from sqlalchemy import select
from app.models.transaction import Transaction
from app.services.transfer_schedule_service import materialize
from tests.test_reports_postgres import session, client, clean_db  # noqa: F401
from tests.test_scheduled_transfers import create_plan


async def test_concurrent_materialization(client,auth_headers,session,postgres_sessions):
    _,_,_,_,plan=await create_plan(client,auth_headers)
    async def run():
        async with postgres_sessions() as connection:
            from app.models.transfer_schedule import TransferSchedule
            p=await connection.get(TransferSchedule,uuid.UUID(plan['id']))
            pair=await materialize(connection,p.workspace_id,p.id,date.fromisoformat(plan['start_date']))
            return pair[0].transfer_pair_id
    pairs=await asyncio.gather(run(),run())
    assert pairs[0]==pairs[1]
    rows=list((await session.scalars(select(Transaction).where(Transaction.transfer_pair_id==pairs[0]))).all())
    assert len(rows)==2


async def test_f004_migration_round_trip(postgres_sessions):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('transfer_migration', Path(__file__).parents[1] / 'alembic/versions/f004_scheduled_transfers.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with postgres_sessions() as connection:
        async_conn = await connection.connection()
        def run(conn):
            schema = conn.get_execution_options()['schema_translate_map'][None]
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                assert 'transfer_schedules' not in inspect(conn).get_table_names(schema=schema)
                migration.upgrade()
                assert 'transfer_schedules' in inspect(conn).get_table_names(schema=schema)
                constraints = inspect(conn).get_unique_constraints('transactions', schema=schema)
                assert any(c['name']=='uq_transfer_occurrence_leg' for c in constraints)
        await async_conn.run_sync(run)
        await connection.commit()
