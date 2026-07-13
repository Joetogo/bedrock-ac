"""Lambda target: correlate Neat room conditions with Teams call quality.

The join key is room + time window. Neat Sense gives occupancy/environment per
space; Graph gives media quality per session. This tool stitches them so the
model can answer questions like "did poor air quality in Room 4 line up with
worse call quality?".

Tool:
  correlate_room_calls(room_id, fromDateTime, toDateTime)
"""
from __future__ import annotations

import json
from typing import Any

from _shared.clients import neat_get, graph_get, tool_ok, tool_err


def _avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def correlate(args: dict[str, Any]) -> dict:
    room_id = args.get("room_id")
    frm, to = args.get("fromDateTime"), args.get("toDateTime")
    if not (room_id and frm and to):
        return tool_err("room_id, fromDateTime, toDateTime are required")

    # 1. Neat side: room metadata + sensor readings in window.
    room = neat_get(f"/rooms/{room_id}")
    room_name = room.get("name", "")
    sensors = neat_get(f"/rooms/{room_id}/sensors", {"from": frm, "to": to})
    readings = sensors.get("readings") or sensors.get("data") or []

    env = {
        "avg_co2": _avg([r.get("co2") for r in readings]),
        "avg_temp": _avg([r.get("temperature") for r in readings]),
        "avg_humidity": _avg([r.get("humidity") for r in readings]),
        "max_people": max([r.get("peopleCount", 0) or 0 for r in readings], default=0),
        "avg_voc": _avg([r.get("voc") for r in readings]),
        "sample_count": len(readings),
    }

    # 2. Graph side: call records whose organizer/room maps to this space.
    flt = f"startDateTime ge {frm} and startDateTime le {to}"
    # NB: callRecords rejects $top ("Query option 'Top' is not allowed") — filter only.
    recs = graph_get("/communications/callRecords",
                     {"$filter": flt}).get("value", [])

    # Best-effort room match: Teams Rooms accounts usually carry the space name.
    matched = [
        c for c in recs
        if room_name and room_name.lower() in json.dumps(c).lower()
    ]

    call_quality = []
    for c in matched[:10]:  # cap fan-out for the POC
        detail = graph_get(f"/communications/callRecords/{c['id']}",
                           {"$expand": "sessions($expand=segments)"})
        jit, loss, rtt = [], [], []
        for s in detail.get("sessions", []):
            for seg in s.get("segments", []):
                for m in seg.get("media", []):
                    st = (m.get("streams") or [{}])[0]
                    jit.append(st.get("averageJitter"))
                    loss.append(st.get("averagePacketLossRate"))
                    rtt.append(st.get("averageRoundTripTime"))
        call_quality.append({
            "call_id": c["id"],
            "start": c.get("startDateTime"),
            "avg_jitter_ms": _avg(jit),
            "avg_packet_loss": _avg(loss),
            "avg_round_trip_ms": _avg(rtt),
        })

    return tool_ok({
        "room": {"id": room_id, "name": room_name},
        "window": {"from": frm, "to": to},
        "environment": env,
        "calls": call_quality,
        "calls_matched": len(matched),
        "note": ("Room match is name-substring based for the POC; for production "
                 "map Teams Rooms UPNs to Neat space ids explicitly."),
    })


def handler(event, context):
    args = event if isinstance(event, dict) else json.loads(event or "{}")
    return correlate(args)
