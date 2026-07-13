# neat-graph-bedrock Web Console (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an authenticated, browser-based chat console (hosted on AWS) that lets a logged-in user query the existing neat-graph-bedrock AgentCore Runtime in plain English, with per-user saved conversation history.

**Architecture:** A Next.js static-export SPA in S3 behind CloudFront calls an API Gateway HTTP API (Cognito JWT authorizer) that fronts a single Python Lambda "web tier". The Lambda invokes the existing runtime via SigV4 (reusing the CLI's invoke logic through a shared `runtime_client.py`) and persists each turn to a per-user DynamoDB table. Everything is provisioned by a new SAM stack alongside the existing one.

**Tech Stack:** Python 3.12 (Lambda, pytest, moto), AWS SAM (S3, CloudFront, HTTP API, Lambda, DynamoDB, Cognito), Next.js (App Router, `output: 'export'`) + React + Tailwind CSS + Framer Motion + lucide-react + Vitest.

## Global Constraints

- **Read-only upstream (non-negotiable):** the agent stays read-only against Neat / ThousandEyes / Graph. The web tier's ONLY writes are to its own per-user DynamoDB chat-history table. No upstream creds ever reach the browser or the Lambda env.
- **Per-user isolation:** every API route is behind the Cognito JWT authorizer; all DynamoDB access is partitioned by the token's `sub` claim. A user can only read/write their own threads.
- **`conversationId` IS the runtime `runtimeSessionId`:** one 36-char UUID per thread; it must be **≥33 characters** (a `str(uuid.uuid4())` is exactly 36).
- **Buffered, not streaming** in Phase 1 (matches the runtime entrypoint and the API Gateway 29s integration timeout).
- **Region us-east-1**, same as the live runtime `arn:aws:bedrock-agentcore:us-east-1:<account_id>:runtime/<runtime-id>`.
- **Reuse the existing Cognito user pool** (output `UserPoolId` of the existing stack); the console adds a SEPARATE authorization-code app client — it never touches the client-credentials client used by the Gateway.
- **Message timestamp format** (`ts`, and the `#MSG#<ts>` sort-key segment): ISO-8601 UTC, millisecond precision, trailing `Z`, e.g. `2026-07-08T05:39:12.004Z` — fixed-width and lexicographically sortable.
- **Frontend stack is fixed:** Next.js static export + React + Tailwind + Framer Motion + lucide-react. Motion used sparingly and must respect `prefers-reduced-motion`.
- **Python style:** match the existing repo (`from __future__ import annotations`, stdlib argparse/json, boto3 clients created inside functions with an injectable client/table param for testing).

## File Structure

**Backend (`webapp/api/`)** — packaged flat into the Lambda zip; modules import each other by bare name (`import runtime_client`).
- `runtime_client.py` — canonical runtime invoke logic: `build_payload`, `parse_response`, `invoke_runtime`. Imported by the Lambda AND by `scripts/invoke_agent.py`.
- `store.py` — all DynamoDB access (thread + message CRUD), table name from `TABLE_NAME` env.
- `handler.py` — Lambda entry `handler(event, context)`; routes the four endpoints, reads `sub` from JWT claims, maps errors to HTTP status.
- `requirements-dev.txt` — `pytest`, `moto[dynamodb]` (boto3 is provided by the Lambda runtime; pinned here for local tests).
- `tests/conftest.py` — puts `webapp/api/` on `sys.path` so tests import the flat modules.
- `tests/test_runtime_client.py`, `tests/test_store.py`, `tests/test_handler.py`.

**Infra (`webapp/infra/`)**
- `template.yaml` — the whole new SAM stack (DynamoDB, Lambda + role, HTTP API + JWT authorizer + routes, Cognito auth-code client + hosted-UI domain, S3 + CloudFront + OAC), with Outputs.

**Frontend (`webapp/frontend/`)** — Next.js App Router, static export.
- `package.json`, `next.config.mjs`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.mjs`, `vitest.config.ts`, `.env.local.example`.
- `app/layout.tsx`, `app/globals.css`, `app/page.tsx` (console), `app/callback/page.tsx` (OAuth code exchange).
- `lib/config.ts` (public env), `lib/pkce.ts` (PKCE helpers), `lib/auth.ts` (login/callback/logout/token), `lib/api.ts` (typed API client), `lib/types.ts`.
- `components/MessageBubble.tsx`, `components/Composer.tsx`, `components/ChatThread.tsx`, `components/Sidebar.tsx`, `components/ErrorToast.tsx`.
- `lib/pkce.test.ts`, `lib/api.test.ts`, `lib/auth.test.ts`.

**Glue / docs**
- Root `Makefile` — add `webapp-build`, `webapp-deploy`, `webapp-sync` targets (repo already has a `Makefile`).
- `webapp/README.md` — deploy steps + manual E2E checklist.

---

### Task 1: Shared runtime client (`runtime_client.py`) + CLI refactor

**Files:**
- Create: `webapp/api/runtime_client.py`
- Create: `webapp/api/tests/conftest.py`
- Create: `webapp/api/tests/test_runtime_client.py`
- Modify: `scripts/invoke_agent.py` (import shared logic instead of its own copies)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `build_payload(prompt: str, session: str) -> bytes` → JSON `{"prompt","sessionId"}`.
  - `parse_response(raw: bytes) -> str` → the `answer`, or `"[agent error] ..."` when the payload carries `error` and no `answer`.
  - `invoke_runtime(prompt: str, session: str, *, runtime_arn: str, region: str, client=None) -> str` → calls `bedrock-agentcore invoke_agent_runtime` and returns the parsed answer. `client` is injectable for tests.

- [ ] **Step 1: Write the failing test**

Create `webapp/api/tests/conftest.py`:

```python
import sys
from pathlib import Path

# Make the flat Lambda modules (runtime_client, store, handler) importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

Create `webapp/api/tests/test_runtime_client.py`:

```python
import json

import runtime_client as rc


def test_build_payload_shape():
    assert json.loads(rc.build_payload("hi", "s1")) == {"prompt": "hi", "sessionId": "s1"}


def test_parse_response_answer():
    raw = json.dumps({"answer": "42", "sessionId": "s1"}).encode()
    assert rc.parse_response(raw) == "42"


def test_parse_response_surfaces_error():
    raw = json.dumps({"error": "boom"}).encode()
    assert "boom" in rc.parse_response(raw)


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    def __init__(self, answer: str):
        self._answer = answer
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": _FakeBody(json.dumps({"answer": self._answer}).encode())}


def test_invoke_runtime_passes_session_and_returns_answer():
    client = _FakeClient("six rooms")
    out = rc.invoke_runtime(
        "list rooms", "sess-0123456789012345678901234567890",
        runtime_arn="arn:runtime", region="us-east-1", client=client)
    assert out == "six rooms"
    assert client.calls[0]["agentRuntimeArn"] == "arn:runtime"
    assert client.calls[0]["runtimeSessionId"] == "sess-0123456789012345678901234567890"
    assert json.loads(client.calls[0]["payload"]) == {
        "prompt": "list rooms", "sessionId": "sess-0123456789012345678901234567890"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/api && python -m pytest tests/test_runtime_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runtime_client'`.

- [ ] **Step 3: Write minimal implementation**

Create `webapp/api/runtime_client.py`:

```python
"""Canonical AgentCore Runtime invoke logic, shared by the Lambda web tier
and scripts/invoke_agent.py so the runtime contract lives in one place."""
from __future__ import annotations

import json

import boto3


def build_payload(prompt: str, session: str) -> bytes:
    return json.dumps({"prompt": prompt, "sessionId": session}).encode()


def parse_response(raw: bytes) -> str:
    data = json.loads(raw.decode() or "{}")
    if "error" in data and "answer" not in data:
        return f"[agent error] {data['error']}"
    return data.get("answer", raw.decode())


def invoke_runtime(prompt: str, session: str, *, runtime_arn: str,
                   region: str, client=None) -> str:
    client = client or boto3.client("bedrock-agentcore", region_name=region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session,
        payload=build_payload(prompt, session),
    )
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    return parse_response(body if isinstance(body, bytes) else str(body).encode())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/api && python -m pytest tests/test_runtime_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Refactor the CLI to import the shared module**

Replace the `build_payload`/`parse_response` copies in `scripts/invoke_agent.py` with imports from the shared module, loaded by path (mirrors the existing `tests/test_invoke_agent.py` importlib pattern so no package plumbing is needed). Edit `scripts/invoke_agent.py`: remove its own `build_payload` and `parse_response` function definitions, and after `import boto3` add:

```python
import importlib.util
from pathlib import Path

_rc_spec = importlib.util.spec_from_file_location(
    "runtime_client",
    Path(__file__).resolve().parent.parent / "webapp" / "api" / "runtime_client.py")
_rc = importlib.util.module_from_spec(_rc_spec)
_rc_spec.loader.exec_module(_rc)

build_payload = _rc.build_payload      # re-export: keeps tests/test_invoke_agent.py green
parse_response = _rc.parse_response
```

Leave the rest of `scripts/invoke_agent.py` (`main`, arg parsing) unchanged — it still calls `build_payload`/`parse_response` at module scope.

- [ ] **Step 6: Run BOTH test files to verify nothing regressed**

Run: `python -m pytest tests/test_invoke_agent.py webapp/api/tests/test_runtime_client.py -v`
Expected: PASS (existing 3 CLI tests + 4 new tests).

- [ ] **Step 7: Commit**

```bash
git add webapp/api/runtime_client.py webapp/api/tests/conftest.py webapp/api/tests/test_runtime_client.py scripts/invoke_agent.py
git commit -m "feat(webapp): shared runtime_client; CLI reuses it"
```

---

### Task 2: DynamoDB store (`store.py`)

**Files:**
- Create: `webapp/api/store.py`
- Create: `webapp/api/requirements-dev.txt`
- Create: `webapp/api/tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all take an optional `table=` boto3 Table for tests; default resolves `boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])`):
  - `upsert_thread(sub: str, conv_id: str, title: str, now: str, table=None) -> None` — creates/updates the `CONV#<id>` metadata row; sets `title` only if not already set; always sets `updatedAt=now`.
  - `put_message(sub: str, conv_id: str, role: str, text: str, ts: str, table=None) -> None` — writes a `CONV#<id>#MSG#<ts>` row.
  - `list_threads(sub: str, table=None) -> list[dict]` — `[{"id","title","updatedAt"}]`, newest `updatedAt` first.
  - `get_messages(sub: str, conv_id: str, table=None) -> list[dict]` — `[{"role","text","ts"}]`, chronological.
  - `delete_thread(sub: str, conv_id: str, table=None) -> None` — deletes the metadata row and all message rows.

