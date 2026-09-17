"""Shared plumbing for the agents.

One place to construct the Claude client and one shape for what an agent hands
back, so the supervisor can treat every sub-agent the same way.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


@dataclass
class AgentResponse:
    """What every sub-agent returns. The supervisor maps this to the API model."""

    agent_name: str
    summary: str
    sql: Optional[str] = None
    data: Optional[list[dict[str, Any]]] = None
    columns: Optional[list[str]] = None
    chart_config: Optional[dict] = None
    sources: Optional[list[dict]] = field(default=None)
    truncated: bool = False


def build_client():
    """Anthropic client. Kept behind a function so tests can patch one place."""
    import anthropic

    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def strip_fences(text: str) -> str:
    """Remove a markdown code fence if the model wrapped its answer in one.

    The old `.strip("```sql")` call stripped any leading or trailing characters
    in that set, so a query ending in a backtick-quoted identifier lost part of
    itself. This removes the fence and nothing else.
    """
    return _FENCE.sub("", text.strip()).strip()


def parse_json_object(text: str) -> Optional[dict]:
    """Best-effort JSON parse of a model response.

    Models sometimes add a sentence before the object. Falling back to the first
    balanced brace span recovers those without failing the whole request.
    """
    cleaned = strip_fences(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(cleaned)):
        if cleaned[index] == "{":
            depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def first_text(response) -> str:
    """Pull the text out of a Claude response, tolerating an empty block list."""
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""
