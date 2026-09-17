"""Agent tests: routing, chart selection, and the parsing around model output.

Everything a model returns is treated as untrusted here. A hallucinated column
name in a chart config is the failure that reaches the user as a blank panel,
so it gets its own test.
"""

from __future__ import annotations

import pytest
from conftest import FakeClaude, FakeDatabricksClient, FakeSQLExecutor, FakeVectorSearch

from agents.base import parse_json_object, strip_fences
from agents.knowledge_agent import KnowledgeAgent
from agents.sql_analyst import SQLAnalystAgent
from agents.supervisor import KNOWLEDGE, SQL_QUERY, VISUALIZATION, SupervisorAgent
from agents.viz_agent import classify_columns, recommend_chart, validate_chart
from services.vector_search import Document

# --- base helpers ---


def test_strip_fences_removes_a_sql_fence():
    assert strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_keeps_trailing_backticked_identifier():
    """`.strip('```sql')` used to eat characters off the end of the query."""
    assert strip_fences("SELECT `order`") == "SELECT `order`"


def test_strip_fences_leaves_plain_text_alone():
    assert strip_fences("  SELECT 1  ") == "SELECT 1"


def test_parse_json_object_handles_a_preamble():
    parsed = parse_json_object('Sure! Here you go:\n{"chart_type": "bar"}')
    assert parsed == {"chart_type": "bar"}


def test_parse_json_object_returns_none_for_prose():
    assert parse_json_object("I could not decide on a chart.") is None


# --- column classification ---


def test_numeric_column_is_detected_past_a_leading_null():
    """Reading only row one would type this column as categorical."""
    rows = [{"revenue": None}, {"revenue": 42}]
    assert classify_columns(["revenue"], rows)["numeric"] == ["revenue"]


def test_booleans_are_not_numbers():
    rows = [{"is_active": True}]
    assert classify_columns(["is_active"], rows)["categorical"] == ["is_active"]


def test_date_like_names_are_temporal():
    rows = [{"order_month": "2024-05", "total": 1}]
    kinds = classify_columns(["order_month", "total"], rows)
    assert kinds["temporal"] == ["order_month"]


# --- chart heuristics ---


def test_time_series_becomes_a_line():
    rows = [{"order_date": f"2024-05-{d:02d}", "revenue": d} for d in range(1, 6)]
    assert recommend_chart(["order_date", "revenue"], rows)["chart_type"] == "line"


def test_category_and_measure_become_a_bar():
    rows = [{"region": "East", "revenue": 10}, {"region": "West", "revenue": 20}]
    assert recommend_chart(["region", "revenue"], rows)["chart_type"] == "bar"


def test_too_many_categories_fall_back_to_a_table():
    """Forty bars is not a chart, it is a smear."""
    rows = [{"sku": f"s{i}", "units": i} for i in range(40)]
    assert recommend_chart(["sku", "units"], rows)["chart_type"] == "table"


def test_a_single_statistic_gets_no_chart():
    assert recommend_chart(["revenue"], [{"revenue": 1000}]) is None


def test_two_measures_become_a_scatter():
    rows = [{"spend": i, "revenue": i * 2} for i in range(5)]
    assert recommend_chart(["spend", "revenue"], rows)["chart_type"] == "scatter"


def test_no_rows_means_no_chart():
    assert recommend_chart(["a", "b"], []) is None


# --- chart validation ---


def test_chart_naming_an_absent_column_is_discarded():
    """The model's favourite failure. A chart on a column that is not there."""
    config = {"chart_type": "bar", "x_axis": "region", "y_axis": "profit_margin"}
    assert validate_chart(config, ["region", "revenue"]) is None


def test_unknown_chart_type_is_discarded():
    assert validate_chart({"chart_type": "sankey", "x_axis": "a", "y_axis": "b"}, ["a", "b"]) is None


def test_valid_chart_passes_through():
    config = {"chart_type": "bar", "x_axis": "region", "y_axis": "revenue", "title": "Revenue"}
    assert validate_chart(config, ["region", "revenue"]) == config


def test_non_table_chart_without_axes_is_discarded():
    assert validate_chart({"chart_type": "line"}, ["a", "b"]) is None


def test_null_color_by_is_dropped_not_rejected():
    config = {"chart_type": "bar", "x_axis": "a", "y_axis": "b", "color_by": "null"}
    assert validate_chart(config, ["a", "b"]) == {"chart_type": "bar", "x_axis": "a", "y_axis": "b"}


