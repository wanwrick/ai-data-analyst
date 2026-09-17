"""
AI Data Analyst — FastAPI Backend
Serves the React frontend and provides API endpoints for the AI analyst agents.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.supervisor import SupervisorAgent
from models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    SchemaInfo,
    TableDetails,
)
from services.databricks_client import DatabricksClient
from services.sql_executor import SQLExecutor, SQLValidationError
from services.vector_search import VectorSearchService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    logger.info("Starting AI Data Analyst")
    app.state.db_client = DatabricksClient()
    app.state.sql_executor = SQLExecutor(app.state.db_client)
    app.state.vector_search = VectorSearchService(app.state.db_client)
    app.state.supervisor = SupervisorAgent(
        sql_executor=app.state.sql_executor,
        db_client=app.state.db_client,
        vector_search=app.state.vector_search,
    )
    logger.info("Services initialized")
    yield
    logger.info("Shutting down AI Data Analyst")


# --- App ---

app = FastAPI(
    title="AI Data Analyst",
    description="Ask questions about your data warehouse in natural language",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---


@app.get("/api/health", response_model=HealthResponse)
async def health():
    vector_search = getattr(app.state, "vector_search", None)
    return HealthResponse(
        status="healthy",
        service="ai-data-analyst",
        vector_search=bool(vector_search and vector_search.is_available),
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Main endpoint — ask a natural language question about your data."""
    try:
        result = await app.state.supervisor.route(
            question=request.question,
            catalog=request.catalog,
            schema_filter=request.schema_filter,
        )
    except SQLValidationError as exc:
        # The model wrote something we will not run. That is a refusal, not a
        # server fault, and the user should see why.
        logger.warning("Rejected generated SQL: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return AnalyzeResponse(
        question=request.question,
        agent_used=result.agent_name,
        sql=result.sql,
        summary=result.summary,
        data=result.data,
        columns=result.columns,
        chart_recommendation=result.chart_config,
        sources=result.sources,
        truncated=result.truncated,
    )


@app.get("/api/schemas", response_model=list[SchemaInfo])
async def list_schemas(catalog: str = "medallion_demo"):
    """List available schemas and tables for the schema explorer."""
    try:
        return await app.state.db_client.list_schemas(catalog)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/table/{catalog}/{schema}/{table}", response_model=TableDetails)
async def get_table_details(catalog: str, schema: str, table: str):
    """Get column details for a specific table."""
    try:
        return await app.state.db_client.get_table_details(f"{catalog}.{schema}.{table}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/history")
async def get_history(limit: int = 20):
    """Recent analysis history. Persistence is not wired up yet."""
    return {"history": [], "total": 0, "limit": limit}


# --- Serve Frontend (production) ---
# The compiled React app is served as static files. This must stay last,
# because it mounts a catch-all at the root.

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
