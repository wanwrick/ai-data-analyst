"""
Databricks Client — Wrapper around the Databricks SDK.

Provides methods for:
- Unity Catalog schema/table discovery
- SQL Warehouse management
- Table metadata and statistics
"""

import os
import logging
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import TableInfo

logger = logging.getLogger(__name__)


class DatabricksClient:
    def __init__(self):
        self.client = WorkspaceClient(
            host=os.getenv("DATABRICKS_HOST"),
            token=os.getenv("DATABRICKS_TOKEN"),
        )
        self._warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")

    @property
    def warehouse_id(self) -> str:
        if not self._warehouse_id:
            warehouses = list(self.client.warehouses.list())
            if warehouses:
                # Pick the first running or smallest warehouse
                running = [w for w in warehouses if w.state and w.state.value == "RUNNING"]
                self._warehouse_id = (running[0] if running else warehouses[0]).id
        return self._warehouse_id

    async def list_schemas(self, catalog: str) -> list:
        """List all schemas and their tables in a catalog."""
        results = []
        schemas = list(self.client.schemas.list(catalog_name=catalog))
        for schema in schemas:
            if schema.name.startswith("__"):
                continue
            tables = list(
                self.client.tables.list(
                    catalog_name=catalog, schema_name=schema.name
                )
            )
            results.append({
                "catalog": catalog,
                "schema_name": schema.name,
                "tables": [
                    {
                        "name": t.name,
                        "type": t.table_type.value if t.table_type else "TABLE",
                        "comment": t.comment or "",
                    }
                    for t in tables
                ],
            })
        return results

    async def get_schema_context(self, catalog: str, schema_filter: str = None) -> str:
        """Get formatted schema context for LLM prompts."""
        schemas = await self.list_schemas(catalog)
        context_parts = []

        for schema_info in schemas:
            if schema_filter and schema_info["schema_name"] != schema_filter:
                continue

            for table in schema_info["tables"]:
                full_name = f"{catalog}.{schema_info['schema_name']}.{table['name']}"
                try:
                    details = await self.get_table_details(full_name)
                    cols = ", ".join(
                        f"{c['name']} ({c['type']})" for c in details.get("columns", [])
                    )
                    context_parts.append(
                        f"Table: {full_name}\n"
                        f"  Comment: {table.get('comment', 'N/A')}\n"
                        f"  Columns: {cols}\n"
                    )
                except Exception as e:
                    logger.warning(f"Could not get details for {full_name}: {e}")
                    context_parts.append(f"Table: {full_name} (details unavailable)\n")

        return "\n".join(context_parts)

    async def get_table_details(self, full_table_name: str) -> dict:
        """Get column details and sample data for a table."""
        parts = full_table_name.split(".")
        if len(parts) != 3:
            raise ValueError(f"Expected catalog.schema.table, got: {full_table_name}")

        table = self.client.tables.get(full_name=full_table_name)
        columns = [
            {
                "name": c.name,
                "type": c.type_text or "STRING",
                "comment": c.comment or "",
                "nullable": c.nullable,
            }
            for c in (table.columns or [])
        ]

        return {
            "full_name": full_table_name,
            "columns": columns,
            "comment": table.comment or "",
            "table_type": table.table_type.value if table.table_type else "TABLE",
            "row_count": table.properties.get("numRows") if table.properties else None,
        }
