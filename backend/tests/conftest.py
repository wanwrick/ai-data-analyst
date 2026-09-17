"""Test fixtures.

Nothing here talks to Databricks or to Anthropic. The agents take their clients
by constructor argument precisely so the tests can hand them a fake, which is
also what makes the suite runnable in CI with no secrets.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# The app imports as `agents.*` / `services.*`, matching how uvicorn runs it.
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@dataclass
class _Block:
    text: str


@dataclass
class _Response:
    content: list


class FakeMessages:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self._replies.pop(0) if self._replies else ""
        return _Response(content=[_Block(text=reply)])


class FakeClaude:
    """Returns canned replies in order, and records what it was asked."""

    def __init__(self, *replies: str):
        self.messages = FakeMessages(list(replies))

    @property
    def calls(self) -> list[dict]:
        return self.messages.calls


class FakeSQLExecutor:
    """Runs the real validator, returns canned rows."""

    def __init__(self, columns=None, data=None):
        from services.sql_executor import SQLExecutor

        self._real = SQLExecutor(db_client=None)
        self.columns = columns or ["region", "revenue"]
        self.data = data if data is not None else [
            {"region": "East", "revenue": 120},
            {"region": "West", "revenue": 90},
        ]
        self.executed: list[str] = []

    def validate(self, sql: str) -> None:
        self._real.validate(sql)

    async def execute(self, sql: str) -> dict:
        self.executed.append(sql)
        return {
            "columns": self.columns,
            "data": self.data,
            "row_count": len(self.data),
            "truncated": False,
        }


class FakeDatabricksClient:
    def __init__(self, context: str = "Table: medallion_demo.gold.fact_orders"):
        self.context = context

    async def get_schema_context(self, catalog, schema_filter=None) -> str:
        return self.context

    async def list_schemas(self, catalog) -> list:
        return [
            {
                "catalog": catalog,
                "schema_name": "gold",
                "tables": [{"name": "fact_orders", "type": "TABLE", "comment": "orders"}],
            }
        ]

    async def get_table_details(self, full_name) -> dict:
        return {
            "full_name": full_name,
            "columns": [{"name": "order_id", "type": "STRING", "comment": "", "nullable": False}],
            "comment": "",
            "table_type": "TABLE",
            "row_count": 42,
        }


class FakeVectorSearch:
    def __init__(self, documents=None, available=True):
        self._documents = documents or []
        self._available = available

    @property
    def is_available(self) -> bool:
        return self._available

    async def similarity_search(self, query, num_results=5):
        return self._documents


@pytest.fixture
def fake_executor():
    return FakeSQLExecutor()


@pytest.fixture
def fake_db():
    return FakeDatabricksClient()
