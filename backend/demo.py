"""Demo mode: run the whole app with no Databricks workspace and no API key.

    uvicorn demo:app --port 8000

Every service is replaced by a fixture that returns the same shapes the real
ones do, so the frontend, the routing, the chart heuristic and the SQL
validator all run for real. Only the two network calls are faked.

This exists so the app can be evaluated without provisioning anything. It is
not a test double: `python -m pytest` uses its own fakes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager

from agents.supervisor import SupervisorAgent
from app import app
from services.sql_executor import SQLExecutor
from services.vector_search import Document

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CATALOG = "medallion_demo"

SAMPLE_TABLES = {
    "gold": {
        "fact_orders": [
            ("order_id", "STRING", "One row per confirmed order"),
            ("customer_id", "STRING", "Joins to dim_customers"),
            ("order_date", "TIMESTAMP", "When the order was confirmed"),
            ("region", "STRING", "Billing region"),
            ("revenue", "DECIMAL(10,2)", "Net of refunds"),
        ],
        "dim_customers": [
            ("customer_id", "STRING", "Surrogate key"),
            ("tenure_months", "INT", "Months since first order"),
            ("churn_score", "DOUBLE", "Rolling 90-day model output, 0 to 1"),
        ],
        "agg_daily_revenue": [
            ("order_date", "DATE", "Calendar day"),
            ("revenue", "DECIMAL(12,2)", "Sum of net revenue"),
        ],
    },
    "silver": {
        "silver_orders": [("order_id", "STRING", "Validated order events")],
    },
}

REVENUE_BY_REGION = {
    "columns": ["region", "revenue"],
    "data": [
        {"region": "East", "revenue": 412_900},
        {"region": "West", "revenue": 388_140},
        {"region": "Central", "revenue": 201_775},
        {"region": "North", "revenue": 96_430},
    ],
}

REVENUE_BY_DAY = {
    "columns": ["order_date", "revenue"],
    "data": [
        {"order_date": f"2024-05-{day:02d}", "revenue": 11_000 + (day * 940) % 7_300}
        for day in range(1, 22)
    ],
}

CHURN_RISK = {
    "columns": ["tenure_band", "accounts_at_risk"],
    "data": [
        {"tenure_band": "0-6 months", "accounts_at_risk": 88},
        {"tenure_band": "6-12 months", "accounts_at_risk": 214},
        {"tenure_band": "12-24 months", "accounts_at_risk": 71},
        {"tenure_band": "24+ months", "accounts_at_risk": 39},
    ],
}

GLOSSARY = [
    Document(
        title="Glossary: churn_score",
        content=(
            "churn_score is the rolling 90-day output of the retention model, "
            "scored nightly on dim_customers. It ranges from 0 to 1. Anything "
            "above 0.7 is treated as high risk. Owned by Customer Analytics; "
            "last revised in March."
        ),
        source_uri="docs/glossary/churn_score.md",
        score=0.94,
    ),
    Document(
        title="Runbook: retention model refresh",
        content=(
            "The retention model refreshes at 02:00 America/Toronto. A missed "
            "refresh leaves churn_score stale rather than null, so check "
            "freshness before reading the score."
        ),
        source_uri="docs/runbooks/retention-refresh.md",
        score=0.71,
    ),
]


def _pick_result(question: str) -> dict:
    """Route the canned data by keyword. Good enough to exercise the UI."""
    lowered = question.lower()
    if "churn" in lowered or "risk" in lowered:
        return CHURN_RISK
    if any(word in lowered for word in ("trend", "over time", "daily", "day", "month")):
        return REVENUE_BY_DAY
    return REVENUE_BY_REGION


class DemoDatabricksClient:
    async def get_schema_context(self, catalog=CATALOG, schema_filter=None) -> str:
        parts = []
        for schema, tables in SAMPLE_TABLES.items():
            if schema_filter and schema != schema_filter:
                continue
            for table, columns in tables.items():
                rendered = ", ".join(f"{name} ({type_})" for name, type_, _ in columns)
                parts.append(f"Table: {catalog}.{schema}.{table}\n  Columns: {rendered}\n")
        return "\n".join(parts)

    async def list_schemas(self, catalog=CATALOG) -> list:
        return [
            {
                "catalog": catalog,
                "schema_name": schema,
                "tables": [
                    {"name": table, "type": "TABLE", "comment": columns[0][2]}
                    for table, columns in tables.items()
                ],
            }
            for schema, tables in SAMPLE_TABLES.items()
        ]

    async def get_table_details(self, full_name: str) -> dict:
        _, schema, table = full_name.split(".")
        columns = SAMPLE_TABLES.get(schema, {}).get(table, [])
        return {
            "full_name": full_name,
            "columns": [
                {"name": n, "type": t, "comment": c, "nullable": True} for n, t, c in columns
            ],
            "comment": "",
            "table_type": "TABLE",
            "row_count": len(columns) * 1000,
        }


class DemoSQLExecutor(SQLExecutor):
    """Real validation, canned results. A write still gets refused."""

    def __init__(self):
        super().__init__(db_client=None)
        self.last_question = ""

    async def execute(self, sql: str) -> dict:
        self.validate(sql)
        result = _pick_result(self.last_question or sql)
        await asyncio.sleep(0.25)  # so the pending state is visible
        return {**result, "row_count": len(result["data"]), "truncated": False}


class DemoVectorSearch:
    is_available = True

    async def similarity_search(self, query: str, num_results: int = 5):
        lowered = query.lower()
        return [d for d in GLOSSARY if any(t in lowered for t in ("churn", "score", "mean", "defin"))]


class DemoClaude:
    """Answers the three question shapes without calling a model."""

    def __init__(self, executor: DemoSQLExecutor):
        self.executor = executor
        self.messages = self

    def create(self, *, messages, max_tokens, **kwargs):
        prompt = messages[0]["content"]
        return _Reply(self._answer(prompt, max_tokens))

    def _answer(self, prompt: str, max_tokens: int) -> str:
        question = _extract_question(prompt)

        if "Classify the question" in prompt:
            lowered = question.lower()
            if any(w in lowered for w in ("chart", "graph", "plot", "visuali")):
                return "VISUALIZATION"
            if any(w in lowered for w in ("mean", "definition", "defined", "how is", "what is a")):
                return "KNOWLEDGE"
            return "SQL_QUERY"

        if "Write a SQL query" in prompt:
            self.executor.last_question = question
            result = _pick_result(question)
            columns = ", ".join(result["columns"])
            return (
                f"SELECT {columns}\n"
                f"FROM {CATALOG}.gold.fact_orders\n"
                f"GROUP BY {result['columns'][0]}\n"
                f"ORDER BY {result['columns'][1]} DESC"
            )

        if "Recommend a chart" in prompt:
            return '{"chart_type": "bar", "x_axis": "region", "y_axis": "revenue", "title": "Revenue by region"}'

        if "documentation excerpts" in prompt:
            return (
                "churn_score is the rolling 90-day output of the retention model, "
                "scored nightly and ranging from 0 to 1, per Glossary: churn_score. "
                "Anything above 0.7 is treated as high risk."
            )

        if "Unity Catalog schema below" in prompt:
            return (
                "The schema does not document that column. Based on the name alone "
                "it looks like a score, but that is an inference, not a definition."
            )

        if "Summarize these query results" in prompt:
            return _summarize(question)

        return "Demo mode response."


def _extract_question(prompt: str) -> str:
    for pattern in (r"User question:\s*(.+)", r"question:\s*(.+)", r"Question:\s*(.+)"):
        match = re.search(pattern, prompt)
        if match:
            return match.group(1).strip().splitlines()[0]
    return ""


def _summarize(question: str) -> str:
    result = _pick_result(question)
    if result is CHURN_RISK:
        return (
            "412 accounts score as high risk. The 6-to-12-month tenure band holds "
            "214 of them, more than the other three bands combined."
        )
    if result is REVENUE_BY_DAY:
        return (
            "Revenue held between 11.0K and 18.2K a day across the 21 days, with "
            "no sustained trend in either direction."
        )
    return (
        "East leads at 412.9K, just ahead of West at 388.1K. North trails at 96.4K, "
        "under a quarter of East."
    )


class _Block:
    def __init__(self, text: str):
        self.text = text


class _Reply:
    def __init__(self, text: str):
        self.content = [_Block(text)]


def _install(_app):
    executor = DemoSQLExecutor()
    db = DemoDatabricksClient()
    vector_search = DemoVectorSearch()
    _app.state.db_client = db
    _app.state.sql_executor = executor
    _app.state.vector_search = vector_search
    _app.state.supervisor = SupervisorAgent(
        executor, db, vector_search=vector_search, client=DemoClaude(executor)
    )
    logger.info("Demo mode: no Databricks workspace and no API key in use")


@asynccontextmanager
async def _demo_lifespan(_app):
    """Swap in the demo services instead of constructing real clients."""
    _install(_app)
    yield


# Override the app's own lifespan. Everything else about the app is unchanged.
app.router.lifespan_context = _demo_lifespan

__all__ = ["app"]
