"""Lambda target: Microsoft Graph callRecords.

callRecords is application-permission only (CallRecords.Read.All) - this Lambda
authenticates with client credentials. Records surface ~30 min after a call
ends, so this is near-real-time, not live.

Tools:
  graph_list_call_records(fromDateTime, toDateTime)
      -> recent call records (id, start/end, organizer, modalities)
  graph_call_quality(call_id)
      -> per-session media metrics (jitter, packet loss, round-trip, codec)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from _shared.clients import graph_get, tool_ok, tool_err

# callRecords only accepts a $filter window within the last 30 days and not in
# the future (Graph retains call records ~30 days). Clamp so the agent can't 400.
_LOOKBACK = timedelta(days=30)
_SAFETY = timedelta(minutes=10)   # stay strictly inside the 30-day boundary
_MAX_RECORDS = 150                # cap records re-entering the model context


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_name(context) -> str:
    try:
        cc = context.client_context
        if cc and cc.custom and "bedrockAgentCoreToolName" in cc.custom:
            return cc.custom["bedrockAgentCoreToolName"].split("___")[-1]
    except Exception:
        pass
    return ""


def list_call_records(args: dict[str, Any]) -> dict:
    # Default to the full valid window; clamp any supplied dates into
    # [now-30d, now] so we never trip callRecords' 400 (see _LOOKBACK above).
    now = datetime.now(timezone.utc)
    floor = now - _LOOKBACK + _SAFETY

    def _arg(key: str, default: datetime) -> datetime:
        raw = args.get(key)
        if not raw:
            return default
        try:
            return _parse_iso(raw)
        except Exception:
            return default

    to = min(_arg("toDateTime", now), now)
    frm = max(_arg("fromDateTime", floor), floor)
    if frm > to:                       # window entirely outside the valid range
        frm, to = floor, now

    frm_s, to_s = _fmt(frm), _fmt(to)
    # $filter on startDateTime; callRecords rejects $top, so no paging option.
    flt = f"startDateTime ge {frm_s} and startDateTime le {to_s}"
    payload = graph_get("/communications/callRecords", {"$filter": flt})
    rows = [
        {
            "call_id": c.get("id"),
            "start": c.get("startDateTime"),
            "end": c.get("endDateTime"),
            "type": c.get("type"),
            "modalities": c.get("modalities"),
            "organizer": (c.get("organizer_v2") or {}).get("identity", {})
                         .get("user", {}).get("displayName"),
            "join_url_present": bool(c.get("joinWebUrl")),
        }
        for c in payload.get("value", [])
    ]
    # "Everything tagged Teams" over a wide window can return thousands of
    # small records; cap what re-enters the model context. count stays exact.
    total = len(rows)
    out = {"window": {"from": frm_s, "to": to_s}, "records": rows, "count": total}
    if total > _MAX_RECORDS:
        out["records"] = rows[:_MAX_RECORDS]
        out["returned"] = _MAX_RECORDS
        out["truncated"] = True
    return tool_ok(out)


def call_quality(args: dict[str, Any]) -> dict:
    call_id = args.get("call_id")
    if not call_id:
        return tool_err("call_id is required")

    payload = graph_get(f"/communications/callRecords/{call_id}",
                        {"$expand": "sessions($expand=segments)"})

    sessions_out = []
    for s in payload.get("sessions", []):
        for seg in s.get("segments", []):
            media = seg.get("media", [])
            for m in media:
                stream = (m.get("streams") or [{}])[0]
                sessions_out.append({
                    "caller": (s.get("caller") or {}).get("identity", {})
                              .get("user", {}).get("displayName")
                              or (s.get("caller") or {}).get("name"),
                    "label": m.get("label"),
                    "avg_jitter_ms": stream.get("averageJitter"),
                    "max_jitter_ms": stream.get("maxJitter"),
                    "avg_packet_loss": stream.get("averagePacketLossRate"),
                    "avg_round_trip_ms": stream.get("averageRoundTripTime"),
                    "codec": stream.get("audioCodec") or stream.get("videoCodec"),
                })
    return tool_ok({
        "call_id": call_id,
        "start": payload.get("startDateTime"),
        "end": payload.get("endDateTime"),
        "media_streams": sessions_out,
        "stream_count": len(sessions_out),
    })


def handler(event, context):
    tool = _tool_name(context)
    args = event if isinstance(event, dict) else json.loads(event or "{}")

    if tool == "graph_list_call_records":
        return list_call_records(args)
    if tool == "graph_call_quality":
        return call_quality(args)
    return tool_err(f"unknown tool: {tool!r}")
