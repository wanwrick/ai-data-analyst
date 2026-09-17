"""Pydantic request and response models for the API surface.

These live apart from `app.py` because the frontend's generated client is built
from this schema. Changing a field here changes the contract, which is easier to
notice when the contract is its own file.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

ChartType = Literal["bar", "line", "pie", "scatter", "funnel", "heatmap", "table"]
AgentName = Literal["SQL Analyst", "Knowledge Agent", "Viz Agent"]


class AnalyzeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    catalog: str = "medallion_demo"
    schema_filter: Optional[str] = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question cannot be blank")
        return value.strip()


class ChartConfig(BaseModel):
    chart_type: ChartType
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    color_by: Optional[str] = None
    title: Optional[str] = None


class Source(BaseModel):
    """Where an answer came from, so the user can check it."""

    type: str
    reference: str
    excerpt: Optional[str] = None
    score: Optional[float] = None


class AnalyzeResponse(BaseModel):
    question: str
    agent_used: str
    summary: str
    sql: Optional[str] = None
    data: Optional[list[dict[str, Any]]] = None
    columns: Optional[list[str]] = None
    chart_recommendation: Optional[ChartConfig] = None
    sources: Optional[list[Source]] = None
    truncated: bool = False


class ColumnInfo(BaseModel):
    name: str
    type: str
    comment: str = ""
    nullable: Optional[bool] = None


class TableSummary(BaseModel):
    name: str
    type: str = "TABLE"
    comment: str = ""


class SchemaInfo(BaseModel):
    catalog: str
    schema_name: str
    tables: list[TableSummary]


class TableDetails(BaseModel):
    full_name: str
    columns: list[ColumnInfo]
    comment: str = ""
    table_type: str = "TABLE"
    row_count: Optional[int] = None


class HealthResponse(BaseModel):
    status: str
    service: str
    vector_search: bool = False
