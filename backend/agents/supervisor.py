"""Supervisor agent — classifies the question and dispatches it.

The supervisor owns routing and nothing else. Each sub-agent owns its own
prompting and its own failure modes, which is what keeps a change to chart
selection from touching SQL generation.

Routing is one cheap call. When it is ambiguous the answer is SQL_QUERY,
because a wrong data answer is visible and a wrong definition answer is not.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import AgentResponse, DEFAULT_MODEL, build_client, first_text
from .knowledge_agent import KnowledgeAgent
from .sql_analyst import SQLAnalystAgent
from .viz_agent import VizAgent, recommend_chart

logger = logging.getLogger(__name__)

SQL_QUERY = "SQL_QUERY"
KNOWLEDGE = "KNOWLEDGE"
VISUALIZATION = "VISUALIZATION"
CLASSIFICATIONS = {SQL_QUERY, KNOWLEDGE, VISUALIZATION}

ROUTING_PROMPT = """You are a data analyst supervisor. Classify the question into
exactly one category.

- SQL_QUERY: answerable by querying tables.
  "What was revenue last month?" / "Top 10 customers by spend"
  / "How many orders were cancelled?"

- KNOWLEDGE: about what the data means, how a metric is defined, or what exists.
  "What does the tier column mean?" / "How is LTV calculated?"
  / "What tables are in the gold layer?"

- VISUALIZATION: explicitly asks for a chart or asks how to visualize something.
  "Show me a bar chart of revenue by region" / "Create a funnel chart"
  / "What is the best way to visualize this?"

A question that asks for data without naming a chart is SQL_QUERY.
Respond with ONLY the category name."""


class SupervisorAgent:
    def __init__(
        self,
        sql_executor,
        db_client,
        vector_search=None,
        client=None,
        model: str = DEFAULT_MODEL,
    ):
        self.client = client or build_client()
        self.model = model
        self.sql_analyst = SQLAnalystAgent(sql_executor, db_client, client=self.client, model=model)
        self.knowledge_agent = KnowledgeAgent(
            db_client, vector_search=vector_search, client=self.client, model=model
        )
        self.viz_agent = VizAgent(self.sql_analyst, client=self.client, model=model)

    async def classify(self, question: str) -> str:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=20,
                messages=[
                    {"role": "user", "content": f"{ROUTING_PROMPT}\n\nUser question: {question}"}
                ],
            )
            label = first_text(response).strip().upper()
        except Exception as exc:
            logger.warning("Routing call failed (%s); defaulting to %s", exc, SQL_QUERY)
            return SQL_QUERY

        # Models occasionally answer in a sentence. Look for the label in it.
        for candidate in CLASSIFICATIONS:
            if candidate in label:
                logger.info("Routed to %s", candidate)
                return candidate

        logger.info("Unrecognized routing label %r; defaulting to %s", label, SQL_QUERY)
        return SQL_QUERY

    async def route(
        self,
        question: str,
        catalog: str = "medallion_demo",
        schema_filter: Optional[str] = None,
    ) -> AgentResponse:
        classification = await self.classify(question)

        if classification == KNOWLEDGE:
            return await self.knowledge_agent.search(question, catalog)

        if classification == VISUALIZATION:
            return await self.viz_agent.recommend(question, catalog, schema_filter)

        result = await self.sql_analyst.analyze(question, catalog, schema_filter)
        # Every data answer gets a chart suggestion from the cheap heuristic.
        # The model is only consulted when a chart was the actual request.
        result.chart_config = recommend_chart(result.columns or [], result.data or [])
        return result