# --- routing ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply,expected",
    [
        ("SQL_QUERY", SQL_QUERY),
        ("KNOWLEDGE", KNOWLEDGE),
        ("VISUALIZATION", VISUALIZATION),
        ("  knowledge  ", KNOWLEDGE),
        ("The category is VISUALIZATION.", VISUALIZATION),
    ],
)
async def test_classification_reads_the_label(reply, expected):
    supervisor = SupervisorAgent(
        FakeSQLExecutor(), FakeDatabricksClient(), client=FakeClaude(reply)
    )
    assert await supervisor.classify("anything") == expected


@pytest.mark.asyncio
async def test_unrecognized_label_defaults_to_sql():
    """A wrong data answer is visible. A wrong definition answer is not."""
    supervisor = SupervisorAgent(
        FakeSQLExecutor(), FakeDatabricksClient(), client=FakeClaude("banana")
    )
    assert await supervisor.classify("anything") == SQL_QUERY


@pytest.mark.asyncio
async def test_routing_failure_does_not_fail_the_request():
    class Exploding:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("upstream down")

    supervisor = SupervisorAgent(
        FakeSQLExecutor(), FakeDatabricksClient(), client=Exploding()
    )
    assert await supervisor.classify("anything") == SQL_QUERY


# --- sql analyst ---


@pytest.mark.asyncio
async def test_sql_answer_carries_the_query_and_a_chart():
    executor = FakeSQLExecutor()
    claude = FakeClaude("SQL_QUERY", "```sql\nSELECT region, revenue FROM t\n```", "East leads.")
    supervisor = SupervisorAgent(executor, FakeDatabricksClient(), client=claude)

    result = await supervisor.route("revenue by region")

    assert result.agent_name == "SQL Analyst"
    assert result.sql == "SELECT region, revenue FROM t"
    assert result.summary == "East leads."
    assert result.chart_config["chart_type"] == "bar"


@pytest.mark.asyncio
async def test_generated_write_is_refused_before_execution():
    """The validator runs on model output, so nothing reaches the warehouse."""
    from services.sql_executor import SQLValidationError

    executor = FakeSQLExecutor()
    claude = FakeClaude("DROP TABLE medallion_demo.gold.fact_orders")
    analyst = SQLAnalystAgent(executor, FakeDatabricksClient(), client=claude)

    with pytest.raises(SQLValidationError):
        await analyst.analyze("delete everything")
    assert executor.executed == []


@pytest.mark.asyncio
async def test_empty_model_response_raises_rather_than_running_nothing():
    analyst = SQLAnalystAgent(FakeSQLExecutor(), FakeDatabricksClient(), client=FakeClaude(""))
    with pytest.raises(ValueError, match="no SQL"):
        await analyst.analyze("something")


# --- knowledge agent ---


@pytest.mark.asyncio
async def test_documents_are_cited_when_the_index_answers():
    docs = [Document(title="Glossary: churn_score", content="Rolling 90-day model output.",
                     source_uri="docs/glossary.md", score=0.91)]
    agent = KnowledgeAgent(
        FakeDatabricksClient(),
        vector_search=FakeVectorSearch(docs),
        client=FakeClaude("A rolling 90-day score, per the glossary."),
    )
    result = await agent.search("what is churn_score")
    assert result.sources[0]["reference"] == "docs/glossary.md"
    assert result.sources[0]["type"] == "documentation"


@pytest.mark.asyncio
async def test_missing_index_falls_back_and_says_so():
    """A thin answer is fine. An unexplained thin answer is not."""
    agent = KnowledgeAgent(
        FakeDatabricksClient(),
        vector_search=FakeVectorSearch(documents=[], available=False),
        client=FakeClaude("Based on the schema, it appears to be a score."),
    )
    result = await agent.search("what is churn_score")
    assert result.sources[0]["type"] == "unity_catalog"
    assert "No documentation index" in result.sources[0]["excerpt"]


# --- viz agent ---


@pytest.mark.asyncio
async def test_hallucinated_chart_falls_back_to_the_heuristic():
    claude = FakeClaude(
        "VISUALIZATION",
        "SELECT region, revenue FROM t",
        "East leads.",
        '{"chart_type": "bar", "x_axis": "region", "y_axis": "made_up_column"}',
    )
    supervisor = SupervisorAgent(FakeSQLExecutor(), FakeDatabricksClient(), client=claude)

    result = await supervisor.route("chart revenue by region")

    assert result.agent_name == "Viz Agent"
    assert result.chart_config["y_axis"] == "revenue"
