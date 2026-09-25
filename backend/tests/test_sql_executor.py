"""SQL validator tests.

The model writes this SQL, so the validator is the security boundary. Two kinds
of bug matter equally: letting a write through, and rejecting a legitimate query
because the word "delete" appeared inside a string.
"""

from __future__ import annotations

import pytest

from services.sql_executor import (
    MAX_RESULT_ROWS,
    SQLExecutor,
    SQLValidationError,
    strip_noise,
)


@pytest.fixture
def executor():
    return SQLExecutor(db_client=None)


ALLOWED = [
    "SELECT 1",
    "SELECT * FROM medallion_demo.gold.fact_orders LIMIT 10",
    "WITH recent AS (SELECT * FROM t) SELECT * FROM recent",
    "SHOW TABLES IN medallion_demo.gold",
    "DESCRIBE medallion_demo.gold.fact_orders",
    "EXPLAIN SELECT * FROM t",
    "SELECT * FROM t;",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_only_queries_are_allowed(executor, sql):
    executor.validate(sql)


BLOCKED = [
    "DROP TABLE medallion_demo.gold.fact_orders",
    "DELETE FROM t WHERE 1=1",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "TRUNCATE TABLE t",
    "MERGE INTO t USING s ON t.id = s.id",
    "GRANT SELECT ON t TO `user`",
    "CREATE TABLE t AS SELECT 1",
    "ALTER TABLE t ADD COLUMN c INT",
]


@pytest.mark.parametrize("sql", BLOCKED)
def test_writes_are_rejected(executor, sql):
    with pytest.raises(SQLValidationError):
        executor.validate(sql)


def test_stacked_statement_is_rejected(executor):
    """The classic. A valid SELECT followed by something that is not."""
    with pytest.raises(SQLValidationError, match="Multiple statements"):
        executor.validate("SELECT 1; DROP TABLE medallion_demo.gold.fact_orders")


def test_write_hidden_behind_a_comment_is_rejected(executor):
    """Scanning raw text would see the -- and stop reading. Stripping first does not."""
    with pytest.raises(SQLValidationError):
        executor.validate("SELECT 1 /* harmless */ ; DROP TABLE t")


def test_keyword_inside_a_string_literal_is_not_a_write(executor):
    """A support ticket that mentions deleting something is still a SELECT."""
    executor.validate("SELECT * FROM tickets WHERE note = 'please delete this row'")


def test_keyword_inside_a_comment_is_not_a_write(executor):
    executor.validate("-- todo: drop this table next quarter\nSELECT 1")


def test_column_named_like_a_keyword_is_allowed(executor):
    executor.validate("SELECT update_ts, create_date FROM medallion_demo.gold.fact_orders")


def test_empty_query_is_rejected(executor):
    with pytest.raises(SQLValidationError, match="Empty"):
        executor.validate("   ")


def test_error_message_names_the_offending_keyword(executor):
    """A single statement that starts legitimately and turns into a write."""
    with pytest.raises(SQLValidationError, match="INSERT"):
        executor.validate("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x")


# --- limit handling ---


def test_limit_is_added_when_missing(executor):
    statement, capped = executor._add_limit("SELECT * FROM t")
    assert capped
    assert "LIMIT 1000" in statement


def test_existing_limit_is_respected(executor):
    sql = "SELECT * FROM t LIMIT 5"
    assert executor._add_limit(sql) == (sql, False)


def test_limit_mentioned_only_in_a_string_does_not_count(executor):
    """Otherwise the query runs uncapped because the word appeared in a value."""
    sql = "SELECT * FROM t WHERE label = 'no limit'"
    statement, capped = executor._add_limit(sql)
    assert capped
    assert "LIMIT 1000" in statement


def test_trailing_semicolon_does_not_break_the_limit(executor):
    statement, _ = executor._add_limit("SELECT * FROM t;")
    assert statement.endswith("LIMIT 1000")
    assert ";" not in statement


# --- strip_noise ---


def test_strip_noise_removes_comments_and_literals():
    cleaned = strip_noise("SELECT 'drop' /* delete */ FROM t -- truncate")
    assert "drop" not in cleaned.lower()
    assert "delete" not in cleaned.lower()
    assert "truncate" not in cleaned.lower()
    assert "SELECT" in cleaned


def test_strip_noise_handles_escaped_quotes():
    cleaned = strip_noise("SELECT * FROM t WHERE name = 'O''Brien drop table'")
    assert "drop" not in cleaned.lower()
    assert "FROM t WHERE name" in cleaned


def test_limit_inside_a_subquery_does_not_count_as_the_outer_cap(executor):
    """A LIMIT on an inner query bounds that query alone; the outer still needs one."""
    sql = "SELECT * FROM (SELECT customer_id FROM t LIMIT 5) x"
    statement, capped = executor._add_limit(sql)
    assert capped
    assert "LIMIT 1000" in statement


def test_top_level_limit_after_a_subquery_is_respected(executor):
    sql = "SELECT * FROM (SELECT customer_id FROM t) x LIMIT 20"
    assert executor._add_limit(sql) == (sql, False)


# --- truncation ---


def _warehouse_returning(rows: list[list]):
    """A stand-in for the Databricks client that answers every statement with rows."""

    class Column:
        def __init__(self, name):
            self.name = name

    class Fake:
        class client:
            class statement_execution:
                @staticmethod
                def execute_statement(**kwargs):
                    return type(
                        "R",
                        (),
                        {
                            "manifest": type(
                                "M", (), {"schema": type("S", (), {"columns": [Column("n")]})}
                            ),
                            "result": type("Res", (), {"data_array": rows}),
                        },
                    )

        warehouse_id = "w"

    return Fake()


@pytest.mark.asyncio
async def test_result_at_the_cap_is_flagged_truncated():
    """A silently clipped result presented as complete is a wrong answer."""
    rows = [[i] for i in range(MAX_RESULT_ROWS)]
    result = await SQLExecutor(_warehouse_returning(rows)).execute("SELECT n FROM t")
    assert result["truncated"] is True
    assert result["row_count"] == MAX_RESULT_ROWS


@pytest.mark.asyncio
async def test_users_own_limit_at_the_cap_is_not_truncated():
    """LIMIT 1000 returning 1000 rows is the complete answer the user asked for."""
    rows = [[i] for i in range(MAX_RESULT_ROWS)]
    sql = f"SELECT n FROM t LIMIT {MAX_RESULT_ROWS}"
    result = await SQLExecutor(_warehouse_returning(rows)).execute(sql)
    assert result["truncated"] is False
    assert result["row_count"] == MAX_RESULT_ROWS
