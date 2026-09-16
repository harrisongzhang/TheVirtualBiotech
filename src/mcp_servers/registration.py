"""Register tools with MCP error semantics for operational failures."""
from __future__ import annotations

from functools import wraps
import inspect
import json

from fastmcp.exceptions import ToolError

from src.utils.tool_errors import tool_result_error


def register_tool(server, function):
    """Keep each tool's signature and successful output; expose failed calls.

    Several data tools historically returned failure dicts, which the transport
    marked as successful. Raising ToolError makes the MCP response isError=true
    and keeps the failure visible to the caller and the audit trace.
    """
    def checked(result):
        error = tool_result_error(result)
        if error:
            raise ToolError(json.dumps({
                "status": "tool_error",
                "tool": function.__name__,
                "error": error,
                "instruction": (
                    "This call did not produce evidence. Report the failure and "
                    "its effect on the analysis. Identify any alternative source "
                    "explicitly; do not attribute it to this failed call."
                ),
            }))
        return result

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            return checked(await function(*args, **kwargs))
    else:
        @wraps(function)
        def wrapped(*args, **kwargs):
            return checked(function(*args, **kwargs))
    return server.tool()(wrapped)
