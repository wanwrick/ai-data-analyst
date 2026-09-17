"""Knowledge agent — answers what the data means, not what it says.

Definition questions are where self-serve analytics usually breaks. Two teams
read the same column and mean different things by it. This agent answers from
the documentation index when one exists, falls back to catalog metadata when it
does not, and always says which of the two it used.
"""

from __future__ import annotations

import logging

from .base import AgentResponse, DEFAULT_MODEL, build_client, first_text

logger = logging.getLogger(__name__)

RAG_PROMPT = """You are a data documentation expert. Answer the question using only
the documentation excerpts below.

Documentation:
{documents}

Question: {question}

Rules:
- Answer in 2 to 4 sentences.
- Cite the document title when you use it.
- If the excerpts do not answer the question, say so directly and name what
  documentation would be needed. Do not guess a definition."""

METADATA_PROMPT = """You are a data documentation expert. No documentation index is
available, so answer from the Unity Catalog schema below.

Schema:
{schemas}

Question: {question}

Rules:
- Answer in 2 to 4 sentences.
- Distinguish what the schema states from what you are inferring.
- Column comments are evidence. Column names alone are a guess, and you should
  label them as one."""


class KnowledgeAgent:
    """Retrieval over data documentation, with a metadata fallback."""

    name = "Knowledge Agent"

    def __init__(
        self,
        db_client,
        vector_search=None,
        client=None,
        model: str = DEFAULT_MODEL,
    ):
        self.db_client = db_client
        self.vector_search = vector_search
        self.client = client or build_client()
        self.model = model

    async def search(self, question: str, catalog: str = "medallion_demo") -> AgentResponse:
        documents = []
        if self.vector_search is not None:
            documents = await self.vector_search.similarity_search(question)

        if documents:
            return await self._answer_from_documents(question, documents)
        return await self._answer_from_metadata(question, catalog)

    async def _answer_from_documents(self, question: str, documents) -> AgentResponse:
        rendered = "\n\n".join(
            f"[{index + 1}] {doc.title}\n{doc.content}" for index, doc in enumerate(documents)
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": RAG_PROMPT.format(documents=rendered, question=question),
                }
            ],
        )
        return AgentResponse(
            agent_name=self.name,
            summary=first_text(response).strip(),
            sources=[doc.as_source() for doc in documents],
        )

    async def _answer_from_metadata(self, question: str, catalog: str) -> AgentResponse:
        schemas = await self.db_client.get_schema_context(catalog)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": METADATA_PROMPT.format(schemas=schemas, question=question),
                }
            ],
        )
        return AgentResponse(
            agent_name=self.name,
            summary=first_text(response).strip(),
            sources=[
                {
                    "type": "unity_catalog",
                    "reference": catalog,
                    "excerpt": "Answered from catalog metadata. No documentation index is configured.",
                }
            ],
        )
