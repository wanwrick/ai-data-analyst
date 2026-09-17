"""Visualization agent — picks a chart the data can actually support.

Two layers. A deterministic heuristic reads the result's column types and
proposes a chart with no model call, which is what runs on every SQL answer.
The model is asked only when the user explicitly requested a visualization, and
its suggestion is still checked against the real columns before it is returned.
A chart naming a column that is not in the result is worse than no chart.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import AgentResponse, DEFAULT_MODEL, build_client, first_text, parse_json_object

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "pie", "scatter", "funnel", "heatmap", "table"}

# Above this many categories a bar chart stops being readable.
MAX_CATEGORIES_FOR_BAR = 25
# Below this many points a line chart is not a trend.
MIN_POINTS_FOR_LINE = 3

VIZ_PROMPT = """Recommend a chart for this result.

Question: {question}
Available columns: {columns}
Sample rows: {rows}

Respond with ONLY this JSON object and nothing else:
{{"chart_type": "bar|line|pie|scatter|funnel|heatmap|table",
  "x_axis": "column name",
  "y_axis": "column name",
  "color_by": "column name or null",
  "title": "short title"}}

Every column you name must appear in the available columns list."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _looks_temporal(column: str) -> bool:
    lowered = column.lower()
    return any(token in lowered for token in ("date", "time", "month", "week", "year", "day"))


def classify_columns(columns: list[str], data: list[dict]) -> dict[str, list[str]]:
    """Split columns into temporal, numeric, and categorical.

    Types are read from the first row that actually has a value, because a null
    in row one would otherwise make a numeric column look categorical.
    """
    temporal, numeric, categorical = [], [], []
    for column in columns:
        if _looks_temporal(column):
            temporal.append(column)
            continue
        sample = next(
            (row.get(column) for row in data if row.get(column) is not None), None
        )
        if _is_number(sample):
            numeric.append(column)
        else:
            categorical.append(column)
    return {"temporal": temporal, "numeric": numeric, "categorical": categorical}


def recommend_chart(columns: list[str], data: list[dict]) -> Optional[dict]:
    """Deterministic chart choice. No model call, so it runs on every answer."""
    if not columns or not data:
        return None

    kinds = classify_columns(columns, data)
    temporal, numeric, categorical = kinds["temporal"], kinds["numeric"], kinds["categorical"]

    # A single row and a single number is a statistic, not a chart.
    if len(data) == 1 and len(numeric) == 1:
        return None

    if temporal and numeric and len(data) >= MIN_POINTS_FOR_LINE:
        return {
            "chart_type": "line",
            "x_axis": temporal[0],
            "y_axis": numeric[0],
            "title": f"{numeric[0]} over {temporal[0]}",
        }

    if categorical and numeric:
        distinct = len({row.get(categorical[0]) for row in data})
        if distinct > MAX_CATEGORIES_FOR_BAR:
            # Too many bars to read. Show the numbers instead of a smear.
            return {"chart_type": "table", "title": f"{numeric[0]} by {categorical[0]}"}
        return {
            "chart_type": "bar",
            "x_axis": categorical[0],
            "y_axis": numeric[0],
            "title": f"{numeric[0]} by {categorical[0]}",
        }

    if len(numeric) >= 2:
        return {
            "chart_type": "scatter",
            "x_axis": numeric[0],
            "y_axis": numeric[1],
            "title": f"{numeric[1]} against {numeric[0]}",
        }

    return {"chart_type": "table", "title": "Results"}


def validate_chart(config: Optional[dict], columns: list[str]) -> Optional[dict]:
    """Reject a chart that names a column the result does not contain."""
    if not config:
        return None
    chart_type = str(config.get("chart_type", "")).lower()
    if chart_type not in VALID_CHART_TYPES:
        logger.warning("Discarding unknown chart type: %s", chart_type)
        return None

    available = set(columns)
    cleaned: dict[str, Any] = {"chart_type": chart_type}
    for axis in ("x_axis", "y_axis", "color_by"):
        value = config.get(axis)
        if value in (None, "", "null"):
            continue
        if value not in available:
            logger.warning("Discarding chart: %s=%r is not a result column", axis, value)
            return None
        cleaned[axis] = value

    if chart_type != "table" and not ("x_axis" in cleaned and "y_axis" in cleaned):
        return None

    title = config.get("title")
    if isinstance(title, str) and title.strip():
        cleaned["title"] = title.strip()[:120]
    return cleaned


class VizAgent:
    """Chart recommendations, asked for explicitly."""

    name = "Viz Agent"

    def __init__(self, sql_analyst, client=None, model: str = DEFAULT_MODEL):
        self.sql_analyst = sql_analyst
        self.client = client or build_client()
        self.model = model

    async def recommend(
        self,
        question: str,
        catalog: str = "medallion_demo",
        schema_filter: Optional[str] = None,
    ) -> AgentResponse:
        # A chart still needs data, so fetch it the normal way first.
        result = await self.sql_analyst.analyze(question, catalog, schema_filter)
        result.agent_name = self.name
        result.chart_config = await self.choose(
            question, result.columns or [], result.data or []
        )
        return result

    async def choose(self, question: str, columns: list[str], data: list[dict]) -> Optional[dict]:
        """Ask the model, validate the answer, fall back to the heuristic."""
        fallback = recommend_chart(columns, data)
        if not columns or not data:
            return fallback

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[
                    {
                        "role": "user",
                        "content": VIZ_PROMPT.format(
                            question=question,
                            columns=", ".join(columns),
                            rows=data[:3],
                        ),
                    }
                ],
            )
            proposed = validate_chart(parse_json_object(first_text(response)), columns)
        except Exception as exc:
            logger.warning("Chart recommendation call failed: %s", exc)
            proposed = None

        return proposed or fallback
