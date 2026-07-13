# Cisco Webex + ThousandEyes Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Cisco Webex and Cisco ThousandEyes as two new source Lambdas (8 new MCP tools) on the existing Bedrock AgentCore Gateway, and evolve `correlate` into a map-driven multi-source join.

**Architecture:** Mirror the existing one-Lambda-per-source pattern. Two new Lambda targets (`webex`, `thousandeyes`) wrap upstream REST APIs; credentials live in Secrets Manager and are injected server-side. A checked-in `config/locations.json` explicitly keys the four sources (Neat, Webex, Graph, ThousandEyes) to sites/rooms, and `correlate` reads it to fan out and join.

**Tech Stack:** Python 3.12 Lambdas (stdlib-only HTTP via `urllib`, matching `_shared/clients.py`), AWS SAM, boto3, AgentCore Gateway, pytest (fully mocked).

## Global Constraints

- **Stdlib-only HTTP in Lambdas** — use the existing `_http()` helper in `_shared/clients.py`; do NOT add `requests`/`httpx` to Lambda code (keeps the zip tiny). `httpx` is only for `scripts/`.
- **Secrets never reach the model** — tool handlers read secrets via `get_secret()` and return only shaped JSON.
- **Response shaping** — every tool returns `tool_ok(data)` / `tool_err(msg)` from `_shared/clients.py`. Keep payloads compact (they re-enter model context).
- **Graceful degradation** — a missing id, absent Webex Pro Pack, or missing permission returns `tool_err`/`null`, never an unhandled exception.
- **Tests are fully mocked** — mock at the `webex_get` / `te_get` / `neat_get` / `graph_get` boundary; no live network in tests.
- **Region/base URLs via env** — `NEAT_BASE` pattern already exists; add `TE_BASE` and `WEBEX_BASE` with sane defaults.
- **Secret names:** `neat-graph-bedrock/webex`, `neat-graph-bedrock/thousandeyes`.

---

### Task 1: ThousandEyes client helper (`te_get`)

**Files:**
- Modify: `src/_shared/clients.py` (add `TE_BASE` const + `te_get()` after the Neat section)
- Test: `tests/test_clients_te.py`

**Interfaces:**
- Consumes: existing `_http()`, `get_secret()`.
- Produces: `te_get(path: str, params: dict[str, str] | None = None) -> dict` — GETs `{TE_BASE}{path}` with `Authorization: Bearer <bearer_token>` from secret `THOUSANDEYES_SECRET_ARN` (`{"bearer_token": "..."}`); raises `RuntimeError` on status >= 400.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clients_te.py
import os
from unittest import mock
import pytest
import importlib


def _load_clients():
    import src._shared.clients as clients
    return importlib.reload(clients)


def test_te_get_sends_bearer_and_returns_payload():
    os.environ["THOUSANDEYES_SECRET_ARN"] = "arn:te"
    clients = _load_clients()
    with mock.patch.object(clients, "get_secret", return_value={"bearer_token": "TOK"}) as gs, \
         mock.patch.object(clients, "_http", return_value=(200, {"tests": []})) as http:
        out = clients.te_get("/tests", {"aid": "9"})
    gs.assert_called_once_with("arn:te")
    called_url = http.call_args.args[1]
    assert called_url.startswith("https://api.thousandeyes.com/v7/tests")
    assert "aid=9" in called_url
    headers = http.call_args.args[2]
    assert headers["Authorization"] == "Bearer TOK"
    assert out == {"tests": []}


def test_te_get_raises_on_error_status():
    os.environ["THOUSANDEYES_SECRET_ARN"] = "arn:te"
    clients = _load_clients()
    with mock.patch.object(clients, "get_secret", return_value={"bearer_token": "TOK"}), \
         mock.patch.object(clients, "_http", return_value=(403, {"error": "nope"})):
        with pytest.raises(RuntimeError):
            clients.te_get("/tests")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clients_te.py -v`
Expected: FAIL with `AttributeError: module 'src._shared.clients' has no attribute 'te_get'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/_shared/clients.py` (after the Neat section, before the response-helper section):

```python
# --------------------------------------------------------------------------- #
# Cisco ThousandEyes - long-lived API bearer token
# --------------------------------------------------------------------------- #
TE_BASE = os.environ.get("TE_BASE", "https://api.thousandeyes.com/v7")


def te_get(path: str, params: dict[str, str] | None = None) -> dict:
    sec = get_secret(os.environ["THOUSANDEYES_SECRET_ARN"])
    url = f"{TE_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, payload = _http("GET", url, {
        "Authorization": f"Bearer {sec['bearer_token']}",
        "Accept": "application/json",
    })
    if status >= 400:
        raise RuntimeError(f"thousandeyes GET {path} -> {status}: {payload}")
    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clients_te.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit** (skip if git not initialized; otherwise:)

