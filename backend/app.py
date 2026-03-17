"""
AI Data Analyst — FastAPI Backend
Serves the React frontend and provides API endpoints for the AI analyst agents.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from agents.supervisor import SupervisorAgent
from services.databricks_client import DatabricksClient
from services.sql_executor import SQLExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    logger.info("🚀 Starting AI Data Analyst...")
    app.state.db_client = DatabricksClient()
    app.state.sql_executor = SQLExecutor(app.state.db_client)
    app.state.supervisor = SupervisorAgent(
        sql_executor=app.state.sql_executor,
        db_client=app.state.db_client,
    )
    logger.info("✅ All services initialized")
    yield
    logger.info("👋 Shutting down AI Data Analyst")

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

# --- Models ---

class AnalyzeRequest(BaseModel):
    question: str
    catalog: Optional[str] = "medallion_demo"
    schema: Optional[str] = None

class AnalyzeResponse(BaseModel):
    question: str
    agent_used: str
    sql: Optional[str] = None
    summary: str
    data: Optional[list] = None
    columns: Optional[list] = None
    chart_recommendation: Optional[dict] = None
    sources: Optional[list] = None

class SchemaInfo(BaseModel):
    catalog: str
    schema_name: str
    tables: list

# --- Routes ---

@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "ai-data-analyst"}

@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Main endpoint — ask a natural language question about your data."""
    try:
        result = await app.state.supervisor.route(
            question=request.question,
            catalog=request.catalog,
            schema_filter=request.schema,
        )
        return AnalyzeResponse(
            question=request.question,
            agent_used=result.agent_name,
            sql=result.sql,
            summary=result.summary,
            data=result.data,
            columns=result.columns,
            chart_recommendation=result.chart_config,
            sources=result.sources,
        )
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/schemas", response_model=list[SchemaInfo])
async def list_schemas(catalog: str = "medallion_demo"):
    """List available schemas and tables for the schema explorer."""
    try:
        schemas = await app.state.db_client.list_schemas(catalog)
        return schemas
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/table/{catalog}/{schema}/{table}")
async def get_table_details(catalog: str, schema: str, table: str):
    """Get column details and sample data for a specific table."""
    try:
        details = await app.state.db_client.get_table_details(
            f"{catalog}.{schema}.{table}"
        )
        return details
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history")
async def get_history(limit: int = 20):
    """Get recent analysis history."""
    # In production, this would query a persistence layer
    return {"history": [], "total": 0}

# --- Serve Frontend (production) ---
# In production, the compiled React app is served as static files
# This must be the LAST route (catch-all)

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
