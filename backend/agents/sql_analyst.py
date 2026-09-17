"""SQL Analyst agent — natural language to governed SQL and back.

The loop is: read the schema, write the query, refuse it if it is not read-only,
run it, then say what came back in business language. The validation step is not
a formality. The model writes the SQL, so the model is untrusted input.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import AgentResponse, DEFAULT_MODEL, build_client, first_text, strip_fences

logger = logging.getLogger(__name__)

MAX_SUMMARY_ROWS = 10

SQL_PROMPT = """You are an expert SQL analyst working on Databricks (Spark SQL dialect).

Given the following table schemas:

{schemas}

Write a SQL query that answers this question: {question}

Rules:
- SELECT statements only. No DDL and no DML.
- Fully qualify every table as catalog.schema.table.
- Prefer gold layer tables when they can answer the question.
- Aggregate and order so the result is directly readable.
- Return at most 1000 rows.
- Return ONLY the SQL query, with no explanation and no commentary."""

SUMMARY_PROMPT = """Summarize these query results for a business reader in 2 to 3 sentences.

Question: {question}
Columns: {columns}
First {shown} rows: {rows}
Total rows returned: {total}

Be specific about the numbers. Do not mention SQL, tables, or queries.
If the result is empty, say so plainly and suggest what might explain it."""


class SQLAnalystAgent:
    """Answers questions that the warehouse can answer."""

    name = "SQL Analyst"

    def __init__(self, sql_executor, db_client, client=None, model: str = DEFAULT_MODEL):
        self.sql_executor = sql_executor
        self.db_client = db_client
        self.client = client or build_client()
        self.model = model

    async def analyze(
        self,
        question: str,
        catalog: str = "medallion_demo",
        schema_filter: Optional[str] = None,
    ) -> AgentResponse:
        schemas = await self.db_client.get_schema_context(catalog, schema_filter)
        sql = await self.generate_sql(question, schemas)

        # Validation happens before execution, always. The model wrote this.
        self.sql_executor.validate(sql)
        result = await self.sql_executor.execute(sql)

        summary = await self.summarize(
            question=question,
            columns=result["columns"],
            rows=result["data"][:MAX_SUMMARY_ROWS],
            total=len(result["data"]),
        )

        return AgentResponse(
            agent_name=self.name,
            summary=summary,
            sql=sql,
            data=result["data"],
            columns=result["columns"],
            truncated=result.get("truncated", False),
        )

    async def generate_sql(self, question: str, schemas: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": SQL_PROMPT.format(schemas=schemas, question=question),
                }
            ],
        )
        sql = strip_fences(first_text(response))
        if not sql:
            raise ValueError("The model returned no SQL for this question")
        logger.info("Generated SQL: %s", sql.replace("\n", " ")[:200])
        return sql

    async def summarize(
        self, question: str, columns: list[str], rows: list[dict], total: int
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(
                        question=question,
                        columns=", ".join(columns) or "none",
                        shown=len(rows),
                        rows=rows,
                        total=total,
                    ),
                }
            ],
        )
        return first_text(response).strip() or "The query ran but returned no rows."