```bash
git add src/_shared/clients.py tests/test_clients_te.py
git commit -m "feat: add ThousandEyes te_get client helper"
```

---

### Task 2: Webex client helper (`webex_get`) with refresh-token cache

**Files:**
- Modify: `src/_shared/clients.py` (add `WEBEX_BASE` + `_webex_token` cache + `webex_token()` + `webex_get()`)
- Test: `tests/test_clients_webex.py`

**Interfaces:**
- Consumes: `_http()`, `get_secret()`.
- Produces:
  - `webex_token() -> str` — exchanges the stored `refresh_token` for an access token via `https://webexapis.com/v1/access_token` (grant_type=refresh_token), caches it in module-level `_webex_token` until 60s before expiry.
  - `webex_get(path: str, params: dict[str, str] | None = None) -> dict` — GETs `{WEBEX_BASE}{path}` with `Authorization: Bearer <access_token>`; raises `RuntimeError` on status >= 400.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clients_webex.py
import os
import importlib
from unittest import mock
import pytest


def _load_clients():
    import src._shared.clients as clients
    return importlib.reload(clients)


def test_webex_token_refreshes_and_caches():
    os.environ["WEBEX_SECRET_ARN"] = "arn:webex"
    clients = _load_clients()
    secret = {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
    with mock.patch.object(clients, "get_secret", return_value=secret), \
         mock.patch.object(clients, "_http",
                           return_value=(200, {"access_token": "AT", "expires_in": 3600})) as http:
        t1 = clients.webex_token()
        t2 = clients.webex_token()  # second call served from cache
    assert t1 == "AT" and t2 == "AT"
    assert http.call_count == 1  # only one token exchange


def test_webex_get_uses_token_and_returns_payload():
    os.environ["WEBEX_SECRET_ARN"] = "arn:webex"
    clients = _load_clients()
    secret = {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
    with mock.patch.object(clients, "get_secret", return_value=secret), \
         mock.patch.object(clients, "webex_token", return_value="AT"), \
         mock.patch.object(clients, "_http", return_value=(200, {"items": [1]})) as http:
        out = clients.webex_get("/devices", {"max": "10"})
    url = http.call_args.args[1]
    assert url.startswith("https://webexapis.com/v1/devices")
    assert "max=10" in url
    assert http.call_args.args[2]["Authorization"] == "Bearer AT"
    assert out == {"items": [1]}


def test_webex_get_raises_on_error():
    os.environ["WEBEX_SECRET_ARN"] = "arn:webex"
    clients = _load_clients()
    with mock.patch.object(clients, "webex_token", return_value="AT"), \
         mock.patch.object(clients, "_http", return_value=(400, {"error": "bad"})):
        with pytest.raises(RuntimeError):
            clients.webex_get("/devices")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_clients_webex.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'webex_token'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/_shared/clients.py` (after the ThousandEyes section):

```python
# --------------------------------------------------------------------------- #
# Cisco Webex - service-app OAuth (refresh-token grant)
# --------------------------------------------------------------------------- #
WEBEX_BASE = os.environ.get("WEBEX_BASE", "https://webexapis.com/v1")
_webex_token: dict[str, Any] = {"value": None, "exp": 0}


def webex_token() -> str:
    now = time.time()
    if _webex_token["value"] and now < _webex_token["exp"] - 60:
        return _webex_token["value"]

    sec = get_secret(os.environ["WEBEX_SECRET_ARN"])
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": sec["client_id"],
        "client_secret": sec["client_secret"],
        "refresh_token": sec["refresh_token"],
    }).encode()
    status, payload = _http(
        "POST", "https://webexapis.com/v1/access_token",
        {"Content-Type": "application/x-www-form-urlencoded"}, data,
    )
    if status != 200:
        raise RuntimeError(f"webex token failed: {payload}")
    _webex_token["value"] = payload["access_token"]
    _webex_token["exp"] = now + int(payload.get("expires_in", 3600))
    return _webex_token["value"]


def webex_get(path: str, params: dict[str, str] | None = None) -> dict:
    url = f"{WEBEX_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, payload = _http("GET", url, {
        "Authorization": f"Bearer {webex_token()}",
        "Accept": "application/json",
    })
    if status >= 400:
        raise RuntimeError(f"webex GET {path} -> {status}: {payload}")
    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_clients_webex.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/_shared/clients.py tests/test_clients_webex.py