- [ ] **Step 1: Write the failing test**

Create `webapp/api/requirements-dev.txt`:

```
pytest>=8
moto[dynamodb]>=5
boto3>=1.35
```

Create `webapp/api/tests/test_store.py`:

```python
import boto3
import pytest
from moto import mock_aws

import store

TABLE = "web-console-test"


@pytest.fixture()
def table(monkeypatch):
    with mock_aws():
        res = boto3.resource("dynamodb", region_name="us-east-1")
        res.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                       {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("TABLE_NAME", TABLE)
        yield res.Table(TABLE)


def test_thread_roundtrip_and_message_order(table):
    store.upsert_thread("u1", "c1", "List rooms", "2026-07-08T00:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "List rooms", "2026-07-08T00:00:01.000Z", table=table)
    store.put_message("u1", "c1", "assistant", "6 rooms", "2026-07-08T00:00:02.000Z", table=table)

    msgs = store.get_messages("u1", "c1", table=table)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "List rooms"


def test_list_threads_newest_first_and_excludes_messages(table):
    store.upsert_thread("u1", "c1", "First", "2026-07-08T00:00:00.000Z", table=table)
    store.upsert_thread("u1", "c2", "Second", "2026-07-08T01:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "hi", "2026-07-08T00:00:05.000Z", table=table)

    threads = store.list_threads("u1", table=table)
    assert [t["id"] for t in threads] == ["c2", "c1"]           # newest updatedAt first
    assert all("MSG" not in str(t) for t in threads)             # no message rows leaked


def test_upsert_thread_preserves_first_title(table):
    store.upsert_thread("u1", "c1", "Original", "2026-07-08T00:00:00.000Z", table=table)
    store.upsert_thread("u1", "c1", "", "2026-07-08T02:00:00.000Z", table=table)
    threads = store.list_threads("u1", table=table)
    assert threads[0]["title"] == "Original"
    assert threads[0]["updatedAt"] == "2026-07-08T02:00:00.000Z"


def test_delete_thread_removes_metadata_and_messages(table):
    store.upsert_thread("u1", "c1", "T", "2026-07-08T00:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "hi", "2026-07-08T00:00:01.000Z", table=table)
    store.delete_thread("u1", "c1", table=table)
    assert store.list_threads("u1", table=table) == []
    assert store.get_messages("u1", "c1", table=table) == []


def test_users_are_isolated(table):
    store.upsert_thread("u1", "c1", "mine", "2026-07-08T00:00:00.000Z", table=table)
    assert store.list_threads("u2", table=table) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/api && python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Write minimal implementation**

Create `webapp/api/store.py`:

```python
"""Per-user conversation storage in a single DynamoDB table.

Keys:  PK = USER#<sub>
       SK = CONV#<id>                 (thread metadata: title, createdAt, updatedAt)
       SK = CONV#<id>#MSG#<ts>        (message: role, text, ts)
"""
from __future__ import annotations

import os

import boto3
from boto3.dynamodb.conditions import Key


def _table(table=None):
    if table is not None:
        return table
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _pk(sub: str) -> str:
    return f"USER#{sub}"


def upsert_thread(sub: str, conv_id: str, title: str, now: str, table=None) -> None:
    t = _table(table)
    # Set title only on first write; always bump updatedAt; set createdAt once.
    t.update_item(
        Key={"PK": _pk(sub), "SK": f"CONV#{conv_id}"},
        UpdateExpression=(
            "SET updatedAt = :now, "
            "createdAt = if_not_exists(createdAt, :now), "
            "title = if_not_exists(title, :title)"),
        ExpressionAttributeValues={":now": now, ":title": title or "New conversation"},
    )


def put_message(sub: str, conv_id: str, role: str, text: str, ts: str, table=None) -> None:
    t = _table(table)
    t.put_item(Item={
        "PK": _pk(sub), "SK": f"CONV#{conv_id}#MSG#{ts}",
        "role": role, "text": text, "ts": ts,
    })


def list_threads(sub: str, table=None) -> list[dict]:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub)) & Key("SK").begins_with("CONV#"))
    threads = []
    for it in resp.get("Items", []):
        if "#MSG#" in it["SK"]:
            continue
        threads.append({
            "id": it["SK"].split("CONV#", 1)[1],
            "title": it.get("title", "New conversation"),
            "updatedAt": it.get("updatedAt", ""),
        })
    threads.sort(key=lambda x: x["updatedAt"], reverse=True)
    return threads


