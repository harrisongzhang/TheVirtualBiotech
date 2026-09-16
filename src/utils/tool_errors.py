"""Recognize operational tool failures in current and legacy result envelopes."""
from __future__ import annotations

import json
import re
from typing import Any


# These are the existing tools' explicit empty-lookup messages. Do not exempt
# arbitrary "not found" errors: a missing dataset, file, or column is a failed
# query, even when a tool also returns found=False or an empty result list.
_EMPTY_LOOKUP = re.compile(
    r"(?:"
    r"(?:Target|Drug|Disease|Biosample|Pathway|GO term|SO term|Study|Variant)"
    r"(?: [^\n]+)? not found"
    r"|Gene not found(?: in (?:expression|essentiality) dataset)?"
    r"|rsID '[^\n]+' not found in database"
    r"|Entity '[^\n]+' not found in literature vector dataset"
    r"|Trial [^\n]+ not found in ClinicalTrials\.gov"
    r"|No therapeutic area found matching '[^\n]+'"
    r"|No cells found for filter: [^\n]+"
    r"|No samples found"
    r"|No perturbation data found for this drug"
    r"|No data for cell line [^\n]+"
    r"|No comparison cell lines with data"
    r"|Insufficient data: drug_a has \d+ hits, drug_b has \d+ hits"
    r")\.?",
    re.IGNORECASE,
)


def tool_result_error(result: Any) -> str | None:
    """Return the failure message, or None for successful/empty lookups.

    Accept a tool's dict, serialized JSON, or MCP text-content blocks. Inspect
    only the result envelope, never arbitrary nested evidence rows that might
    legitimately contain an ``error`` column. Transport ``is_error`` flags are
    also recognized so traces retain raised exceptions as well as legacy dicts.
    """
    if isinstance(result, str):
        try:
            return tool_result_error(json.loads(result))
        except (ValueError, TypeError):
            return None
    if isinstance(result, (list, tuple)):
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                error = tool_result_error(block.get("text"))
            elif getattr(block, "type", None) == "text":
                error = tool_result_error(getattr(block, "text", None))
            else:
                continue
            if error:
                return error
        return None
    if not isinstance(result, dict):
        return None

    if result.get("type") == "text":
        return tool_result_error(result.get("text"))
    if result.get("is_error") is True or result.get("isError") is True:
        return (tool_result_error(result.get("content"))
                or str(result.get("content") or result.get("error") or "Tool call failed"))
    if "structuredContent" in result:
        error = tool_result_error(result["structuredContent"])
        if error:
            return error
    if result.get("type") == "tool_result" or "isError" in result:
        return tool_result_error(result.get("content"))

    error = result.get("error") or result.get("errors")
    failed = result.get("success") is False or result.get("ok") is False
    if not error and not failed:
        return None
    if isinstance(error, list):
        message = "; ".join(str(item) for item in error)
    else:
        message = str(error or "Tool returned an unsuccessful result")
    if _EMPTY_LOOKUP.fullmatch(message.strip()):
        return None
    return message
