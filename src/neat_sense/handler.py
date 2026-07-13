"""Lambda target: Neat Sense room telemetry.

Exposed to the model as tools via AgentCore Gateway. The Gateway routes each
MCP tool call to this function; `event` carries the tool arguments and the
invoked tool name arrives in the client context.

Tools:
  neat_list_rooms()            -> rooms/spaces with their endpoint ids
  neat_room_sensors(room_id, fromDateTime?, toDateTime?)
                               -> temp / CO2 / humidity / people count / VOC
"""
from __future__ import annotations

import json
from typing import Any

from _shared.clients import neat_get, tool_ok, tool_err, downsample_series


def _tool_name(context) -> str:
    # AgentCore passes the resolved tool name in the client context custom field.
    try:
        cc = context.client_context
        if cc and cc.custom and "bedrockAgentCoreToolName" in cc.custom:
            raw = cc.custom["bedrockAgentCoreToolName"]
            # Gateway namespaces tools as "<target>___<tool>"
            return raw.split("___")[-1]
    except Exception:
        pass
    return ""


def list_rooms() -> dict:
    rooms = neat_get("/rooms")
    slim = [
        {
            "room_id": r.get("id"),
            "name": r.get("name"),
            "location": r.get("location") or r.get("locationName"),
            "endpoint_ids": r.get("endpointIds") or r.get("endpoints", []),
        }
        for r in (rooms.get("rooms") or rooms.get("value") or rooms.get("data") or [])
    ]
    return tool_ok({"rooms": slim, "count": len(slim)})


def room_sensors(args: dict[str, Any]) -> dict:
    room_id = args.get("room_id")
    if not room_id:
        return tool_err("room_id is required")
    params: dict[str, str] = {}
    if args.get("fromDateTime"):
        params["from"] = args["fromDateTime"]
    if args.get("toDateTime"):
        params["to"] = args["toDateTime"]

    data = neat_get(f"/rooms/{room_id}/sensor", params or None)
    # Normalise the metric bag so the model gets a predictable shape.
    readings = data.get("readings") or data.get("data") or data
    # A multi-day sensor pull is a long per-timestamp series; bound it so it
    # can't overflow the model context (a dict/other shape passes through).
    metrics = downsample_series(readings) if isinstance(readings, list) else readings
    return tool_ok({
        "room_id": room_id,
        "window": {"from": args.get("fromDateTime"), "to": args.get("toDateTime")},
        "metrics": metrics,
    })


def handler(event, context):
    tool = _tool_name(context)
    args = event if isinstance(event, dict) else json.loads(event or "{}")

    if tool == "neat_list_rooms":
        return list_rooms()
    if tool == "neat_room_sensors":
        return room_sensors(args)
    return tool_err(f"unknown tool: {tool!r}")