def get_messages(sub: str, conv_id: str, table=None) -> list[dict]:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub))
        & Key("SK").begins_with(f"CONV#{conv_id}#MSG#"))
    return [{"role": it["role"], "text": it["text"], "ts": it["ts"]}
            for it in resp.get("Items", [])]


def delete_thread(sub: str, conv_id: str, table=None) -> None:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub))
        & Key("SK").begins_with(f"CONV#{conv_id}"))
    with t.batch_writer() as batch:
        for it in resp.get("Items", []):
            batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/api && pip install -r requirements-dev.txt && python -m pytest tests/test_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/api/store.py webapp/api/requirements-dev.txt webapp/api/tests/test_store.py
git commit -m "feat(webapp): DynamoDB per-user conversation store"
```

---

### Task 3: Lambda handler — `POST /chat`

**Files:**
- Create: `webapp/api/handler.py`
- Create: `webapp/api/tests/test_handler.py`

**Interfaces:**
- Consumes: `runtime_client.invoke_runtime`, `store.upsert_thread/put_message`.
- Produces:
  - `handler(event: dict, context) -> dict` — HTTP API v2 response `{"statusCode","headers","body"}`.
  - Helpers used by later tasks: `_sub(event) -> str | None`, `_resp(status: int, body: dict | None) -> dict`, `_ts() -> str` (ISO-8601 ms + `Z`).
  - Env read: `RUNTIME_ARN`, `AWS_REGION` (provided by Lambda), `TABLE_NAME`.
  - `POST /chat` request `{"prompt": str, "conversationId": str | null}` → `200 {"answer","conversationId"}`; mints a `str(uuid.uuid4())` when `conversationId` is null; persists user + assistant turn; upserts thread (title = first prompt, truncated to 60 chars). Runtime failure → `502 {"error"}`. Missing `sub` → `401`.

- [ ] **Step 1: Write the failing test**

Create `webapp/api/tests/test_handler.py`:

```python
import json

import pytest

import handler
import runtime_client
import store


def _event(method, path, *, sub="u1", body=None, route=None):
    return {
        "routeKey": route or f"{method} {path}",
        "rawPath": path,
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": {"sub": sub}}} if sub else {},
        },
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {},
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RUNTIME_ARN", "arn:runtime")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("TABLE_NAME", "t")


def test_chat_returns_answer_and_new_conversation_id(monkeypatch):
    monkeypatch.setattr(runtime_client, "invoke_runtime", lambda *a, **k: "6 rooms")
    saved = []
    monkeypatch.setattr(store, "put_message", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)

    resp = handler.handler(_event("POST", "/chat", body={"prompt": "list rooms", "conversationId": None}), None)
    assert resp["statusCode"] == 200
    payload = json.loads(resp["body"])
    assert payload["answer"] == "6 rooms"
    assert len(payload["conversationId"]) >= 33          # runtime session-id rule
    assert len(saved) == 2                                # user + assistant persisted


def test_chat_reuses_supplied_conversation_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(runtime_client, "invoke_runtime",
                        lambda prompt, session, **k: seen.setdefault("session", session) or "ok")
    monkeypatch.setattr(store, "put_message", lambda *a, **k: None)
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)

    cid = "abcdefabcdef-abcdef-abcdef-abcdef1234"
    resp = handler.handler(_event("POST", "/chat", body={"prompt": "hi", "conversationId": cid}), None)
    assert json.loads(resp["body"])["conversationId"] == cid
    assert seen["session"] == cid                         # conversationId == runtime session


def test_chat_runtime_error_returns_502(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("runtime down")
    monkeypatch.setattr(runtime_client, "invoke_runtime", boom)
    monkeypatch.setattr(store, "put_message", lambda *a, **k: None)
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)

    resp = handler.handler(_event("POST", "/chat", body={"prompt": "hi", "conversationId": None}), None)
    assert resp["statusCode"] == 502
    assert "error" in json.loads(resp["body"])


