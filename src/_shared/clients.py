"""Shared clients and helpers for the Neat x Graph correlation tools.

These run inside AgentCore Gateway *Lambda targets*. The Gateway passes tool
arguments as the Lambda `event` and tool metadata via the `context` client
context. Each handler just returns plain JSON.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

import boto3

_secrets = boto3.client("secretsmanager")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
NEAT_BASE = os.environ.get("NEAT_BASE", "https://api.pulse.neat.no/v1")


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def get_secret(arn_or_name: str) -> dict[str, Any]:
    resp = _secrets.get_secret_value(SecretId=arn_or_name)
    return json.loads(resp["SecretString"])


# --------------------------------------------------------------------------- #
# HTTP (stdlib only - keeps the Lambda zip tiny, no requests dependency)
# --------------------------------------------------------------------------- #
def _http(method: str, url: str, headers: dict[str, str],
          body: bytes | None = None, timeout: int = 20) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return e.code, {"error": "http_error", "status": e.code, "detail": detail[:2000]}


# --------------------------------------------------------------------------- #
# Microsoft Graph - client credentials (app-only). callRecords is app-only.
# --------------------------------------------------------------------------- #
_graph_token: dict[str, Any] = {"value": None, "exp": 0}


def graph_token() -> str:
    now = time.time()
    if _graph_token["value"] and now < _graph_token["exp"] - 60:
        return _graph_token["value"]

    sec = get_secret(os.environ["GRAPH_SECRET_ARN"])
    tenant = sec["tenant_id"]
    data = urllib.parse.urlencode({
        "client_id": sec["client_id"],
        "client_secret": sec["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode()

    status, payload = _http(
        "POST",
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        {"Content-Type": "application/x-www-form-urlencoded"},
        data,
    )
    if status != 200:
        raise RuntimeError(f"graph token failed: {payload}")
    _graph_token["value"] = payload["access_token"]
    _graph_token["exp"] = now + int(payload.get("expires_in", 3600))
    return _graph_token["value"]


def graph_get(path: str, params: dict[str, str] | None = None) -> dict:
    url = f"{GRAPH_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="(),:='")
    status, payload = _http("GET", url, {
        "Authorization": f"Bearer {graph_token()}",
        "Accept": "application/json",
    })
    if status >= 400:
        raise RuntimeError(f"graph GET {path} -> {status}: {payload}")
    return payload


# --------------------------------------------------------------------------- #
# Neat Pulse / Neat Sense - org-scoped Bearer API key
# --------------------------------------------------------------------------- #
def neat_get(path: str, params: dict[str, str] | None = None) -> dict:
    sec = get_secret(os.environ["NEAT_SECRET_ARN"])
    org = sec["org_id"]
    url = f"{NEAT_BASE}/orgs/{org}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, payload = _http("GET", url, {
        "Authorization": f"Bearer {sec['api_key']}",
        "Accept": "application/json",
    })
    if status >= 400:
        raise RuntimeError(f"neat GET {path} -> {status}: {payload}")
    return payload


# --------------------------------------------------------------------------- #
# Cisco ThousandEyes - long-lived API bearer token (v7 REST API)
# --------------------------------------------------------------------------- #
TE_BASE = os.environ.get("TE_BASE", "https://api.thousandeyes.com/v7")


def te_get(path: str, params: dict[str, str] | None = None) -> dict:
    sec = get_secret(os.environ["THOUSANDEYES_SECRET_ARN"])
    url = f"{TE_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # ThousandEyes v7 uses an OAuth Bearer token (no username). The token is
    # injected server-side from Secrets Manager; the model never sees it.
    status, payload = _http("GET", url, {
        "Authorization": f"Bearer {sec['bearer_token']}",
        "Accept": "application/json",
    })
    if status >= 400:
        raise RuntimeError(f"thousandeyes GET {path} -> {status}: {payload}")
    return payload


# --------------------------------------------------------------------------- #
# Payload downsampler - keeps tool output from overflowing the model context
# --------------------------------------------------------------------------- #
# Metric endpoints (ThousandEyes network/voice, Neat sensors) return a raw
# per-round time series - thousands of rows for a week-long window. Feeding
# that verbatim into the model blew the context window, and the framework's
# overflow recovery then trimmed the user's own question out of the
# conversation ("your message came through empty"). downsample_series compresses
# any oversized list into summary stats over ALL rows plus a decimated series,
# so a week of data costs a few KB instead of hundreds. It only reads and
# reshapes what was already fetched - no new upstream calls, still read-only.
def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_fields(rows: list) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if k not in seen and _is_num(v):
                seen.add(k)
                fields.append(k)
    return fields


def _pctl(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 4)


def _stats(rows: list, fields: list[str]) -> dict:
    out: dict[str, dict] = {}
    for f in fields:
        vals = sorted(r[f] for r in rows if isinstance(r, dict) and _is_num(r.get(f)))
        if not vals:
            continue
        out[f] = {
            "n": len(vals),
            "min": round(vals[0], 4),
            "max": round(vals[-1], 4),
            "avg": round(sum(vals) / len(vals), 4),
            "p95": _pctl(vals, 0.95),
        }
    return out


def _decimate(rows: list, max_points: int) -> list:
    n = len(rows)
    if n <= max_points:
        return list(rows)
    stride = n / max_points
    idxs = sorted({min(int(i * stride), n - 1) for i in range(max_points)})
    if idxs[-1] != n - 1:          # always keep the most recent point
        idxs[-1] = n - 1
    return [rows[i] for i in idxs]


# Floor on points kept per group so a many-agent test still yields a usable
# trend line (60 total / 14 agents would otherwise be ~4 points each). Stats
# are always over ALL rows, so accuracy is unaffected by this resolution knob.
_MIN_SERIES_POINTS = 10


def _group_key(row: Any, key: str):
    if not isinstance(row, dict):
        return None
    v = row.get(key)
    if isinstance(v, dict):        # e.g. {"agent": {"agentName": ...}}
        v = v.get("agentName") or v.get("agentId") or v.get("name")
    return v if isinstance(v, (str, int, float)) else None


def downsample_series(rows: Any, *, group_by: str | None = None,
                      max_points: int = 60, max_groups: int = 30) -> dict:
    """Compress a large list of metric rows so it can't overflow the context.

    Returns one of three shapes:
      * ``{"points", "count", "sampled": False}``      - list already small
      * ``{"stats", "series", "count", "sampled": True}`` - flat, decimated
      * ``{"by", "groups": [...], "count", "sampled": True}`` - per-series

    ``stats`` are computed over every row; ``series`` is decimated to at most
    ``max_points`` (per group when grouped). Derived from the fetched rows
    only - no additional upstream reads.
    """
    if not isinstance(rows, list) or len(rows) <= max_points:
        return {"points": rows, "sampled": False,
                "count": len(rows) if isinstance(rows, list) else 0}

    fields = _numeric_fields(rows)

    if group_by:
        groups: dict = {}
        for r in rows:
            k = _group_key(r, group_by)
            if k is not None:
                groups.setdefault(k, []).append(r)
        if 2 <= len(groups) <= max_groups:
            per = max(_MIN_SERIES_POINTS, max_points // len(groups))
            return {
                "by": group_by,
                "sampled": True,
                "count": len(rows),
                "points_per_group": per,
                "groups": [
                    {"group": gk, "count": len(grows),
                     "stats": _stats(grows, fields),
                     "series": _decimate(grows, per)}
                    for gk, grows in groups.items()
                ],
            }

    return {"stats": _stats(rows, fields), "series": _decimate(rows, max_points),
            "count": len(rows), "sampled": True}


# --------------------------------------------------------------------------- #
# AgentCore Lambda-target response helper
# --------------------------------------------------------------------------- #
def tool_ok(data: Any) -> dict:
    """AgentCore Gateway Lambda targets just return JSON; the Gateway wraps it
    into an MCP toolResult. Keep payloads compact - they re-enter the model
    context as untrusted tool output."""
    return {"ok": True, "data": data}


def tool_err(msg: str) -> dict:
    return {"ok": False, "error": msg}
