"""Auto-imports all tool modules so they register themselves into the
shared registry on startup.
"""
from mcp_server.tools import (  # noqa: F401
    transactions,
    imports,
    transfers,
    accounts,
    pockets,
    categories,
    payees,
    budgets,
    reports,
    search,
    aggregate,
    proposals,
    knowledge,
    lifecycle,
    goals,
    groups,
    rules,
)
