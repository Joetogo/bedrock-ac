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
