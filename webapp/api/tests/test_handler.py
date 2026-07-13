import json

import pytest

import handler
import agentcore_client as runtime_client
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


# ---- POST /chat: async submit -------------------------------------------------

def test_chat_submit_returns_job_and_enqueues_worker(monkeypatch):
    enq = {}
    monkeypatch.setattr(handler, "_invoke_worker", lambda payload: enq.update(payload))
    saved = []
    monkeypatch.setattr(store, "put_message", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)
    jobs = []
    monkeypatch.setattr(store, "put_job", lambda sub, jid, status, **k: jobs.append((jid, status)))

    resp = handler.handler(_event("POST", "/chat", body={"prompt": "list rooms", "conversationId": None}), None)
    assert resp["statusCode"] == 202
    payload = json.loads(resp["body"])
    assert len(payload["conversationId"]) >= 33            # runtime session-id rule
    assert payload["jobId"]
    assert len(saved) == 1 and saved[0][2] == "user"       # user message persisted immediately
    assert jobs and jobs[0][1] == "pending"                # pending job persisted
    assert enq["mode"] == "worker"                         # worker enqueued...
    assert enq["jobId"] == payload["jobId"]                # ...with matching ids + prompt
    assert enq["conversationId"] == payload["conversationId"]
    assert enq["prompt"] == "list rooms"


def test_chat_submit_requires_prompt(monkeypatch):
    monkeypatch.setattr(handler, "_invoke_worker", lambda payload: None)
    resp = handler.handler(_event("POST", "/chat", body={"prompt": "   "}), None)
    assert resp["statusCode"] == 400


def test_chat_submit_reuses_supplied_conversation_id(monkeypatch):
    enq = {}
    monkeypatch.setattr(handler, "_invoke_worker", lambda payload: enq.update(payload))
    monkeypatch.setattr(store, "put_message", lambda *a, **k: None)
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)
    monkeypatch.setattr(store, "put_job", lambda *a, **k: None)

    cid = "abcdefabcdef-abcdef-abcdef-abcdef1234"
    resp = handler.handler(_event("POST", "/chat", body={"prompt": "hi", "conversationId": cid}), None)
    assert json.loads(resp["body"])["conversationId"] == cid
    assert enq["conversationId"] == cid                    # worker gets the same conversation id


# ---- worker path: runs the agent ---------------------------------------------

def test_worker_runs_agent_and_marks_job_done(monkeypatch):
    monkeypatch.setattr(runtime_client, "invoke_runtime", lambda prompt, session, **k: f"answer for {session}")
    saved = []
    monkeypatch.setattr(store, "put_message", lambda sub, cid, role, text, ts: saved.append((role, text)))
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)
    jobs = []
    monkeypatch.setattr(store, "put_job", lambda sub, jid, status, **k: jobs.append((status, k)))

    conv = "conv-abc-conv-abc-conv-abc-conv-abc12"
    ev = {"mode": "worker", "sub": "u1", "conversationId": conv,
          "jobId": "j1", "prompt": "hi", "tsUser": "2024-01-01T00:00:00.000Z"}
    out = handler.handler(ev, None)
    assert out == {"ok": True}
    assert ("assistant", f"answer for {conv}") in saved    # conversationId == runtime session
    assert jobs[-1][0] == "done"
    assert jobs[-1][1]["answer"] == f"answer for {conv}"


def test_worker_same_ms_timestamp_orders_after_user(monkeypatch):
    monkeypatch.setattr(runtime_client, "invoke_runtime", lambda *a, **k: "answer")
    monkeypatch.setattr(handler, "_ts", lambda: "2024-01-01T00:00:00.000Z")
    saved = []
    monkeypatch.setattr(store, "put_message", lambda sub, cid, role, text, ts: saved.append((role, ts)))
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)
    monkeypatch.setattr(store, "put_job", lambda *a, **k: None)

    ev = {"mode": "worker", "sub": "u1", "conversationId": "c", "jobId": "j1",
          "prompt": "hi", "tsUser": "2024-01-01T00:00:00.000Z"}
    handler.handler(ev, None)
    asst_ts = dict(saved)["assistant"]
    assert asst_ts > "2024-01-01T00:00:00.000Z"            # distinct SK, chronological order


def test_worker_runtime_error_marks_job_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("runtime down")
    monkeypatch.setattr(runtime_client, "invoke_runtime", boom)
    monkeypatch.setattr(store, "put_message", lambda *a, **k: None)
    monkeypatch.setattr(store, "upsert_thread", lambda *a, **k: None)
    jobs = []
    monkeypatch.setattr(store, "put_job", lambda sub, jid, status, **k: jobs.append((status, k)))

    ev = {"mode": "worker", "sub": "u1", "conversationId": "c", "jobId": "j1",
          "prompt": "hi", "tsUser": "2024-01-01T00:00:00.000Z"}
    out = handler.handler(ev, None)
    assert out == {"ok": False}
    assert jobs[-1][0] == "error"
    assert "runtime down" in jobs[-1][1]["error"]


# ---- GET /chat/{jobId}: poll status ------------------------------------------

def test_get_job_status_done(monkeypatch):
    monkeypatch.setattr(store, "get_job",
                        lambda sub, jid: {"status": "done", "conversationId": "c1", "answer": "hi", "error": ""})
    ev = _event("GET", "/chat/j1", route="GET /chat/{jobId}")
    ev["pathParameters"] = {"jobId": "j1"}
    resp = handler.handler(ev, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["answer"] == "hi"


def test_get_job_status_not_found(monkeypatch):
    monkeypatch.setattr(store, "get_job", lambda sub, jid: None)
    ev = _event("GET", "/chat/nope", route="GET /chat/{jobId}")
    ev["pathParameters"] = {"jobId": "nope"}
    resp = handler.handler(ev, None)
    assert resp["statusCode"] == 404


# ---- auth + conversation routes ----------------------------------------------

def test_missing_sub_returns_401():
    resp = handler.handler(_event("POST", "/chat", sub=None, body={"prompt": "hi"}), None)
    assert resp["statusCode"] == 401


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
