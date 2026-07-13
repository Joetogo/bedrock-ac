#!/usr/bin/env python3
"""Invoke the deployed AgentCore Runtime agent from the CLI.

  python scripts/invoke_agent.py "List the Neat rooms." --session demo-1

Runtime ARN is read from --runtime-arn or the AGENT_RUNTIME_ARN env var
(printed by `agentcore launch`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import boto3

import importlib.util
from pathlib import Path

_rc_spec = importlib.util.spec_from_file_location(
    "agentcore_client",
    Path(__file__).resolve().parent.parent / "webapp" / "api" / "agentcore_client.py")
_rc = importlib.util.module_from_spec(_rc_spec)
_rc_spec.loader.exec_module(_rc)

build_payload = _rc.build_payload      # re-export: keeps tests/test_invoke_agent.py green
parse_response = _rc.parse_response


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 + emoji
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--session", default="cli-default")
    ap.add_argument("--runtime-arn", default=os.environ.get("AGENT_RUNTIME_ARN"))
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    if not args.runtime_arn:
        sys.exit("set --runtime-arn or AGENT_RUNTIME_ARN (from `agentcore launch`)")

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=args.runtime_arn,
        runtimeSessionId=args.session,
        payload=build_payload(args.prompt, args.session),
    )
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    print(parse_response(body if isinstance(body, bytes) else str(body).encode()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
