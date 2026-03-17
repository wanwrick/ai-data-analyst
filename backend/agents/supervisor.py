"""
Supervisor Agent — Routes user queries to specialized sub-agents.

Uses Claude to classify the intent and dispatch to:
- SQL Analyst: data questions answerable with SQL
- Knowledge Agent: documentation/definition lookups
- Viz Agent: chart and visualization requests
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional
import anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")


@dataclass
class AgentResponse:
    agent_name: str
    summary: str
    sql: Optional[str] = None
    data: Optional[list] = None
    columns: Optional[list] = None
    chart_config: Optional[dict] = None
    sources: Optional[list] = None


ROUTING_PROMPT = """You are a data analyst supervisor agent. Your job is to classify
the user's question into exactly one category:

- SQL_QUERY: Questions about data that can be answered by querying tables.
  Examples: "What was revenue last month?", "Top 10 customers by spend",
  "How many orders were cancelled?"

- KNOWLEDGE: Questions about what data means, business definitions, or documentation.
  Examples: "What does the tier column mean?", "How is LTV calculated?",
  "What tables are in the gold layer?"

- VISUALIZATION: Explicit requests to create, modify, or recommend a chart or dashboard.
  Examples: "Show me a bar chart of revenue by region", "Create a funnel chart",
  "What's the best way to visualize this?"

Respond with ONLY the classification (SQL_QUERY, KNOWLEDGE, or VISUALIZATION).
No explanation."""


class SupervisorAgent:
    def __init__(self, sql_executor, db_client):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.sql_executor = sql_executor
        self.db_client = db_client

    async def classify(self, question: str) -> str:
        """Classify the user's question into an agent category."""
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=20,
            messages=[
                {"role": "user", "content": f"{ROUTING_PROMPT}\n\nUser question: {question}"}
            ],
        )
        classification = response.content[0].text.strip().upper()
        if classification not in ("SQL_QUERY", "KNOWLEDGE", "VISUALIZATION"):
            classification = "SQL_QUERY"  # Default fallback
        logger.info(f"Query classified as: {classification}")
        return classification

    async def route(
        self,
        question: str,
        catalog: str = "medallion_demo",
        schema_filter: Optional[str] = None,
    ) -> AgentResponse:
        """Route the question to the appropriate sub-agent."""
        classification = await self.classify(question)

        if classification == "SQL_QUERY":
            return await self._handle_sql(question, catalog, schema_filter)
        elif classification == "KNOWLEDGE":
            return await self._handle_knowledge(question, catalog)
        else:
            return await self._handle_visualization(question, catalog, schema_filter)

    async def _handle_sql(
        self, question: str, catalog: str, schema_filter: Optional[str]
    ) -> AgentResponse:
        """Generate SQL, execute it, and summarize results."""
        # 1. Get schema context
        schemas = await self.db_client.get_schema_context(catalog, schema_filter)

        # 2. Generate SQL with Claude
        sql_prompt = f"""You are an expert SQL analyst on Databricks (Spark SQL dialect).
Given the following table schemas:

{schemas}

Write a SQL query to answer this question: {question}

Rules:
- Use only SELECT statements (no DDL/DML)
- Use fully qualified table names (catalog.schema.table)
- Prefer the gold layer tables when available
- Include appropriate aggregations and ordering
- Limit results to 1000 rows max
- Return ONLY the SQL query, no explanation."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": sql_prompt}],
        )
        sql = response.content[0].text.strip().strip("```sql").strip("```").strip()

        # 3. Validate and execute
        self.sql_executor.validate(sql)
        result = await self.sql_executor.execute(sql)

        # 4. Summarize with Claude
        summary_prompt = f"""Summarize these SQL query results in 2-3 sentences for a business user.
Question: {question}
Results (first 10 rows): {result['data'][:10]}
Total rows: {len(result['data'])}
Be specific with numbers. Don't mention SQL."""

        summary_response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": summary_prompt}],
        )

        # 5. Recommend chart type
        chart_config = self._recommend_chart(result["columns"], result["data"])

        return AgentResponse(
            agent_name="SQL Analyst",
            summary=summary_response.content[0].text.strip(),
            sql=sql,
            data=result["data"],
            columns=result["columns"],
            chart_config=chart_config,
        )

    async def _handle_knowledge(self, question: str, catalog: str) -> AgentResponse:
        """Search documentation and answer with RAG."""
        # In production: query Databricks Vector Search
        # For MVP: use Claude with catalog metadata as context
        schemas = await self.db_client.get_schema_context(catalog)

        prompt = f"""You are a data documentation expert. Answer this question
using the following schema information:

{schemas}

Question: {question}

Provide a clear, concise answer. If the schema doesn't contain enough
information, say so and suggest what additional documentation might help."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        return AgentResponse(
            agent_name="Knowledge Agent",
            summary=response.content[0].text.strip(),
            sources=[{"type": "unity_catalog", "catalog": catalog}],
        )

    async def _handle_visualization(
        self, question: str, catalog: str, schema_filter: Optional[str]
    ) -> AgentResponse:
        """Generate SQL + chart configuration for visualization requests."""
        # First get data via SQL agent
        sql_result = await self._handle_sql(question, catalog, schema_filter)

        # Then enhance with visualization-specific config
        viz_prompt = f"""Given this data with columns {sql_result.columns},
recommend the best chart type and configuration as JSON:
{{
  "chart_type": "bar|line|pie|scatter|funnel|heatmap",
  "x_axis": "column_name",
  "y_axis": "column_name",
  "color_by": "optional_column",
  "title": "Chart Title"
}}

Question: {question}
Return ONLY the JSON."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": viz_prompt}],
        )

        sql_result.agent_name = "Viz Agent"
        # Parse chart config from response
        import json
        try:
            chart_text = response.content[0].text.strip().strip("```json").strip("```")
            sql_result.chart_config = json.loads(chart_text)
        except (json.JSONDecodeError, IndexError):
            pass

        return sql_result

    def _recommend_chart(self, columns: list, data: list) -> Optional[dict]:
        """Heuristic chart recommendation based on column types."""
        if not columns or not data:
            return None

        # Simple heuristic: if there's a date + numeric column → line chart
        # If there's a category + numeric → bar chart
        date_cols = [c for c in columns if "date" in c.lower() or "time" in c.lower()]
        num_cols = [c for c in columns if isinstance(data[0].get(c), (int, float))]
        cat_cols = [c for c in columns if c not in date_cols and c not in num_cols]

        if date_cols and num_cols:
            return {
                "chart_type": "line",
                "x_axis": date_cols[0],
                "y_axis": num_cols[0],
                "title": f"{num_cols[0]} over time",
            }
        elif cat_cols and num_cols:
            return {
                "chart_type": "bar",
                "x_axis": cat_cols[0],
                "y_axis": num_cols[0],
                "title": f"{num_cols[0]} by {cat_cols[0]}",
            }
        return None
