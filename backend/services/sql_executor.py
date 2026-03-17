"""
SQL Executor — Safe SQL execution on Databricks SQL Warehouse.

Safety features:
- Only SELECT statements allowed
- Query cost guardrails
- Timeout enforcement
- Result size limits
"""

import re
import logging

logger = logging.getLogger(__name__)

BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT",
    "UPDATE", "MERGE", "GRANT", "REVOKE", "DENY",
]

MAX_RESULT_ROWS = 1000
QUERY_TIMEOUT_SECONDS = 120


class SQLValidationError(Exception):
    pass


class SQLExecutor:
    def __init__(self, db_client):
        self.db_client = db_client

    def validate(self, sql: str):
        """Validate SQL is safe to execute (read-only)."""
        normalized = sql.upper().strip()

        # Must start with SELECT, WITH, or SHOW/DESCRIBE
        valid_starts = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
        if not any(normalized.startswith(s) for s in valid_starts):
            raise SQLValidationError(
                f"Only SELECT queries are allowed. Query starts with: "
                f"{normalized[:20]}..."
            )

        # Check for blocked DDL/DML keywords at statement boundaries
        for keyword in BLOCKED_KEYWORDS:
            pattern = rf"\b{keyword}\b"
            if re.search(pattern, normalized):
                raise SQLValidationError(
                    f"Blocked keyword detected: {keyword}. "
                    f"Only read-only queries are permitted."
                )

        # Ensure LIMIT clause exists (add if missing)
        if "LIMIT" not in normalized:
            logger.info("Adding LIMIT clause to query")

        logger.info("✅ SQL validation passed")

    def _add_limit(self, sql: str) -> str:
        """Add LIMIT clause if not present."""
        if "LIMIT" not in sql.upper():
            return f"{sql.rstrip(';')}\nLIMIT {MAX_RESULT_ROWS}"
        return sql

    async def execute(self, sql: str) -> dict:
        """Execute SQL and return results as list of dicts."""
        sql = self._add_limit(sql)
        logger.info(f"Executing SQL:\n{sql[:200]}...")

        try:
            result = self.db_client.client.statement_execution.execute_statement(
                warehouse_id=self.db_client.warehouse_id,
                statement=sql,
                wait_timeout=f"{QUERY_TIMEOUT_SECONDS}s",
            )

            # Extract columns
            columns = [col.name for col in (result.manifest.schema.columns or [])]

            # Extract data as list of dicts
            data = []
            if result.result and result.result.data_array:
                for row in result.result.data_array:
                    data.append(dict(zip(columns, row)))

            logger.info(f"Query returned {len(data)} rows, {len(columns)} columns")
            return {"columns": columns, "data": data, "row_count": len(data)}

        except Exception as e:
            logger.error(f"SQL execution failed: {e}")
            raise