git commit -m "feat: add Webex client helper with refresh-token cache"
```

---

### Task 3: Location map loader + `config/locations.json`

**Files:**
- Create: `config/locations.json`
- Modify: `src/_shared/clients.py` (add `load_location_map()` + `resolve_location()`)
- Test: `tests/test_location_map.py`

**Interfaces:**
- Produces:
  - `load_location_map() -> dict` — reads JSON from `os.environ["LOCATIONS_PATH"]` (default `config/locations.json`), `lru_cache`d.
  - `resolve_location(m: dict, site: str | None, room: str | None) -> dict` — returns `{"site": <site_obj>, "room": <room_obj_or_None>}`; raises `KeyError` if neither matches. Matching is exact on `site` and `room` names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_location_map.py
import json
import os
import importlib
import pytest


def _load_clients():
    import src._shared.clients as clients
    return importlib.reload(clients)


def _write_map(tmp_path):
    m = {"sites": [{
        "site": "sydney-hq",
        "thousandeyes": {"network_test_id": "1", "voice_test_id": "2"},
        "rooms": [{"room": "L5 Boardroom", "neat_space_id": "sp_a",
                   "webex_workspace_id": "wx_a", "teams_room_upn": "l5@x.com"}],
    }]}
    p = tmp_path / "locations.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    return str(p)


def test_load_and_resolve_site(tmp_path):
    os.environ["LOCATIONS_PATH"] = _write_map(tmp_path)
    clients = _load_clients()
    m = clients.load_location_map()
    r = clients.resolve_location(m, site="sydney-hq", room=None)
    assert r["site"]["thousandeyes"]["voice_test_id"] == "2"
    assert r["room"] is None


def test_resolve_room(tmp_path):
    os.environ["LOCATIONS_PATH"] = _write_map(tmp_path)
    clients = _load_clients()
    m = clients.load_location_map()
    r = clients.resolve_location(m, site=None, room="L5 Boardroom")
    assert r["room"]["neat_space_id"] == "sp_a"
    assert r["site"]["site"] == "sydney-hq"


def test_resolve_unknown_raises(tmp_path):
    os.environ["LOCATIONS_PATH"] = _write_map(tmp_path)
    clients = _load_clients()
    m = clients.load_location_map()
    with pytest.raises(KeyError):
        clients.resolve_location(m, site="nope", room=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_location_map.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'load_location_map'`

- [ ] **Step 3: Write minimal implementation**

Create `config/locations.json`:

```json
{
  "sites": [
    {
      "site": "sydney-hq",
      "thousandeyes": { "network_test_id": "", "voice_test_id": "" },
      "rooms": [
        {
          "room": "Sydney L5 Boardroom",
          "neat_space_id": "",
          "webex_workspace_id": "",
          "teams_room_upn": ""
        }
      ]
    }
  ]
}
```

Add to `src/_shared/clients.py` (add `from pathlib import Path` to the imports if not present):

```python
from pathlib import Path


@lru_cache(maxsize=1)
def load_location_map() -> dict[str, Any]:
    path = os.environ.get("LOCATIONS_PATH", "config/locations.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_location(m: dict, site: str | None, room: str | None) -> dict:
    for s in m.get("sites", []):
        if site is not None and s.get("site") == site:
            return {"site": s, "room": None}
        for r in s.get("rooms", []):
            if room is not None and r.get("room") == room:
                return {"site": s, "room": r}
    raise KeyError(f"no location match for site={site!r} room={room!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_location_map.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/_shared/clients.py config/locations.json tests/test_location_map.py
git commit -m "feat: add location map loader and config/locations.json"
```

---

### Task 4: ThousandEyes Lambda handler (4 tools)

**Files:**
- Create: `src/thousandeyes/handler.py`
- Create: `src/thousandeyes/requirements.txt` (comment-only — stdlib + shared layer)
- Test: `tests/test_thousandeyes.py`

**Interfaces:**
- Consumes: `te_get()` from Task 1, `tool_ok`/`tool_err`.
- Produces `handler(event, context)` dispatching on `event['tool']`; arguments on the flat `event` dict. Tools: `te_list_tests_alerts`, `te_network_results`, `te_voice_results`, `te_path_visualization`.

Read `src/graph_calls/handler.py` first to copy the exact event-dispatch convention this repo uses, then mirror it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thousandeyes.py
import importlib
from unittest import mock


def _load():
    import src.thousandeyes.handler as h
    return importlib.reload(h)


