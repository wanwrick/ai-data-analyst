"""Databricks Vector Search wrapper for the documentation index.

Vector Search is optional. A workspace without an index should still serve
questions about data, so this degrades to "no documents found" rather than
failing the request. `is_available` lets the health endpoint report which mode
the app is running in, so a thin answer is explainable instead of mysterious.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_INDEX = os.getenv("VECTOR_SEARCH_INDEX", "medallion_demo.gold.data_docs_index")
DEFAULT_ENDPOINT = os.getenv("VECTOR_SEARCH_ENDPOINT", "")
DEFAULT_NUM_RESULTS = 5

# Columns the docs index is expected to expose.
TEXT_COLUMN = "content"
TITLE_COLUMN = "title"
URI_COLUMN = "source_uri"


@dataclass
class Document:
    title: str
    content: str
    source_uri: str
    score: float

    def as_source(self) -> dict:
        return {
            "type": "documentation",
            "reference": self.source_uri or self.title,
            "excerpt": self.content[:300],
            "score": round(self.score, 4),
        }


class VectorSearchService:
    def __init__(
        self,
        db_client,
        index_name: str = DEFAULT_INDEX,
        endpoint_name: str = DEFAULT_ENDPOINT,
    ):
        self.db_client = db_client
        self.index_name = index_name
        self.endpoint_name = endpoint_name
        self._index = None
        self._checked = False

    @property
    def is_available(self) -> bool:
        """True when an index was resolved. Checked once, then cached."""
        if not self._checked:
            self._resolve_index()
        return self._index is not None

    def _resolve_index(self) -> None:
        self._checked = True
        if not self.index_name:
            logger.info("No vector search index configured; knowledge answers will use catalog metadata only")
            return
        try:
            from databricks.vector_search.client import VectorSearchClient

            client = VectorSearchClient(disable_notice=True)
            self._index = client.get_index(
                endpoint_name=self.endpoint_name or None,
                index_name=self.index_name,
            )
            logger.info("Vector search index ready: %s", self.index_name)
        except Exception as exc:
            # Missing package, missing index, or no permission. All are fine.
            logger.warning("Vector search unavailable (%s); falling back to catalog metadata", exc)
            self._index = None

    async def similarity_search(
        self, query: str, num_results: int = DEFAULT_NUM_RESULTS
    ) -> list[Document]:
        if not self.is_available:
            return []
        try:
            raw = self._index.similarity_search(
                query_text=query,
                columns=[TITLE_COLUMN, TEXT_COLUMN, URI_COLUMN],
                num_results=num_results,
            )
        except Exception as exc:
            logger.warning("Vector search query failed: %s", exc)
            return []
        return self._parse(raw)

    @staticmethod
    def _parse(raw: Optional[dict]) -> list[Document]:
        """Unpack the SDK's column-array response into documents."""
        if not raw:
            return []
        result = raw.get("result") or {}
        rows = result.get("data_array") or []
        manifest = raw.get("manifest") or {}
        columns = [c.get("name") for c in (manifest.get("columns") or [])]

        documents: list[Document] = []
        for row in rows:
            record = dict(zip(columns, row)) if columns else {}
            # The relevance score is appended as the final element.
            score = row[-1] if row and isinstance(row[-1], (int, float)) else 0.0
            documents.append(
                Document(
                    title=str(record.get(TITLE_COLUMN, "") or ""),
                    content=str(record.get(TEXT_COLUMN, "") or ""),
                    source_uri=str(record.get(URI_COLUMN, "") or ""),
                    score=float(score),
                )
            )
        return documents
