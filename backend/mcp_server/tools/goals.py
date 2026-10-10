"""Goal lifecycle: the same services and permissions as the UI."""

import uuid

from app.schemas.goal import GoalComplete, GoalRestart, GoalUpdate
from app.services import goal_service
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_tool_workspace
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


def _parameters(schema):
    data_schema = schema.model_json_schema()
    definitions = data_schema.pop("$defs", {})
    return {
        "$defs": definitions,
        "type": "object",
        "properties": {
            "goal_id": {"type": "string", "format": "uuid"},
            "data": data_schema,
            "apply": _APPLY_FIELD,
        },
        "required": ["goal_id", "data"],
        "additionalProperties": False,
    }


async def _action(session, ctx, goal_id, data, apply, kind, schema, service):
    workspace = await resolve_tool_workspace(session, ctx, write=True)
    goal_id = uuid.UUID(goal_id)
    current = await goal_service.get_goal(session, goal_id, workspace.id, ctx.user_id)
    if not current:
        raise ValueError("Goal not found")
    payload = schema.model_validate(data)
    preview = {
        "kind": kind,
        "target": {"id": str(goal_id), "name": current.name, "currency": current.currency},
        "proposed": payload.model_dump(mode="json", exclude_unset=True),
    }
    if isinstance(payload, GoalComplete) and payload.payment:
        funding = await goal_service.goal_funding(session, workspace.id, goal_id)
        pocket = next(
            (p for p in funding["pockets"] if p["id"] == str(payload.payment.pocket_id)), None
        )
        if not pocket:
            raise ValueError("Select a pocket linked to the goal")
        preview["target"]["payment_currency"] = pocket["currency"]
    if _can_apply(ctx, apply):
        try:
            result = await service(session, workspace.id, ctx.user_id, goal_id, payload)
        except Exception:
            await session.rollback()
            raise
        return {**preview, "applied": True, "result": result.model_dump(mode="json")}
    return preview


async def _update(session, workspace_id, user_id, goal_id, payload):
    return await goal_service.update_goal(session, goal_id, workspace_id, user_id, payload)


@tool(
    name="propose_update_goal",
    description=_PROPOSAL_PREFACE
    + "Update a goal including repeat_frequency, target_date, target_amount and pocket tracking. "
    "Use complete/restart actions for cycle transitions.",
    parameters=_parameters(GoalUpdate),
    is_proposal=True,
    tags=["propose", "goals"],
)
async def propose_update_goal(*, session, ctx, goal_id: str, data: dict, apply: bool = False):
    return await _action(session, ctx, goal_id, data, apply, "update_goal", GoalUpdate, _update)


@tool(
    name="propose_complete_goal",
    description=_PROPOSAL_PREFACE
    + "Complete the current cycle using its cycle_number from list_goals. Optional payment records "
    "ONE real posted expense in the selected linked pocket's native currency. Alternatively pass "
    "an existing posted transaction_id already assigned to a linked pocket; never supply both. "
    "Omitting both records completion without spending. Do not infer payment from reaching 100%. "
    "After completion offer propose_restart_goal if repeat_frequency is configured.",
    parameters=_parameters(GoalComplete),
    is_proposal=True,
    tags=["propose", "goals"],
)
async def propose_complete_goal(*, session, ctx, goal_id: str, data: dict, apply: bool = False):
    return await _action(
        session,
        ctx,
        goal_id,
        data,
        apply,
        "complete_goal",
        GoalComplete,
        goal_service.complete_goal,
    )


@tool(
    name="propose_restart_goal",
    description=_PROPOSAL_PREFACE
    + "Start the next cycle of a completed repeating goal. Pass the completed cycle_number. "
    "Advances one scheduled period, keeps pocket leftovers, resets manual progress, preserves history. "
    "Does not create a payment or move bank money.",
    parameters=_parameters(GoalRestart),
    is_proposal=True,
    tags=["propose", "goals"],
)
async def propose_restart_goal(*, session, ctx, goal_id: str, data: dict, apply: bool = False):
    return await _action(
        session, ctx, goal_id, data, apply, "restart_goal", GoalRestart, goal_service.restart_goal
    )


@tool(
    name="get_goal_history",
    description="Read completed goal cycles and their payment transaction IDs.",
    parameters={
        "type": "object",
        "properties": {"goal_id": {"type": "string", "format": "uuid"}},
        "required": ["goal_id"],
        "additionalProperties": False,
    },
    tags=["read", "goals"],
)
async def get_goal_history(*, session, ctx, goal_id: str):
    workspace = await resolve_tool_workspace(session, ctx)
    rows = await goal_service.completion_history(session, workspace.id, uuid.UUID(goal_id))
    return {"items": [row.model_dump(mode="json") for row in rows], "total": len(rows)}


@tool(
    name="get_goal_funding",
    description="Read linked pockets, native available balances, and the latest 100 posted expenses eligible to complete this goal without creating duplicate spending.",
    parameters={
        "type": "object",
        "properties": {"goal_id": {"type": "string", "format": "uuid"}},
        "required": ["goal_id"],
        "additionalProperties": False,
    },
    tags=["read", "goals"],
)
async def get_goal_funding(*, session, ctx, goal_id: str):
    workspace = await resolve_tool_workspace(session, ctx)
    return await goal_service.goal_funding(session, workspace.id, uuid.UUID(goal_id))
