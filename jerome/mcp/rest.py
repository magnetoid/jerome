"""Turn what a REST handler returned into the JSON REST would send.

A tool calls the REST handler function itself (or the same core function with the same
arguments), then passes the result through :func:`rest_json`, so a tool and its route
answer with the same JSON for the same object.
"""

from typing import Any

from fastapi.encoders import jsonable_encoder
from mcp.types import ToolAnnotations
from pydantic import TypeAdapter, ValidationError

READ_ONLY = ToolAnnotations(read_only_hint=True)


def rest_json(value: Any, response_model: Any = None) -> Any:
    """The route's response JSON: through ``response_model`` when the route has one.

    The value is data the server produced, so a mismatch with the model is a server bug,
    not bad input: it is raised as a RuntimeError (``error: internal error``), never as
    the ValidationError that would read ``invalid: ...`` to the agent.
    """
    if response_model is None:
        return jsonable_encoder(value)
    adapter = TypeAdapter(response_model)
    try:
        validated = adapter.validate_python(value, from_attributes=True)
    except ValidationError as exc:
        raise RuntimeError(f"response does not match {response_model!r}") from exc
    return adapter.dump_python(validated, mode="json")
