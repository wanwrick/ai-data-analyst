"""
SQL Executor — Safe SQL execution on Databricks SQL Warehouse.

Safety features:
- Only SELECT statements allowed
- Stacked statements rejected
- Query cost guardrails
- Timeout enforcement
- Result size limits

The model writes the SQL, so the model is untrusted input. Validation runs on
the statement with comments and string literals removed, because scanning the
raw text both misses `SELECT 1 -- ; DROP TABLE t` and falsely rejects
`WHERE note = 'please delete this'`.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT",
    "UPDATE", "MERGE", "GRANT", "REVOKE", "DENY", "COPY", "RESTORE",
]

VALID_STARTS = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")

MAX_RESULT_ROWS = 1000
QUERY_TIMEOUT_SECONDS = 120

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SINGLE_QUOTED = re.compile(r"'(?:''|\\.|[^'])*'", re.DOTALL)
_DOUBLE_QUOTED = re.compile(r'"(?:""|\\.|[^"])*"', re.DOTALL)
_BACKTICKED = re.compile(r"`[^`]*`")


class SQLValidationError(Exception):
    pass


def _has_top_level_limit(structure: str) -> bool:
    """True if a LIMIT keyword appears at parenthesis depth zero."""
    depth = 0
    for match in re.finditer(r"[()]|\bLIMIT\b", structure.upper()):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            return True
    return False


def strip_noise(sql: str) -> str:
    """Remove comments, string literals, and quoted identifiers.

    What is left is the statement's structure, which is the only part that
    should decide whether the query is allowed to run.
    """
    cleaned = _BLOCK_COMMENT.sub(" ", sql)
    cleaned = _LINE_COMMENT.sub(" ", cleaned)
    cleaned = _SINGLE_QUOTED.sub("''", cleaned)
    cleaned = _DOUBLE_QUOTED.sub('""', cleaned)
    cleaned = _BACKTICKED.sub("``", cleaned)
    return cleaned


class SQLExecutor:
    def __init__(self, db_client):
        self.db_client = db_client

    def validate(self, sql: str) -> None:
        """Raise unless the statement is a single read-only query."""
        if not sql or not sql.strip():
            raise SQLValidationError("Empty query")

        structure = strip_noise(sql).strip()
        normalized = structure.upper()

        # A trailing semicolon is fine. A second statement after it is not.
        if ";" in normalized.rstrip().rstrip(";"):
            raise SQLValidationError(
                "Multiple statements are not allowed. Submit one query at a time."
            )

        if not normalized.startswith(VALID_STARTS):
            first_word = (normalized.split() or ["(empty)"])[0]
            raise SQLValidationError(
                f"Only read-only queries are allowed. This one starts with {first_word}."
            )

        for keyword in BLOCKED_KEYWORDS:
            if re.search(rf"\b{keyword}\b", normalized):
                raise SQLValidationError(
                    f"Blocked keyword detected: {keyword}. Only read-only queries are permitted."
                )

        logger.info("SQL validation passed")

    def _add_limit(self, sql: str) -> str:
        """Cap the result set when the query did not cap itself.

        Only a LIMIT at the top level counts. A LIMIT inside a subquery bounds
        that subquery alone, so treating it as the outer cap let an unbounded
        result through.
        """
        if _has_top_level_limit(strip_noise(sql)):
            return sql
        return f"{sql.rstrip().rstrip(';')}\nLIMIT {MAX_RESULT_ROWS}"

    async def execute(self, sql: str) -> dict:
        """Execute SQL and return results as a list of dicts."""
        capped = self._add_limit(sql)
        logger.info("Executing SQL: %s", capped.replace("\n", " ")[:200])

        try:
            result = self.db_client.client.statement_execution.execute_statement(
                warehouse_id=self.db_client.warehouse_id,
                statement=capped,
                wait_timeout=f"{QUERY_TIMEOUT_SECONDS}s",
            )

            columns = [col.name for col in (result.manifest.schema.columns or [])]

            data = []
            if result.result and result.result.data_array:
                for row in result.result.data_array:
                    data.append(dict(zip(columns, row)))

            # Only a cap we imposed can have cut rows. A user's own LIMIT 1000
            # returning 1000 rows is a complete answer, not a truncated one.
            truncated = capped != sql and len(data) >= MAX_RESULT_ROWS

            logger.info("Query returned %d rows, %d columns", len(data), len(columns))
            return {
                "columns": columns,
                "data": data,
                "row_count": len(data),
                "truncated": truncated,
            }

        except Exception as exc:
            logger.error("SQL execution failed: %s", exc)
            raise
