"""Lambda target: Cisco ThousandEyes v7 test results and alerts.

Wraps the ThousandEyes v7 REST API (long-lived bearer token, injected
server-side via Secrets Manager). The AgentCore Gateway passes the MCP tool
name in ``context.client_context.custom['bedrockAgentCoreToolName']`` (formatted
``<target>___<tool>``) and the tool arguments as the Lambda ``event`` dict.

ThousandEyes v7 quirks baked in here:
  * time window params are ``startDate`` / ``endDate`` (ISO-8601 UTC), NOT from/to
  * voice/RTP results live at ``/test-results/{id}/rtp-server``
  * the base URL already includes ``/v7`` (see TE_BASE in _shared.clients)

Tools:
  te_list_tests_alerts(fromDateTime?, toDateTime?)
      -> configured tests + active alerts in the window
  te_network_results(test_id, fromDateTime, toDateTime)
      -> loss / latency / jitter per agent
  te_voice_results(test_id, fromDateTime, toDateTime)
      -> RTP server metrics (MOS / jitter / loss / latency)
  te_path_visualization(test_id, fromDateTime?, toDateTime?)
      -> hop-by-hop path with per-hop latency/loss
"""
from __future__ import annotations

import json
from typing import Any

from _shared.clients import te_get, tool_ok, tool_err, downsample_series


def _series_key(rows) -> str | None:
    """Pick the field that identifies each agent's own time series so the
    downsampler can keep a per-agent trend instead of one blended line."""
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for k in ("agentName", "agentId", "agent", "server", "serverId"):
            if k in rows[0]:
                return k
    return None


def _tool_name(context) -> str:
    try:
        cc = context.client_context
        if cc and cc.custom and "bedrockAgentCoreToolName" in cc.custom:
            return cc.custom["bedrockAgentCoreToolName"].split("___")[-1]
    except Exception:
        pass
    return ""


def _window(args: dict[str, Any]) -> dict:
    # ThousandEyes v7 uses startDate/endDate (ISO-8601 UTC).
    p = {}
    if args.get("fromDateTime"):
        p["startDate"] = args["fromDateTime"]
    if args.get("toDateTime"):
        p["endDate"] = args["toDateTime"]
    return p


def list_tests_alerts(args: dict[str, Any]) -> dict:
    tests = te_get("/tests")
    alerts = te_get("/alerts", _window(args) or None)
    return tool_ok({
        "tests": tests.get("tests", tests),
        "alerts": alerts.get("alerts", alerts),
    })


def network_results(args: dict[str, Any]) -> dict:
    tid = args.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/network", _window(args) or None)
    results = data.get("results", data)
    return tool_ok({"test_id": tid,
                    "results": downsample_series(results, group_by=_series_key(results))})


def voice_results(args: dict[str, Any]) -> dict:
    tid = args.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/rtp-server", _window(args) or None)
    results = data.get("results", data)
    return tool_ok({"test_id": tid,
                    "results": downsample_series(results, group_by=_series_key(results))})


def path_visualization(args: dict[str, Any]) -> dict:
    tid = args.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/path-vis", _window(args) or None)
    results = data.get("results", data)
    # Path-vis has no per-agent trend to preserve; a flat decimation is enough
    # to keep a large hop list from overflowing the context.
    return tool_ok({"test_id": tid, "pathVis": downsample_series(results)})


_TOOLS = {
    "te_list_tests_alerts": list_tests_alerts,
    "te_network_results": network_results,
    "te_voice_results": voice_results,
    "te_path_visualization": path_visualization,
}


def handler(event, context):
    tool = _tool_name(context)
    args = event if isinstance(event, dict) else json.loads(event or "{}")
    fn = _TOOLS.get(tool)
    if fn is None:
        return tool_err(f"unknown tool: {tool!r}")
    try:
        return fn(args)
    except Exception as e:  # noqa: BLE001
        return tool_err(f"{type(e).__name__}: {e}")