def test_missing_sub_returns_401():
    resp = handler.handler(_event("POST", "/chat", sub=None, body={"prompt": "hi"}), None)
    assert resp["statusCode"] == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/api && python -m pytest tests/test_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'handler'`.

- [ ] **Step 3: Write minimal implementation**

Create `webapp/api/handler.py`:

```python
"""API Gateway HTTP API (v2) Lambda for the web console.

Routes (all behind a Cognito JWT authorizer):
    POST   /chat
    GET    /conversations
    GET    /conversations/{id}
    DELETE /conversations/{id}
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import runtime_client
import store

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
}


def _resp(status: int, body: dict | None = None) -> dict:
    return {"statusCode": status, "headers": _CORS,
            "body": "" if body is None else json.dumps(body)}


def _sub(event: dict) -> str | None:
    return (event.get("requestContext", {}).get("authorizer", {})
            .get("jwt", {}).get("claims", {}).get("sub"))


def _ts() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _chat(sub: str, event: dict) -> dict:
    body = json.loads(event.get("body") or "{}")
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return _resp(400, {"error": "prompt is required"})
    conv_id = body.get("conversationId") or str(uuid.uuid4())

    try:
        answer = runtime_client.invoke_runtime(
            prompt, conv_id,
            runtime_arn=os.environ["RUNTIME_ARN"],
            region=os.environ.get("AWS_REGION", "us-east-1"))
    except Exception as exc:                       # noqa: BLE001 - surfaced to the UI
        return _resp(502, {"error": f"runtime invocation failed: {exc}"})

    try:
        store.upsert_thread(sub, conv_id, prompt[:60], _ts())
        store.put_message(sub, conv_id, "user", prompt, _ts())
        store.put_message(sub, conv_id, "assistant", answer, _ts())
    except Exception:                              # noqa: BLE001 - history is best-effort
        pass

    return _resp(200, {"answer": answer, "conversationId": conv_id})


def handler(event: dict, context) -> dict:
    sub = _sub(event)
    if not sub:
        return _resp(401, {"error": "unauthorized"})
    route = event.get("routeKey", "")
    if route == "POST /chat":
        return _chat(sub, event)
    return _resp(404, {"error": f"no route for {route}"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/api && python -m pytest tests/test_handler.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/api/handler.py webapp/api/tests/test_handler.py
git commit -m "feat(webapp): Lambda POST /chat (invoke + persist)"
```

---

### Task 4: Lambda handler — conversation routes

**Files:**
- Modify: `webapp/api/handler.py` (add list/get/delete routing)
- Modify: `webapp/api/tests/test_handler.py` (add route tests)

**Interfaces:**
- Consumes: `store.list_threads/get_messages/delete_thread`, the Task-3 helpers.
- Produces:
  - `GET /conversations` → `200 {"conversations":[{"id","title","updatedAt"}]}`.
  - `GET /conversations/{id}` → `200 {"id","messages":[{"role","text","ts"}]}`.
  - `DELETE /conversations/{id}` → `204` (empty body).

- [ ] **Step 1: Write the failing test**

Append to `webapp/api/tests/test_handler.py`:

```python
def test_list_conversations(monkeypatch):
    monkeypatch.setattr(store, "list_threads",
                        lambda sub, **k: [{"id": "c1", "title": "T", "updatedAt": "z"}])
    resp = handler.handler(_event("GET", "/conversations"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["conversations"][0]["id"] == "c1"


def test_get_conversation_messages(monkeypatch):
    monkeypatch.setattr(store, "get_messages",
                        lambda sub, cid, **k: [{"role": "user", "text": "hi", "ts": "z"}])
    ev = _event("GET", "/conversations/c1", route="GET /conversations/{id}")
    ev["pathParameters"] = {"id": "c1"}
    resp = handler.handler(ev, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["messages"][0]["text"] == "hi"


def test_delete_conversation(monkeypatch):
    called = {}
    monkeypatch.setattr(store, "delete_thread",
                        lambda sub, cid, **k: called.update(cid=cid))
    ev = _event("DELETE", "/conversations/c1", route="DELETE /conversations/{id}")
    ev["pathParameters"] = {"id": "c1"}
    resp = handler.handler(ev, None)
    assert resp["statusCode"] == 204
    assert called["cid"] == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/api && python -m pytest tests/test_handler.py -v`
Expected: FAIL — the three new routes return 404.

- [ ] **Step 3: Write minimal implementation**

In `webapp/api/handler.py`, add these functions above `handler`:

```python
def _list(sub: str) -> dict:
    return _resp(200, {"conversations": store.list_threads(sub)})


def _get(sub: str, event: dict) -> dict:
    cid = (event.get("pathParameters") or {}).get("id", "")
    return _resp(200, {"id": cid, "messages": store.get_messages(sub, cid)})


def _delete(sub: str, event: dict) -> dict:
    cid = (event.get("pathParameters") or {}).get("id", "")
    store.delete_thread(sub, cid)
    return _resp(204, None)
```

Replace the routing block at the end of `handler` with:

```python
    route = event.get("routeKey", "")
    if route == "POST /chat":
        return _chat(sub, event)
    if route == "GET /conversations":
        return _list(sub)
    if route == "GET /conversations/{id}":
        return _get(sub, event)
    if route == "DELETE /conversations/{id}":
        return _delete(sub, event)
    return _resp(404, {"error": f"no route for {route}"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/api && python -m pytest tests/ -v`
Expected: PASS (all handler + store + runtime_client tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/api/handler.py webapp/api/tests/test_handler.py
git commit -m "feat(webapp): Lambda conversation list/get/delete routes"
```

---

### Task 5: SAM stack — DynamoDB, Lambda, HTTP API + JWT authorizer

**Files:**
- Create: `webapp/infra/template.yaml`

**Interfaces:**
- Consumes: the Lambda code at `../api/`; env `TABLE_NAME`, `RUNTIME_ARN`, `ALLOWED_ORIGIN`.
- Produces (Outputs, extended in Tasks 6-7): `ApiBaseUrl`, `TableName`, `FunctionArn`. Resource `WebApiFn` (references `WebApi` + `WebAppClient`).
- Parameters: `ProjectName` (default `neat-graph-bedrock`), `ExistingUserPoolId`, `RuntimeArn`, `AllowedOrigin` (default `*`, tightened to the CloudFront URL in Task 7).

> This task writes the whole template except the Cognito resources (Task 6) and hosting resources (Task 7). Because the JWT authorizer references `!Ref WebAppClient` (Task 6) and the app client references `WebDistribution` (Task 7), the template is only validated at the end of Task 7. Write this task's resources now; do not run `sam validate` until Task 7.

- [ ] **Step 1: Write the template**

Create `webapp/infra/template.yaml`:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: neat-graph-bedrock Web Console (Phase 1) — API, storage, auth, hosting.

Parameters:
  ProjectName:
    Type: String
    Default: neat-graph-bedrock
  ExistingUserPoolId:
    Type: String
    Description: UserPoolId output of the existing neat-graph-bedrock stack.
  RuntimeArn:
    Type: String
    Description: AgentCore Runtime ARN to invoke.
  AllowedOrigin:
    Type: String
    Default: "*"

Globals:
  Function:
    Runtime: python3.12
    Timeout: 30
    MemorySize: 256

Resources:
  HistoryTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub "${ProjectName}-web-history"
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - {AttributeName: PK, AttributeType: S}
        - {AttributeName: SK, AttributeType: S}
      KeySchema:
        - {AttributeName: PK, KeyType: HASH}
        - {AttributeName: SK, KeyType: RANGE}

  WebApiFn:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub "${ProjectName}-web-api"
      CodeUri: ../api/
      Handler: handler.handler
      Environment:
        Variables:
          TABLE_NAME: !Ref HistoryTable
          RUNTIME_ARN: !Ref RuntimeArn
          ALLOWED_ORIGIN: !Ref AllowedOrigin
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref HistoryTable
        - Statement:
            - Effect: Allow
              Action: bedrock-agentcore:InvokeAgentRuntime
              Resource: !Ref RuntimeArn
      Events:
        Chat:
          Type: HttpApi
          Properties: {ApiId: !Ref WebApi, Method: POST, Path: /chat}
        ListConversations:
          Type: HttpApi
          Properties: {ApiId: !Ref WebApi, Method: GET, Path: /conversations}
        GetConversation:
          Type: HttpApi
          Properties: {ApiId: !Ref WebApi, Method: GET, Path: /conversations/{id}}
        DeleteConversation:
          Type: HttpApi
          Properties: {ApiId: !Ref WebApi, Method: DELETE, Path: /conversations/{id}}

  WebApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      CorsConfiguration:
        AllowOrigins: [!Ref AllowedOrigin]
        AllowHeaders: [authorization, content-type]
        AllowMethods: [GET, POST, DELETE, OPTIONS]
      Auth:
        DefaultAuthorizer: CognitoJwt
        Authorizers:
          CognitoJwt:
            IdentitySource: "$request.header.Authorization"
            JwtConfiguration:
              issuer: !Sub "https://cognito-idp.${AWS::Region}.amazonaws.com/${ExistingUserPoolId}"
              audience:
                - !Ref WebAppClient

Outputs:
  ApiBaseUrl:
    Value: !Sub "https://${WebApi}.execute-api.${AWS::Region}.amazonaws.com"
  TableName:
    Value: !Ref HistoryTable
  FunctionArn:
    Value: !GetAtt WebApiFn.Arn
```

- [ ] **Step 2: Commit**

```bash
git add webapp/infra/template.yaml
git commit -m "feat(webapp): SAM — DynamoDB, Lambda, HTTP API + JWT authorizer"
```

---

### Task 6: SAM stack — Cognito auth-code app client + hosted UI domain

**Files:**
- Modify: `webapp/infra/template.yaml` (add Cognito resources + outputs; the client references `WebDistribution`, added in Task 7)

**Interfaces:**
- Consumes: `ExistingUserPoolId` param; `WebDistribution` (Task 7) for callback/logout URLs.
- Produces: `WebAppClient` (the audience for the Task-5 authorizer); Outputs `AppClientId`, `CognitoDomain`, `HostedLoginUrl`.

- [ ] **Step 1: Add the Cognito resources**

Add these two resources under `Resources:` in `webapp/infra/template.yaml` (paste exactly once — duplicate YAML keys are an error):

```yaml
  WebAppClient:
    Type: AWS::Cognito::UserPoolClient
    Properties:
      ClientName: !Sub "${ProjectName}-web-client"
      UserPoolId: !Ref ExistingUserPoolId
      GenerateSecret: false                       # public SPA client (PKCE)
      AllowedOAuthFlows: [code]
      AllowedOAuthFlowsUserPoolClient: true
      AllowedOAuthScopes: [openid, email, profile]
      SupportedIdentityProviders: [COGNITO]
      CallbackURLs:
        - !Sub "https://${WebDistribution.DomainName}/callback"
        - http://localhost:3000/callback
      LogoutURLs:
        - !Sub "https://${WebDistribution.DomainName}/"
        - http://localhost:3000/

  WebUserPoolDomain:
    Type: AWS::Cognito::UserPoolDomain
    Properties:
      Domain: !Sub "${ProjectName}-web-${AWS::AccountId}"
      UserPoolId: !Ref ExistingUserPoolId
```

- [ ] **Step 2: Add the Cognito outputs**

Add under `Outputs:`:

```yaml
  AppClientId:
    Value: !Ref WebAppClient
  CognitoDomain:
    Value: !Sub "https://${ProjectName}-web-${AWS::AccountId}.auth.${AWS::Region}.amazoncognito.com"
  HostedLoginUrl:
    Value: !Sub "https://${ProjectName}-web-${AWS::AccountId}.auth.${AWS::Region}.amazoncognito.com/login?client_id=${WebAppClient}&response_type=code&scope=openid+email+profile&redirect_uri=https://${WebDistribution.DomainName}/callback"
```

- [ ] **Step 3: Commit (validation happens in Task 7)**

```bash
git add webapp/infra/template.yaml
git commit -m "feat(webapp): SAM — Cognito auth-code client + hosted UI domain"
```

---

### Task 7: SAM stack — S3 + CloudFront (OAC) + SPA fallback

**Files:**
- Modify: `webapp/infra/template.yaml` (add S3, CloudFront, OAC, bucket policy, outputs)

**Interfaces:**
- Consumes: nothing new.
- Produces: `WebDistribution` (referenced by Task 6); Outputs `SiteBucket`, `SiteUrl`, `DistributionId`.

- [ ] **Step 1: Add hosting resources**

Add under `Resources:`:

```yaml
  SiteBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Sub "${ProjectName}-web-site-${AWS::AccountId}"
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true

  SiteOAC:
    Type: AWS::CloudFront::OriginAccessControl
    Properties:
      OriginAccessControlConfig:
        Name: !Sub "${ProjectName}-web-oac"
        OriginAccessControlOriginType: s3
        SigningBehavior: always
        SigningProtocol: sigv4

  WebDistribution:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        Enabled: true
        DefaultRootObject: index.html
        Origins:
          - Id: s3origin
            DomainName: !GetAtt SiteBucket.RegionalDomainName
            OriginAccessControlId: !Ref SiteOAC
            S3OriginConfig: {OriginAccessIdentity: ""}
        DefaultCacheBehavior:
          TargetOriginId: s3origin
          ViewerProtocolPolicy: redirect-to-https
          CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6   # AWS Managed-CachingOptimized
        CustomErrorResponses:
          - {ErrorCode: 403, ResponseCode: 200, ResponsePagePath: /index.html}
          - {ErrorCode: 404, ResponseCode: 200, ResponsePagePath: /index.html}

  SiteBucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref SiteBucket
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: {Service: cloudfront.amazonaws.com}
            Action: s3:GetObject
            Resource: !Sub "${SiteBucket.Arn}/*"
            Condition:
              StringEquals:
                AWS:SourceArn: !Sub "arn:aws:cloudfront::${AWS::AccountId}:distribution/${WebDistribution}"
```

- [ ] **Step 2: Add the hosting outputs**

Add under `Outputs:`:

```yaml
  SiteBucketName:
    Value: !Ref SiteBucket
  SiteUrl:
    Value: !Sub "https://${WebDistribution.DomainName}"
  DistributionId:
    Value: !Ref WebDistribution
```

- [ ] **Step 3: Validate the whole template**

Run: `cd webapp/infra && sam validate --lint --region us-east-1`
Expected: `template.yaml is a valid SAM Template`. Fix any duplicate-key or unresolved-`!Ref` errors before committing.

- [ ] **Step 4: Commit**

```bash
git add webapp/infra/template.yaml
git commit -m "feat(webapp): SAM — S3 + CloudFront (OAC) static hosting"
```

---

### Task 8: Frontend scaffold — Next.js static export + Tailwind + theme

**Files:**
- Create: `webapp/frontend/package.json`
- Create: `webapp/frontend/next.config.mjs`
- Create: `webapp/frontend/tsconfig.json`
- Create: `webapp/frontend/tailwind.config.ts`
- Create: `webapp/frontend/postcss.config.mjs`
- Create: `webapp/frontend/.env.local.example`
- Create: `webapp/frontend/app/globals.css`
- Create: `webapp/frontend/app/layout.tsx`
- Create: `webapp/frontend/app/page.tsx` (placeholder, filled in Task 13)

**Interfaces:**
- Produces: a buildable static-export Next app. `npm run build` writes `out/`.

- [ ] **Step 1: package.json**

```json
{
  "name": "neat-graph-web-console",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "14.2.5",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "framer-motion": "11.3.8",
    "lucide-react": "0.417.0"
  },
  "devDependencies": {
    "typescript": "5.5.4",
    "@types/react": "18.3.3",
    "@types/react-dom": "18.3.0",
    "@types/node": "20.14.12",
    "tailwindcss": "3.4.7",
    "postcss": "8.4.40",
    "autoprefixer": "10.4.19",
    "vitest": "2.0.5",
    "jsdom": "24.1.1"
  }
}
```

- [ ] **Step 2: next.config.mjs (static export)**

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  trailingSlash: true,
};
export default nextConfig;
```

- [ ] **Step 3: tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["dom", "dom.iterable", "esnext"],
    "jsx": "preserve",
    "module": "esnext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true,
    "paths": { "@/*": ["./*"] }
  },
  "include": ["**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 4: Tailwind + PostCSS + globals**

`tailwind.config.ts`:

```ts
import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // slate-biased neutrals + a single indigo accent (chosen, not default grey)
        ink: '#0b1020',
        accent: '#4f46e5',
      },
    },
  },
  plugins: [],
};
export default config;
```

`postcss.config.mjs`:

```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

`app/globals.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root { color-scheme: light dark; }
html, body { height: 100%; }
```

- [ ] **Step 5: layout + placeholder page + env example**

`app/layout.tsx`:

```tsx
import './globals.css';
import type { ReactNode } from 'react';

export const metadata = { title: 'neat-graph-bedrock Console' };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-ink dark:bg-ink dark:text-slate-100">
        {children}
      </body>
    </html>
  );
}
```

`app/page.tsx`:

```tsx
export default function Home() {
  return <main className="p-8">Console coming online…</main>;
}
```

`.env.local.example`:

```
NEXT_PUBLIC_API_BASE=https://REPLACE.execute-api.us-east-1.amazonaws.com
NEXT_PUBLIC_COGNITO_DOMAIN=https://neat-graph-bedrock-web-<account_id>.auth.us-east-1.amazoncognito.com
NEXT_PUBLIC_CLIENT_ID=REPLACE_APP_CLIENT_ID
NEXT_PUBLIC_REDIRECT_URI=http://localhost:3000/callback
```

- [ ] **Step 6: Build to verify the export works**

Run: `cd webapp/frontend && npm install && npm run build`
Expected: build succeeds; `out/index.html` exists.

- [ ] **Step 7: Commit**

```bash
git add webapp/frontend/package.json webapp/frontend/next.config.mjs webapp/frontend/tsconfig.json webapp/frontend/tailwind.config.ts webapp/frontend/postcss.config.mjs webapp/frontend/.env.local.example webapp/frontend/app/
git commit -m "feat(webapp): Next.js static-export scaffold + Tailwind"
```

---

### Task 9: Frontend config + PKCE helpers (unit-tested)

**Files:**
- Create: `webapp/frontend/lib/config.ts`
- Create: `webapp/frontend/lib/pkce.ts`
- Create: `webapp/frontend/lib/pkce.test.ts`
- Create: `webapp/frontend/vitest.config.ts`

**Interfaces:**
- Produces:
  - `config` object: `{ apiBase, cognitoDomain, clientId, redirectUri }` from `NEXT_PUBLIC_*`.
  - `randomVerifier(): string` — 43-128 char URL-safe string.
  - `challengeFromVerifier(verifier: string): Promise<string>` — base64url SHA-256.
  - `base64UrlEncode(bytes: ArrayBuffer): string`.

- [ ] **Step 1: Write the failing test**

`webapp/frontend/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';
export default defineConfig({ test: { environment: 'jsdom' } });
```

(`jsdom` is already in devDependencies from Task 8; run `npm install` if not yet installed.)

`webapp/frontend/lib/pkce.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { randomVerifier, challengeFromVerifier, base64UrlEncode } from './pkce';

describe('pkce', () => {
  it('verifier is url-safe and long enough', () => {
    const v = randomVerifier();
    expect(v.length).toBeGreaterThanOrEqual(43);
    expect(v).toMatch(/^[A-Za-z0-9\-._~]+$/);
  });

  it('base64url has no +, / or =', () => {
    const s = base64UrlEncode(new Uint8Array([251, 252, 253]).buffer);
    expect(s).not.toMatch(/[+/=]/);
  });

  it('challenge is deterministic for a verifier', async () => {
    const c1 = await challengeFromVerifier('abc123');
    const c2 = await challengeFromVerifier('abc123');
    expect(c1).toBe(c2);
    expect(c1).not.toBe('abc123');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/frontend && npx vitest run lib/pkce.test.ts`
Expected: FAIL — `./pkce` not found.

- [ ] **Step 3: Write minimal implementation**

`webapp/frontend/lib/config.ts`:

```ts
export const config = {
  apiBase: process.env.NEXT_PUBLIC_API_BASE ?? '',
  cognitoDomain: process.env.NEXT_PUBLIC_COGNITO_DOMAIN ?? '',
  clientId: process.env.NEXT_PUBLIC_CLIENT_ID ?? '',
  redirectUri: process.env.NEXT_PUBLIC_REDIRECT_URI ?? '',
};
```

`webapp/frontend/lib/pkce.ts`:

```ts
export function base64UrlEncode(bytes: ArrayBuffer): string {
  const b = String.fromCharCode(...new Uint8Array(bytes));
  return btoa(b).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function randomVerifier(): string {
  const arr = new Uint8Array(32);
  crypto.getRandomValues(arr);
  return base64UrlEncode(arr.buffer);
}

export async function challengeFromVerifier(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64UrlEncode(digest);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/frontend && npx vitest run lib/pkce.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/lib/config.ts webapp/frontend/lib/pkce.ts webapp/frontend/lib/pkce.test.ts webapp/frontend/vitest.config.ts
git commit -m "feat(webapp): frontend config + PKCE helpers"
```

---

### Task 10: Frontend auth module (login / callback / logout)

**Files:**
- Create: `webapp/frontend/lib/auth.ts`
- Create: `webapp/frontend/lib/auth.test.ts`

**Interfaces:**
- Consumes: `config`, `randomVerifier`, `challengeFromVerifier`.
- Produces:
  - `beginLogin(): Promise<string>` — stores a fresh verifier in `sessionStorage` under `pkce_verifier` and returns the Cognito authorize URL (with `code_challenge`).
  - `exchangeCode(code: string, fetchFn=fetch): Promise<void>` — POSTs to `${cognitoDomain}/oauth2/token`, stores `id_token` in `localStorage` under `id_token`.
  - `getToken(): string | null` — reads `localStorage.id_token`.
  - `logout(): void` — clears `id_token` and redirects to the Cognito logout URL.
  - `isExpired(token: string): boolean` — decodes the JWT `exp` and compares to now.

- [ ] **Step 1: Write the failing test**

`webapp/frontend/lib/auth.test.ts`:

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { exchangeCode, getToken, isExpired } from './auth';

function makeJwt(expSecondsFromNow: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + expSecondsFromNow }));
  return `h.${payload}.s`;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('auth', () => {
  it('exchangeCode stores the id_token', async () => {
    sessionStorage.setItem('pkce_verifier', 'v');
    const fake = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id_token: 'TOKEN123' }),
    });
    await exchangeCode('the-code', fake as unknown as typeof fetch);
    expect(getToken()).toBe('TOKEN123');
    expect(fake).toHaveBeenCalledOnce();
  });

  it('isExpired true for a past token, false for a future one', () => {
    expect(isExpired(makeJwt(-10))).toBe(true);
    expect(isExpired(makeJwt(3600))).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/frontend && npx vitest run lib/auth.test.ts`
Expected: FAIL — `./auth` not found.

- [ ] **Step 3: Write minimal implementation**

`webapp/frontend/lib/auth.ts`:

```ts
import { config } from './config';
import { randomVerifier, challengeFromVerifier } from './pkce';

const VERIFIER_KEY = 'pkce_verifier';
const TOKEN_KEY = 'id_token';

export async function beginLogin(): Promise<string> {
  const verifier = randomVerifier();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const challenge = await challengeFromVerifier(verifier);
  const q = new URLSearchParams({
    client_id: config.clientId,
    response_type: 'code',
    scope: 'openid email profile',
    redirect_uri: config.redirectUri,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  });
  return `${config.cognitoDomain}/oauth2/authorize?${q.toString()}`;
}

export async function exchangeCode(code: string, fetchFn: typeof fetch = fetch): Promise<void> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY) ?? '';
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const resp = await fetchFn(`${config.cognitoDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!resp.ok) throw new Error('token exchange failed');
  const data = await resp.json();
  localStorage.setItem(TOKEN_KEY, data.id_token);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function isExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return typeof payload.exp !== 'number' || payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  const q = new URLSearchParams({
    client_id: config.clientId,
    logout_uri: config.redirectUri.replace('/callback', '/'),
  });
  window.location.href = `${config.cognitoDomain}/logout?${q.toString()}`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/frontend && npx vitest run lib/auth.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/lib/auth.ts webapp/frontend/lib/auth.test.ts
git commit -m "feat(webapp): frontend Cognito PKCE auth module"
```

---

### Task 11: Frontend API client (typed, unit-tested)

**Files:**
- Create: `webapp/frontend/lib/types.ts`
- Create: `webapp/frontend/lib/api.ts`
- Create: `webapp/frontend/lib/api.test.ts`

**Interfaces:**
- Consumes: `config`, `getToken`.
- Produces:
  - Types `Message = {role:'user'|'assistant'; text:string; ts:string}`, `Thread = {id:string; title:string; updatedAt:string}`, `ChatResult = {answer:string; conversationId:string}`.
  - `sendChat(prompt: string, conversationId: string | null, fetchFn=fetch): Promise<ChatResult>`.
  - `listConversations(fetchFn=fetch): Promise<Thread[]>`.
  - `getConversation(id: string, fetchFn=fetch): Promise<Message[]>`.
  - `deleteConversation(id: string, fetchFn=fetch): Promise<void>`.
  - Each attaches `Authorization: Bearer <token>` and throws `Error` on non-2xx.

- [ ] **Step 1: Write the failing test**

`webapp/frontend/lib/api.test.ts`:

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { sendChat, listConversations } from './api';

beforeEach(() => localStorage.setItem('id_token', 'TOK'));

describe('api', () => {
  it('sendChat posts prompt with bearer token and returns result', async () => {
    const fake = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ answer: 'hi', conversationId: 'c1' }),
    });
    const out = await sendChat('list rooms', null, fake as unknown as typeof fetch);
    expect(out.conversationId).toBe('c1');
    const [, opts] = fake.mock.calls[0];
    expect(opts.headers.Authorization).toBe('Bearer TOK');
    expect(JSON.parse(opts.body)).toEqual({ prompt: 'list rooms', conversationId: null });
  });

  it('throws on non-2xx', async () => {
    const fake = vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => ({ error: 'x' }) });
    await expect(listConversations(fake as unknown as typeof fetch)).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp/frontend && npx vitest run lib/api.test.ts`
Expected: FAIL — `./api` not found.

- [ ] **Step 3: Write minimal implementation**

`webapp/frontend/lib/types.ts`:

```ts
export type Message = { role: 'user' | 'assistant'; text: string; ts: string };
export type Thread = { id: string; title: string; updatedAt: string };
export type ChatResult = { answer: string; conversationId: string };
```

`webapp/frontend/lib/api.ts`:

```ts
import { config } from './config';
import { getToken } from './auth';
import type { Message, Thread, ChatResult } from './types';

async function req<T>(path: string, init: RequestInit, fetchFn: typeof fetch): Promise<T> {
  const resp = await fetchFn(`${config.apiBase}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken() ?? ''}`,
      ...(init.headers ?? {}),
    },
  });
  if (!resp.ok) throw new Error(`request failed (${resp.status})`);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export function sendChat(prompt: string, conversationId: string | null,
                         fetchFn: typeof fetch = fetch): Promise<ChatResult> {
  return req<ChatResult>('/chat', {
    method: 'POST', body: JSON.stringify({ prompt, conversationId }),
  }, fetchFn);
}

export async function listConversations(fetchFn: typeof fetch = fetch): Promise<Thread[]> {
  const data = await req<{ conversations: Thread[] }>('/conversations', { method: 'GET' }, fetchFn);
  return data.conversations;
}

export async function getConversation(id: string, fetchFn: typeof fetch = fetch): Promise<Message[]> {
  const data = await req<{ messages: Message[] }>(`/conversations/${id}`, { method: 'GET' }, fetchFn);
  return data.messages;
}

export function deleteConversation(id: string, fetchFn: typeof fetch = fetch): Promise<void> {
  return req<void>(`/conversations/${id}`, { method: 'DELETE' }, fetchFn);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp/frontend && npx vitest run lib/api.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/lib/types.ts webapp/frontend/lib/api.ts webapp/frontend/lib/api.test.ts
git commit -m "feat(webapp): frontend typed API client"
```

---

### Task 12: Chat UI — bubbles, composer, thread (Framer Motion)

**Files:**
- Create: `webapp/frontend/components/MessageBubble.tsx`
- Create: `webapp/frontend/components/Composer.tsx`
- Create: `webapp/frontend/components/ChatThread.tsx`

**Interfaces:**
- Consumes: `Message` type (Task 11).
- Produces:
  - `MessageBubble({ message }: { message: Message })`.
  - `Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void })`.
  - `ChatThread({ messages, pending }: { messages: Message[]; pending: boolean })` — renders bubbles with a Framer Motion enter transition (respecting `prefers-reduced-motion`) and a "thinking…" row when `pending`.

- [ ] **Step 1: MessageBubble**

`webapp/frontend/components/MessageBubble.tsx`:

```tsx
'use client';
import { motion, useReducedMotion } from 'framer-motion';
import type { Message } from '@/lib/types';

export function MessageBubble({ message }: { message: Message }) {
  const reduce = useReducedMotion();
  const isUser = message.role === 'user';
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div className={`max-w-[80ch] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${
        isUser ? 'bg-accent text-white' : 'bg-slate-100 text-ink dark:bg-slate-800 dark:text-slate-100'
      }`}>
        {message.text}
      </div>
    </motion.div>
  );
}
```

- [ ] **Step 2: Composer**

`webapp/frontend/components/Composer.tsx`:

```tsx
'use client';
import { useState } from 'react';
import { SendHorizontal } from 'lucide-react';

export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (t: string) => void }) {
  const [text, setText] = useState('');
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
  };
  return (
    <div className="flex gap-2 border-t border-slate-200 p-3 dark:border-slate-700">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        rows={1}
        placeholder="Ask about the deployment…"
        className="flex-1 resize-none rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-accent dark:border-slate-600"
      />
      <button
        onClick={submit}
        disabled={disabled}
        aria-label="Send"
        className="rounded-lg bg-accent px-3 text-white disabled:opacity-40"
      >
        <SendHorizontal size={18} />
      </button>
    </div>
  );
}
```

- [ ] **Step 3: ChatThread**

`webapp/frontend/components/ChatThread.tsx`:

```tsx
'use client';
import { MessageBubble } from './MessageBubble';
import type { Message } from '@/lib/types';

export function ChatThread({ messages, pending }: { messages: Message[]; pending: boolean }) {
  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {messages.map((m) => <MessageBubble key={m.ts + m.role} message={m} />)}
      {pending && (
        <div className="text-sm text-slate-400">thinking…</div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd webapp/frontend && npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/components/MessageBubble.tsx webapp/frontend/components/Composer.tsx webapp/frontend/components/ChatThread.tsx
git commit -m "feat(webapp): chat thread, composer, message bubbles"
```

---

### Task 13: Sidebar + error toast + wire the console page + callback route

**Files:**
- Create: `webapp/frontend/components/Sidebar.tsx`
- Create: `webapp/frontend/components/ErrorToast.tsx`
- Create: `webapp/frontend/app/callback/page.tsx`
- Modify: `webapp/frontend/app/page.tsx` (full console)

**Interfaces:**
- Consumes: all `lib/*` and `components/*` from Tasks 9-12.
- Produces: the working single-page console (auth gate → sidebar + thread + composer), and the `/callback` route that finishes the PKCE exchange.

- [ ] **Step 1: Sidebar**

`webapp/frontend/components/Sidebar.tsx`:

```tsx
'use client';
import { motion, AnimatePresence } from 'framer-motion';
import { Plus, Trash2, MessageSquare } from 'lucide-react';
import type { Thread } from '@/lib/types';

export function Sidebar({ threads, activeId, onSelect, onNew, onDelete }: {
  threads: Thread[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  return (
    <aside className="flex w-64 flex-col border-r border-slate-200 dark:border-slate-700">
      <button onClick={onNew} className="m-3 flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm text-white">
        <Plus size={16} /> New chat
      </button>
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence>
          {threads.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className={`group flex items-center justify-between px-3 py-2 text-sm ${
                t.id === activeId ? 'bg-slate-100 dark:bg-slate-800' : ''
              }`}
            >
              <button onClick={() => onSelect(t.id)} className="flex items-center gap-2 truncate text-left">
                <MessageSquare size={14} /> <span className="truncate">{t.title}</span>
              </button>
              <button onClick={() => onDelete(t.id)} aria-label="Delete" className="opacity-0 group-hover:opacity-100">
                <Trash2 size={14} />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: ErrorToast**

`webapp/frontend/components/ErrorToast.tsx`:

```tsx
'use client';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertCircle } from 'lucide-react';

export function ErrorToast({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  return (
    <AnimatePresence>
      {message && (
        <motion.div
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }}
          className="fixed bottom-4 right-4 flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm text-white"
          role="alert" onClick={onDismiss}
        >
          <AlertCircle size={16} /> {message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

- [ ] **Step 3: /callback route**

`webapp/frontend/app/callback/page.tsx`:

```tsx
'use client';
import { useEffect, useState } from 'react';
import { exchangeCode } from '@/lib/auth';

export default function Callback() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code');
    if (!code) { setError('missing authorization code'); return; }
    exchangeCode(code)
      .then(() => { window.location.href = '/'; })
      .catch(() => setError('login failed'));
  }, []);
  return <main className="p-8 text-sm">{error ?? 'Signing you in…'}</main>;
}
```

- [ ] **Step 4: The console page (auth gate + state wiring)**

Replace `webapp/frontend/app/page.tsx`:

```tsx
'use client';
import { useEffect, useState, useCallback } from 'react';
import { getToken, isExpired, beginLogin, logout } from '@/lib/auth';
import { sendChat, listConversations, getConversation, deleteConversation } from '@/lib/api';
import type { Message, Thread } from '@/lib/types';
import { Sidebar } from '@/components/Sidebar';
import { ChatThread } from '@/components/ChatThread';
import { Composer } from '@/components/Composer';
import { ErrorToast } from '@/components/ErrorToast';
import { LogOut } from 'lucide-react';

export default function Home() {
  const [ready, setReady] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tok = getToken();
    if (!tok || isExpired(tok)) { beginLogin().then((u) => (window.location.href = u)); return; }
    setReady(true);
    listConversations().then(setThreads).catch(() => setError('could not load history'));
  }, []);

  const refreshThreads = useCallback(() => {
    listConversations().then(setThreads).catch(() => {});
  }, []);

  const openThread = async (id: string) => {
    setActiveId(id);
    try { setMessages(await getConversation(id)); }
    catch { setError('could not open conversation'); }
  };

  const newChat = () => { setActiveId(null); setMessages([]); };

  const onSend = async (text: string) => {
    const ts = new Date().toISOString();
    setMessages((m) => [...m, { role: 'user', text, ts }]);
    setPending(true);
    try {
      const res = await sendChat(text, activeId);
      setActiveId(res.conversationId);
      setMessages((m) => [...m, { role: 'assistant', text: res.answer, ts: new Date().toISOString() }]);
      refreshThreads();
    } catch {
      setError('the agent could not be reached');
    } finally {
      setPending(false);
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteConversation(id);
      if (id === activeId) newChat();
      refreshThreads();
    } catch { setError('could not delete conversation'); }
  };

  if (!ready) return <main className="p-8 text-sm">Redirecting to sign-in…</main>;

  return (
    <div className="flex h-screen">
      <Sidebar threads={threads} activeId={activeId} onSelect={openThread} onNew={newChat} onDelete={onDelete} />
      <main className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <span className="font-semibold">neat-graph-bedrock Console</span>
          <button onClick={logout} aria-label="Sign out" className="flex items-center gap-1 text-sm text-slate-500">
            <LogOut size={16} /> Sign out
          </button>
        </header>
        <ChatThread messages={messages} pending={pending} />
        <Composer disabled={pending} onSend={onSend} />
      </main>
      <ErrorToast message={error} onDismiss={() => setError(null)} />
    </div>
  );
}
```

- [ ] **Step 5: Full frontend build + tests**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: all vitest tests pass; `npm run build` produces `out/` with `index.html` and `callback/index.html`.

- [ ] **Step 6: Commit**

```bash
git add webapp/frontend/components/ webapp/frontend/app/
git commit -m "feat(webapp): sidebar, error toast, callback route, console page"
```

---

### Task 14: Deploy glue + README + manual E2E checklist

**Files:**
- Modify: `Makefile` (add web-console targets)
- Create: `webapp/README.md`

**Interfaces:**
- Consumes: the SAM stack (Tasks 5-7), the frontend build (Tasks 8-13), the existing stack's `UserPoolId` output and the runtime ARN.

- [ ] **Step 1: Add Makefile targets**

Append to `Makefile` (recipe lines use TABS; `WEBSTACK`/`RUNTIME_ARN` are overridable):

```make
WEBSTACK    ?= $(PROJECT)-web
RUNTIME_ARN ?= arn:aws:bedrock-agentcore:us-east-1:<account_id>:runtime/<runtime-id>

.PHONY: webapp-deploy webapp-build webapp-sync

# Deploy API + storage + auth + hosting. Pass POOL_ID=<existing UserPoolId>.
webapp-deploy:
	cd webapp/infra && sam build --template template.yaml && sam deploy \
		--stack-name $(WEBSTACK) --region $(REGION) --resolve-s3 \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides ProjectName=$(PROJECT) \
			ExistingUserPoolId=$(POOL_ID) RuntimeArn=$(RUNTIME_ARN) \
		--no-confirm-changeset

webapp-build:
	cd webapp/frontend && npm install && npm run build

# Upload the static export and invalidate CloudFront. Pass BUCKET= and DIST_ID= (stack outputs).
webapp-sync:
	aws s3 sync webapp/frontend/out s3://$(BUCKET)/ --delete --region $(REGION)
	aws cloudfront create-invalidation --distribution-id $(DIST_ID) --paths "/*"
```

- [ ] **Step 2: Write the deploy + E2E doc**

Create `webapp/README.md`:

````markdown
# neat-graph-bedrock Web Console (Phase 1)

Authenticated chat UI for the AgentCore Runtime. Design:
`docs/superpowers/specs/2026-07-08-web-console-design.md`.

## Deploy (us-east-1)

1. Get the existing stack's user pool id:
   `aws cloudformation describe-stacks --stack-name neat-graph-bedrock --query "Stacks[0].Outputs" --region us-east-1`
   → note `UserPoolId`.
2. Deploy the backend + hosting:
   `make webapp-deploy POOL_ID=<UserPoolId>`
   Record the outputs: `ApiBaseUrl`, `AppClientId`, `CognitoDomain`, `SiteBucketName`,
   `SiteUrl`, `DistributionId`.
3. Configure the frontend env (`webapp/frontend/.env.local`, copy from
   `.env.local.example`) with `NEXT_PUBLIC_API_BASE=<ApiBaseUrl>`,
   `NEXT_PUBLIC_COGNITO_DOMAIN=<CognitoDomain>`, `NEXT_PUBLIC_CLIENT_ID=<AppClientId>`,
   `NEXT_PUBLIC_REDIRECT_URI=<SiteUrl>/callback`.
4. Build + upload:
   `make webapp-build && make webapp-sync BUCKET=<SiteBucketName> DIST_ID=<DistributionId>`
5. Create a test user:
   `aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username you@example.com`
   then set a permanent password with `admin-set-user-password`.

## Manual E2E checklist

- [ ] Visit `<SiteUrl>` → redirected to the Cognito Hosted UI; sign in.
- [ ] Ask "List the Neat rooms." → an answer returns.
- [ ] Follow up "How many is that?" in the same thread → memory recall works.
- [ ] Reload the page → the conversation reappears in the sidebar.
- [ ] Delete the conversation → it disappears; the thread clears.
- [ ] Sign in as a second Cognito user → the first user's threads are NOT visible.
- [ ] Confirm read-only: the agent only answers; no upstream writes occur.
````

- [ ] **Step 3: Verify the Makefile parses**

Run: `make -n webapp-build`
Expected: prints the `npm install && npm run build` commands without a parse error.

- [ ] **Step 4: Commit**

```bash
git add Makefile webapp/README.md
git commit -m "feat(webapp): deploy targets + README + E2E checklist"
```

---

## Self-Review Notes (author)

- **Spec coverage:** SPA+CloudFront (T7,T8), Cognito JWT authorizer + auth-code client (T5,T6,T10), Lambda invoker reusing invoke logic (T1,T3), DynamoDB per-user history + sidebar (T2,T4,T13), `conversationId==sessionId` ≥33 chars (T3), read-only guarantee (Global Constraints; enforced by Lambda IAM in T5 + per-`sub` store in T2), buffered (T3), ISO-8601 `Z` timestamps (T3 `_ts`), Next static export stack (T8-T13), error handling (T3 502 / T13 toast / T10 401 redirect). All covered.
- **Type consistency:** `Message`/`Thread`/`ChatResult` defined in T11 `lib/types.ts` and used unchanged in T12-T13; API `{conversations}` / `{messages}` envelopes match handler responses in T3-T4; `conversationId` field name consistent across handler, api client, and page.
- **Deferred (later phases, not in this plan):** Entra federation, Graph group-gating, streaming.