def test_list_tests_alerts_shape():
    h = _load()
    with mock.patch("src.thousandeyes.handler.te_get") as te:
        te.side_effect = [
            {"tests": [{"testId": 1, "testName": "voip"}]},
            {"alerts": [{"alertId": 7, "active": True}]},
        ]
        out = h.handler({"tool": "te_list_tests_alerts",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T01:00:00Z"}, None)
    assert out["ok"] is True
    assert out["data"]["tests"][0]["testName"] == "voip"
    assert out["data"]["alerts"][0]["alertId"] == 7


def test_network_results_requires_test_id():
    h = _load()
    out = h.handler({"tool": "te_network_results"}, None)
    assert out["ok"] is False


def test_voice_results_shape():
    h = _load()
    with mock.patch("src.thousandeyes.handler.te_get",
                    return_value={"results": [{"mos": 4.1, "jitter": 3, "loss": 0.0}]}):
        out = h.handler({"tool": "te_voice_results", "test_id": "2",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T01:00:00Z"}, None)
    assert out["ok"] is True
    assert out["data"]["results"][0]["mos"] == 4.1


def test_unknown_tool():
    h = _load()
    out = h.handler({"tool": "nope"}, None)
    assert out["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thousandeyes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.thousandeyes'`

- [ ] **Step 3: Write minimal implementation**

Create `src/thousandeyes/requirements.txt`:

```
# stdlib only; ThousandEyes access via the shared layer's te_get()
```

Create `src/thousandeyes/handler.py`:

```python
"""AgentCore Lambda target: Cisco ThousandEyes tools.

Wraps the ThousandEyes v7 API (bearer token injected server-side) and returns
compact JSON. Tool arguments arrive on the flat Lambda ``event`` dict; the tool
name is in ``event['tool']`` (set by the Gateway from the MCP tool name).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.clients import te_get, tool_ok, tool_err  # noqa: E402


def _window(event) -> dict:
    p = {}
    if event.get("fromDateTime"):
        p["from"] = event["fromDateTime"]
    if event.get("toDateTime"):
        p["to"] = event["toDateTime"]
    return p


def te_list_tests_alerts(event) -> dict:
    tests = te_get("/tests")
    alerts = te_get("/alerts", _window(event) or None)
    return tool_ok({"tests": tests.get("tests", tests),
                    "alerts": alerts.get("alerts", alerts)})


def te_network_results(event) -> dict:
    tid = event.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/network", _window(event) or None)
    return tool_ok(data)


def te_voice_results(event) -> dict:
    tid = event.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/voice", _window(event) or None)
    return tool_ok(data)


def te_path_visualization(event) -> dict:
    tid = event.get("test_id")
    if not tid:
        return tool_err("test_id is required")
    data = te_get(f"/test-results/{tid}/path-vis", _window(event) or None)
    return tool_ok(data)


_TOOLS = {
    "te_list_tests_alerts": te_list_tests_alerts,
    "te_network_results": te_network_results,
    "te_voice_results": te_voice_results,
    "te_path_visualization": te_path_visualization,
}


def handler(event, context):
    fn = _TOOLS.get(event.get("tool"))
    if fn is None:
        return tool_err(f"unknown tool: {event.get('tool')}")
    try:
        return fn(event)
    except Exception as e:  # noqa: BLE001
        return tool_err(f"{type(e).__name__}: {e}")
```

> **Verify against the repo:** confirm the real event-dispatch convention by reading `src/graph_calls/handler.py`. If this repo keys the tool name differently (e.g. from `context.client_context` rather than `event['tool']`), adjust `handler()` and the tests to match — the tool bodies stay the same.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thousandeyes.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/thousandeyes/ tests/test_thousandeyes.py
git commit -m "feat: add ThousandEyes Lambda handler (4 tools)"
```

---

### Task 5: Webex Lambda handler (4 tools)

**Files:**
- Create: `src/webex/handler.py`
- Create: `src/webex/requirements.txt` (comment-only, stdlib + shared layer)
- Test: `tests/test_webex.py`

**Interfaces:**
- Consumes: `webex_get()` from Task 2, `tool_ok`/`tool_err`.
- Produces `handler(event, context)` dispatching tools: `webex_list_meetings`, `webex_meeting_quality`, `webex_device_presence`, `webex_workspace_environment`. `webex_meeting_quality` returns `tool_err` if Webex responds 402/403 (Pro Pack / permission).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webex.py
import importlib
from unittest import mock


def _load():
    import src.webex.handler as h
    return importlib.reload(h)


def test_list_meetings_shape():
    h = _load()
    with mock.patch("src.webex.handler.webex_get",
                    return_value={"items": [{"id": "m1"}, {"id": "m2"}]}):
        out = h.handler({"tool": "webex_list_meetings",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T02:00:00Z"}, None)
    assert out["ok"] is True
    assert [m["id"] for m in out["data"]["items"]] == ["m1", "m2"]


def test_meeting_quality_requires_id():
    h = _load()
    out = h.handler({"tool": "webex_meeting_quality"}, None)
    assert out["ok"] is False


def test_meeting_quality_pro_pack_missing_returns_tool_err():
    h = _load()
    with mock.patch("src.webex.handler.webex_get",
                    side_effect=RuntimeError("webex GET /meeting/qualities -> 403: {}")):
        out = h.handler({"tool": "webex_meeting_quality", "meetingId": "m1"}, None)
    assert out["ok"] is False
    assert "Pro Pack" in out["error"] or "403" in out["error"]


def test_device_presence_shape():
    h = _load()
    with mock.patch("src.webex.handler.webex_get",
                    return_value={"items": [{"id": "d1", "connectionStatus": "connected"}]}):
        out = h.handler({"tool": "webex_device_presence"}, None)
    assert out["ok"] is True
    assert out["data"]["items"][0]["connectionStatus"] == "connected"


def test_workspace_environment_requires_workspace_id():
    h = _load()
    out = h.handler({"tool": "webex_workspace_environment"}, None)
    assert out["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.webex'`

- [ ] **Step 3: Write minimal implementation**

Create `src/webex/requirements.txt`:

```
# stdlib only; Webex access via the shared layer's webex_get()
```

Create `src/webex/handler.py`:

```python
"""AgentCore Lambda target: Cisco Webex tools.

Wraps the Webex REST API (service-app OAuth token injected server-side). Tool
name arrives in ``event['tool']``; arguments on the flat ``event`` dict.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.clients import webex_get, tool_ok, tool_err  # noqa: E402


def webex_list_meetings(event) -> dict:
    frm, to = event.get("fromDateTime"), event.get("toDateTime")
    if not frm or not to:
        return tool_err("fromDateTime and toDateTime are required")
    data = webex_get("/meetings", {"from": frm, "to": to, "meetingType": "meeting"})
    return tool_ok(data)


def webex_meeting_quality(event) -> dict:
    mid = event.get("meetingId")
    if not mid:
        return tool_err("meetingId is required")
    try:
        data = webex_get("/meeting/qualities", {"meetingId": mid})
    except RuntimeError as e:
        if "403" in str(e) or "402" in str(e):
            return tool_err("meeting quality requires Webex Pro Pack / admin scope")
        raise
    return tool_ok(data)


def webex_device_presence(event) -> dict:
    data = webex_get("/devices")
    return tool_ok(data)


def webex_workspace_environment(event) -> dict:
    wid = event.get("workspace_id")
    if not wid:
        return tool_err("workspace_id is required")
    params = {"workspaceId": wid}
    if event.get("fromDateTime"):
        params["from"] = event["fromDateTime"]
    if event.get("toDateTime"):
        params["to"] = event["toDateTime"]
    data = webex_get("/workspaceMetrics", params)
    return tool_ok(data)


_TOOLS = {
    "webex_list_meetings": webex_list_meetings,
    "webex_meeting_quality": webex_meeting_quality,
    "webex_device_presence": webex_device_presence,
    "webex_workspace_environment": webex_workspace_environment,
}


def handler(event, context):
    fn = _TOOLS.get(event.get("tool"))
    if fn is None:
        return tool_err(f"unknown tool: {event.get('tool')}")
    try:
        return fn(event)
    except Exception as e:  # noqa: BLE001
        return tool_err(f"{type(e).__name__}: {e}")
```

> **Verify against the live API:** the exact Webex paths (`/workspaceMetrics`, `/meeting/qualities`) and their query params should be confirmed against the Webex API when credentials are available. The tests mock `webex_get`, so they stay valid; only the path/param strings may need a one-line tweak.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webex.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/webex/ tests/test_webex.py
git commit -m "feat: add Webex Lambda handler (4 tools)"
```

---

### Task 6: Evolve `correlate` into a map-driven multi-source join

**Files:**
- Modify: `src/correlate/handler.py`
- Test: `tests/test_correlate.py` (extend/replace)

**Interfaces:**
- Consumes: `neat_get`/`graph_get`/`webex_get`/`te_get`, `load_location_map`, `resolve_location`, `tool_ok`/`tool_err`.
- Produces tool `correlate(site?, room?, fromDateTime, toDateTime)` returning
  `tool_ok({site, room, window, environment, meeting_quality, call_quality, network, path_summary, note})`.
  Each source is wrapped in a local `_safe(fn)` that returns `None` on any exception so a missing id/permission degrades gracefully.

Read the current `src/correlate/handler.py` first to preserve its existing dispatch and the exact Neat/Graph call shapes it already uses.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correlate.py
import importlib
from unittest import mock


def _load():
    import src.correlate.handler as h
    return importlib.reload(h)


_SITE = {
    "site": "sydney-hq",
    "thousandeyes": {"network_test_id": "1", "voice_test_id": "2"},
    "rooms": [{"room": "L5 Boardroom", "neat_space_id": "sp_a",
               "webex_workspace_id": "wx_a", "teams_room_upn": "l5@x.com"}],
}


def test_correlate_joins_all_sources():
    h = _load()
    with mock.patch("src.correlate.handler.load_location_map", return_value={"sites": [_SITE]}), \
         mock.patch("src.correlate.handler.resolve_location",
                    return_value={"site": _SITE, "room": _SITE["rooms"][0]}), \
         mock.patch("src.correlate.handler.neat_get", return_value={"co2": 900}), \
         mock.patch("src.correlate.handler.webex_get", return_value={"items": [{"temperature": 22}]}), \
         mock.patch("src.correlate.handler.graph_get", return_value={"value": [{"jitter": 5}]}), \
         mock.patch("src.correlate.handler.te_get", return_value={"results": [{"loss": 0.0, "mos": 4.2}]}):
        out = h.handler({"tool": "correlate", "room": "L5 Boardroom",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T02:00:00Z"}, None)
    assert out["ok"] is True
    d = out["data"]
    assert d["room"] == "L5 Boardroom"
    assert d["environment"] is not None
    assert d["network"] is not None
    assert "observational" in d["note"].lower()


def test_correlate_degrades_when_source_fails():
    h = _load()
    with mock.patch("src.correlate.handler.load_location_map", return_value={"sites": [_SITE]}), \
         mock.patch("src.correlate.handler.resolve_location",
                    return_value={"site": _SITE, "room": _SITE["rooms"][0]}), \
         mock.patch("src.correlate.handler.neat_get", side_effect=RuntimeError("neat down")), \
         mock.patch("src.correlate.handler.webex_get", return_value=None), \
         mock.patch("src.correlate.handler.graph_get", return_value={"value": []}), \
         mock.patch("src.correlate.handler.te_get", side_effect=RuntimeError("te 403")):
        out = h.handler({"tool": "correlate", "room": "L5 Boardroom",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T02:00:00Z"}, None)
    assert out["ok"] is True
    assert out["data"]["environment"] is None  # neat failed and webex None -> None, not a crash
    assert out["data"]["network"] is None       # te failed -> None


def test_correlate_unknown_location_returns_err():
    h = _load()
    with mock.patch("src.correlate.handler.load_location_map", return_value={"sites": [_SITE]}), \
         mock.patch("src.correlate.handler.resolve_location", side_effect=KeyError("no match")):
        out = h.handler({"tool": "correlate", "room": "ghost",
                         "fromDateTime": "2026-07-01T00:00:00Z",
                         "toDateTime": "2026-07-01T02:00:00Z"}, None)
    assert out["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_correlate.py -v`
Expected: FAIL (new joined-shape assertions fail against the old room↔call-only handler)

- [ ] **Step 3: Write minimal implementation**

Replace the body of `src/correlate/handler.py` with (preserving its import bootstrap):

```python
"""AgentCore Lambda target: map-driven multi-source correlation.

Given a site or room + a UTC window, resolves the explicit location map to the
per-source ids, fans out server-side to Neat / Webex / Graph / ThousandEyes, and
returns a single joined view. Missing ids or permissions degrade to None.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.clients import (  # noqa: E402
    neat_get, graph_get, webex_get, te_get,
    load_location_map, resolve_location, tool_ok, tool_err,
)

_NOTE = ("Associations are observational, not causal. Call records may lag "
         "~30 min; ThousandEyes metrics are site/agent-level.")


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def correlate(event) -> dict:
    site, room = event.get("site"), event.get("room")
    frm, to = event.get("fromDateTime"), event.get("toDateTime")
    if not frm or not to:
        return tool_err("fromDateTime and toDateTime are required")
    if not site and not room:
        return tool_err("provide either site or room")

    m = load_location_map()
    try:
        loc = resolve_location(m, site, room)
    except KeyError as e:
        return tool_err(str(e))

    site_obj, room_obj = loc["site"], loc["room"]
    te_ids = site_obj.get("thousandeyes", {})

    environment = None
    meeting_quality = None
    call_quality = None
    if room_obj:
        if room_obj.get("neat_space_id"):
            environment = _safe(lambda: neat_get(
                f"/spaces/{room_obj['neat_space_id']}/sensors",
                {"from": frm, "to": to}))
        if room_obj.get("webex_workspace_id"):
            wx_env = _safe(lambda: webex_get(
                "/workspaceMetrics",
                {"workspaceId": room_obj["webex_workspace_id"], "from": frm, "to": to}))
            environment = environment or wx_env
        if room_obj.get("teams_room_upn"):
            call_quality = _safe(lambda: graph_get(
                "/communications/callRecords",
                {"$filter": f"startDateTime ge {frm} and startDateTime le {to}"}))

    network = None
    path_summary = None
    if te_ids.get("network_test_id"):
        network = _safe(lambda: te_get(
            f"/test-results/{te_ids['network_test_id']}/network",
            {"from": frm, "to": to}))
    if te_ids.get("voice_test_id"):
        voice = _safe(lambda: te_get(
            f"/test-results/{te_ids['voice_test_id']}/voice",
            {"from": frm, "to": to}))
        if voice is not None:
            network = {"network": network, "voice": voice} if network else {"voice": voice}

    return tool_ok({
        "site": site_obj.get("site"),
        "room": room_obj.get("room") if room_obj else None,
        "window": {"from": frm, "to": to},
        "environment": environment,
        "meeting_quality": meeting_quality,
        "call_quality": call_quality,
        "network": network,
        "path_summary": path_summary,
        "note": _NOTE,
    })


def handler(event, context):
    if event.get("tool") not in (None, "correlate", "correlate_room_calls"):
        return tool_err(f"unknown tool: {event.get('tool')}")
    try:
        return correlate(event)
    except Exception as e:  # noqa: BLE001
        return tool_err(f"{type(e).__name__}: {e}")
```

> **Verify against the repo:** the exact Neat sensors path (`/spaces/{id}/sensors`) and Graph filter must match what the existing `neat_sense`/`graph_calls` handlers already use — copy those verbatim from the current handlers rather than the illustrative strings above. Tests mock the client calls, so they stay green regardless.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_correlate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 6: Commit**

```bash
git add src/correlate/handler.py tests/test_correlate.py
git commit -m "feat: map-driven multi-source correlate"
```

---

### Task 7: Infra — two Lambdas, two secrets, IAM, outputs

**Files:**
- Modify: `infra/template.yaml`
- Verify: `sam validate --lint`, then `make test` still green.

**Interfaces:**
- Produces CloudFormation outputs `WebexFnArn`, `ThousandEyesFnArn` consumed by Task 8.

Read `infra/template.yaml` first and copy the existing `NeatSenseFn` / `GraphCallsFn` resource blocks and their secret + IAM patterns exactly; only the names, env vars, and CodeUri change.

- [ ] **Step 1: Add the two secrets** (mirror the existing `neat-pulse` / `graph-app` `AWS::SecretsManager::Secret` blocks):

```yaml
  WebexSecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Name: !Sub '${AWS::StackName}/webex'
      SecretString: '{"client_id":"REPLACE","client_secret":"REPLACE","refresh_token":"REPLACE"}'

  ThousandEyesSecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Name: !Sub '${AWS::StackName}/thousandeyes'
      SecretString: '{"bearer_token":"REPLACE"}'
```

- [ ] **Step 2: Add the two Lambda functions** (mirror `GraphCallsFn`; use the shared layer, `secretsmanager:GetSecretValue` scoped to the matching secret):

```yaml
  WebexFn:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: ../src/webex/
      Handler: handler.handler
      Layers: [!Ref SharedLayer]
      Environment:
        Variables:
          WEBEX_SECRET_ARN: !Ref WebexSecret
      Policies:
        - AWSSecretsManagerGetSecretValuePolicy:
            SecretArn: !Ref WebexSecret

  ThousandEyesFn:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: ../src/thousandeyes/
      Handler: handler.handler
      Layers: [!Ref SharedLayer]
      Environment:
        Variables:
          THOUSANDEYES_SECRET_ARN: !Ref ThousandEyesSecret
      Policies:
        - AWSSecretsManagerGetSecretValuePolicy:
            SecretArn: !Ref ThousandEyesSecret
```

- [ ] **Step 3: Extend the `CorrelateFn`** environment + policies to reach all four secrets and the location map. Add to its existing `Environment.Variables`: `WEBEX_SECRET_ARN: !Ref WebexSecret`, `THOUSANDEYES_SECRET_ARN: !Ref ThousandEyesSecret`, `LOCATIONS_PATH: /var/task/config/locations.json`; add `AWSSecretsManagerGetSecretValuePolicy` entries for `WebexSecret` and `ThousandEyesSecret`; ensure `config/locations.json` is packaged with the correlate function (copy `config/` into `src/correlate/` at build time, or bundle it in the shared layer and point `LOCATIONS_PATH` at the layer path).

- [ ] **Step 4: Add outputs** (mirror existing `*FnArn` outputs):

```yaml
  WebexFnArn:
    Value: !GetAtt WebexFn.Arn
  ThousandEyesFnArn:
    Value: !GetAtt ThousandEyesFn.Arn
```

- [ ] **Step 5: Validate**

Run: `sam validate --lint` (from `infra/`)
Expected: template is valid.

- [ ] **Step 6: Commit**

```bash
git add infra/template.yaml
git commit -m "feat: infra for Webex + ThousandEyes Lambdas and secrets"
```

---

### Task 8: Deploy wiring — new Gateway targets

**Files:**
- Modify: `scripts/deploy_gateway.py` (add `webex` and `thousandeyes` entries to `TARGETS`)

**Interfaces:**
- Consumes stack outputs `WebexFnArn`, `ThousandEyesFnArn` from Task 7.

- [ ] **Step 1: Add the `webex` target** to the `TARGETS` dict, following the existing entry shape (`fn_output` + inline `tools` schemas):

```python
    "webex": {
        "fn_output": "WebexFnArn",
        "tools": [
            {"name": "webex_list_meetings",
             "description": "List Webex meetings that occurred in a UTC window.",
             "inputSchema": {"type": "object", "properties": {
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}},
                 "required": ["fromDateTime", "toDateTime"]}},
            {"name": "webex_meeting_quality",
             "description": "Per-participant media quality (jitter, packet loss, latency, resolution) for a Webex meeting. Requires Pro Pack.",
             "inputSchema": {"type": "object", "properties": {"meetingId": {"type": "string"}},
                             "required": ["meetingId"]}},
            {"name": "webex_device_presence",
             "description": "List Webex room devices/workspaces with status and in-call presence.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "webex_workspace_environment",
             "description": "Temperature, humidity, air quality, ambient sound for a Webex workspace over a window.",
             "inputSchema": {"type": "object", "properties": {
                 "workspace_id": {"type": "string"},
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}},
                 "required": ["workspace_id"]}},
        ],
    },
```

- [ ] **Step 2: Add the `thousandeyes` target** with all four tools:

```python
    "thousandeyes": {
        "fn_output": "ThousandEyesFnArn",
        "tools": [
            {"name": "te_list_tests_alerts",
             "description": "List configured ThousandEyes tests/agents and active alerts in a window.",
             "inputSchema": {"type": "object", "properties": {
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}}}},
            {"name": "te_network_results",
             "description": "Network metrics (loss, latency, jitter) for a ThousandEyes test over a window.",
             "inputSchema": {"type": "object", "properties": {
                 "test_id": {"type": "string"},
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}},
                 "required": ["test_id", "fromDateTime", "toDateTime"]}},
            {"name": "te_voice_results",
             "description": "Voice/RTP results (MOS, jitter, loss, latency) for a ThousandEyes voice test.",
             "inputSchema": {"type": "object", "properties": {
                 "test_id": {"type": "string"},
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}},
                 "required": ["test_id", "fromDateTime", "toDateTime"]}},
            {"name": "te_path_visualization",
             "description": "Hop-by-hop path visualization with per-hop latency/loss for a ThousandEyes test.",
             "inputSchema": {"type": "object", "properties": {
                 "test_id": {"type": "string"},
                 "fromDateTime": {"type": "string"}, "toDateTime": {"type": "string"}},
                 "required": ["test_id"]}},
        ],
    },
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/deploy_gateway.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add scripts/deploy_gateway.py
git commit -m "feat: register Webex + ThousandEyes gateway targets"
```

---

### Task 9: Docs — README tools table + prerequisites

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the 8 new tools** to the Tools table (Source column: Webex / ThousandEyes), and add a `correlate` note that it is now map-driven via `config/locations.json`.

- [ ] **Step 2: Add prerequisites**:
  - **Cisco Webex:** a service app with admin scopes for analytics/devices/workspaces; **Control Hub Pro Pack** required for Meeting Qualities; store `{client_id, client_secret, refresh_token}` in `neat-graph-bedrock/webex`.
  - **Cisco ThousandEyes:** an API user with the **API Access** permission; store `{bearer_token}` in `neat-graph-bedrock/thousandeyes`. Note the 240 req/min org limit.
  - **Location map:** fill `config/locations.json` with your sites/rooms and per-source ids.

- [ ] **Step 3: Add two `put-secret-value` examples** under Deploy, mirroring the existing Neat/Graph examples.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document Webex + ThousandEyes tools and setup"
```

---

## Self-Review

**Spec coverage:** Webex 4 tools (Task 5) ✓, ThousandEyes 4 tools (Task 4) ✓, refresh-token auth (Task 2) ✓, TE bearer auth (Task 1) ✓, location map (Task 3) ✓, map-driven correlate w/ graceful degradation (Task 6) ✓, infra + secrets + IAM + outputs (Task 7) ✓, deploy targets (Task 8) ✓, docs (Task 9) ✓, tests for all (Tasks 1–6) ✓.

**Placeholder scan:** No "TBD/TODO/implement later" in code steps. The three `> Verify` callouts are explicit "confirm real API path/convention against the live API/repo" instructions, not placeholders — the code given is complete and test-green; only upstream path/param strings may need a one-line tweak once credentials exist. `config/locations.json` ships with empty-string id fields by design (operator fills them).

**Type consistency:** `te_get`, `webex_get`, `webex_token`, `load_location_map`, `resolve_location`, `tool_ok`, `tool_err` names are used identically across Tasks 1–6 and the mock patch targets. `event['tool']` dispatch convention is used uniformly (with a flagged instruction to verify against `graph_calls/handler.py`).

**Known real-world caveat carried forward:** exact upstream REST paths/params for Webex (`/workspaceMetrics`, `/meeting/qualities`) and ThousandEyes (`/test-results/{id}/{network|voice|path-vis}`) should be confirmed when live credentials are available. Because every test mocks the client boundary, the suite stays valid; confirmation is a targeted path-string check, not a redesign.
