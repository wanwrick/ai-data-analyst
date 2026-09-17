"""API tests against the real FastAPI app with fake services behind it.

These check the contract the frontend is built against: field names, status
codes, and that a refused query comes back as a 422 the UI can explain rather
than a 500 it cannot.
"""

from __future__ import annotations

import pytest
from conftest import FakeClaude, FakeDatabricksClient, FakeSQLExecutor, FakeVectorSearch
from fastapi.testclient import TestClient

from agents.supervisor import SupervisorAgent
from app import app
from services.sql_executor import SQLExecutor


def _wire(claude: FakeClaude, executor=None, vector_search=None):
    executor = executor or FakeSQLExecutor()
    db = FakeDatabricksClient()
    app.state.db_client = db
    app.state.sql_executor = executor
    app.state.vector_search = vector_search or FakeVectorSearch(available=False)
    app.state.supervisor = SupervisorAgent(
        executor, db, vector_search=app.state.vector_search, client=claude
    )
    # Bypass lifespan so no real clients are constructed.
    return TestClient(app, raise_server_exceptions=False)


def test_health_reports_vector_search_mode():
    client = _wire(FakeClaude())
    body = client.get("/api/health").json()
    assert body["status"] == "healthy"
    assert body["vector_search"] is False


def test_analyze_returns_the_documented_shape():
    client = _wire(
        FakeClaude("SQL_QUERY", "SELECT region, revenue FROM t", "East leads at 120.")
    )
    response = client.post("/api/analyze", json={"question": "revenue by region"})
    assert response.status_code == 200

    body = response.json()
    assert body["agent_used"] == "SQL Analyst"
    assert body["sql"] == "SELECT region, revenue FROM t"
    assert body["columns"] == ["region", "revenue"]
    assert body["chart_recommendation"]["chart_type"] == "bar"
    assert body["truncated"] is False


def test_refused_query_is_a_422_not_a_500():
    """The UI shows the reason. A 500 would just say something went wrong."""
    client = _wire(FakeClaude("SQL_QUERY", "DROP TABLE medallion_demo.gold.fact_orders"))
    response = client.post("/api/analyze", json={"question": "drop everything"})
    assert response.status_code == 422
    assert "read-only" in response.json()["detail"].lower()


def test_blank_question_is_rejected_by_validation():
    client = _wire(FakeClaude())
    assert client.post("/api/analyze", json={"question": "   "}).status_code == 422


def test_question_field_is_required():
    client = _wire(FakeClaude())
    assert client.post("/api/analyze", json={}).status_code == 422


def test_schema_alias_is_accepted_from_the_frontend():
    """The client sends `schema`; the model field is `schema_filter`."""
    client = _wire(FakeClaude("SQL_QUERY", "SELECT region, revenue FROM t", "ok"))
    response = client.post(
        "/api/analyze", json={"question": "revenue", "catalog": "medallion_demo", "schema": "gold"}
    )
    assert response.status_code == 200


def test_schemas_endpoint_lists_tables():
    client = _wire(FakeClaude())
    body = client.get("/api/schemas").json()
    assert body[0]["schema_name"] == "gold"
    assert body[0]["tables"][0]["name"] == "fact_orders"


def test_table_details_endpoint():
    client = _wire(FakeClaude())
    body = client.get("/api/table/medallion_demo/gold/fact_orders").json()
    assert body["full_name"] == "medallion_demo.gold.fact_orders"
    assert body["columns"][0]["name"] == "order_id"


def test_knowledge_question_returns_sources_and_no_sql():
    client = _wire(FakeClaude("KNOWLEDGE", "It is a rolling 90-day score."))
    body = client.post("/api/analyze", json={"question": "what is churn_score"}).json()
    assert body["agent_used"] == "Knowledge Agent"
    assert body["sql"] is None
    assert body["sources"][0]["type"] == "unity_catalog"


@pytest.mark.parametrize("path", ["/api/health", "/api/schemas", "/api/history"])
def test_documented_get_endpoints_exist(path):
    client = _wire(FakeClaude())
    assert client.get(path).status_code == 200


def test_openapi_schema_builds():
    """A broken response model only surfaces when the schema is generated."""
    client = _wire(FakeClaude())
    schema = client.get("/openapi.json").json()
    assert "/api/analyze" in schema["paths"]


def test_executor_validate_is_the_real_one():
    """Guards against a fake that quietly permits everything."""
    assert isinstance(FakeSQLExecutor()._real, SQLExecutor)
