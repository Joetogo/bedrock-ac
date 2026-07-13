"""API Gateway HTTP API (v2) Lambda for the web console.

Routes (all behind a Cognito JWT authorizer):
    POST   /chat                 -> submit a turn; returns {jobId, conversationId} (202)
    GET    /chat/{jobId}         -> poll job status/answer
    GET    /conversations
    GET    /conversations/{id}
    DELETE /conversations/{id}

`POST /chat` returns immediately and fires an async self-invocation of this
same function (`mode: "worker"`) that runs the agent. This sidesteps the API
Gateway HTTP API 30-second integration cap: the worker runs up to the Lambda
timeout, and the browser polls `GET /chat/{jobId}` for the result.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3

import agentcore_client
import store

_CORS = {
    "Content-Type": "application/json",
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


def _bump_ms(ts: str) -> str:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    dt += timedelta(milliseconds=1)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _invoke_worker(payload: dict) -> None:
    """Fire-and-forget async self-invocation that runs the agent."""
    boto3.client("lambda").invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _chat(sub: str, event: dict) -> dict:
    body = json.loads(event.get("body") or "{}")
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return _resp(400, {"error": "prompt is required"})
    conv_id = body.get("conversationId") or str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    ts_user = _ts()

    try:
        store.upsert_thread(sub, conv_id, prompt[:60], ts_user)
        store.put_message(sub, conv_id, "user", prompt, ts_user)
        store.put_job(sub, job_id, "pending", conv_id=conv_id, ts=ts_user)
    except Exception:                              # noqa: BLE001 - best-effort persistence
        pass

    _invoke_worker({
        "mode": "worker", "sub": sub, "conversationId": conv_id,
        "jobId": job_id, "prompt": prompt, "tsUser": ts_user,
    })
    return _resp(202, {"jobId": job_id, "conversationId": conv_id})


def _worker(event: dict) -> dict:
    """Async path: run the agent and record the result under the job id."""
    sub = event["sub"]
    conv_id = event["conversationId"]
    job_id = event["jobId"]
    prompt = event["prompt"]
    ts_user = event.get("tsUser") or _ts()

    try:
        answer = agentcore_client.invoke_runtime(
            prompt, conv_id,
            runtime_arn=os.environ["RUNTIME_ARN"],
            region=os.environ.get("AWS_REGION", "us-east-1"))
    except Exception as exc:                       # noqa: BLE001 - surfaced to the UI via job
        try:
            store.put_job(sub, job_id, "error", conv_id=conv_id,
                          error=f"runtime invocation failed: {exc}", ts=_ts())
        except Exception:                          # noqa: BLE001
            pass
        return {"ok": False}

    try:
        ts_asst = _ts()
        if ts_asst <= ts_user:
            ts_asst = _bump_ms(ts_user)
        store.upsert_thread(sub, conv_id, prompt[:60], ts_asst)
        store.put_message(sub, conv_id, "assistant", answer, ts_asst)
        store.put_job(sub, job_id, "done", conv_id=conv_id, answer=answer, ts=ts_asst)
    except Exception:                              # noqa: BLE001 - history is best-effort
        pass
    return {"ok": True}


def _job_status(sub: str, event: dict) -> dict:
    job_id = (event.get("pathParameters") or {}).get("jobId", "")
    job = store.get_job(sub, job_id)
    if not job:
        return _resp(404, {"error": "job not found"})
    return _resp(200, job)


def _list(sub: str) -> dict:
    return _resp(200, {"conversations": store.list_threads(sub)})


def _get(sub: str, event: dict) -> dict:
    cid = (event.get("pathParameters") or {}).get("id", "")
    return _resp(200, {"id": cid, "messages": store.get_messages(sub, cid)})


def _delete(sub: str, event: dict) -> dict:
    cid = (event.get("pathParameters") or {}).get("id", "")
    store.delete_thread(sub, cid)
    return _resp(204, None)


def handler(event: dict, context) -> dict:
    # Async worker self-invocation carries no HTTP context.
    if event.get("mode") == "worker":
        return _worker(event)

    sub = _sub(event)
    if not sub:
        return _resp(401, {"error": "unauthorized"})
    route = event.get("routeKey", "")
    if route == "POST /chat":
        return _chat(sub, event)
    if route == "GET /chat/{jobId}":
        return _job_status(sub, event)
    if route == "GET /conversations":
        return _list(sub)
    if route == "GET /conversations/{id}":
        return _get(sub, event)
    if route == "DELETE /conversations/{id}":
        return _delete(sub, event)
    return _resp(404, {"error": f"no route for {route}"})
