#!/usr/bin/env python3
"""Natural-language entry point.

Connects to the AgentCore Gateway over MCP (streamable HTTP), lists the tools,
and runs a Bedrock Converse tool-use loop: the model picks tools, we execute
them through the gateway, feed results back, and return the final answer.

  python scripts/agent.py \
      --mcp-url https://...gateway... \
      --token-url https://...cognito.../oauth2/token \
      --client-id ... --client-secret ... \
      --model anthropic.claude-3-5-sonnet-20241022-v2:0 \
      --region ap-southeast-2 \
      "Which rooms had high CO2 today and did their Teams calls have worse jitter?"

Requires: boto3, mcp (pip install mcp), httpx
"""
from __future__ import annotations

import argparse
import asyncio
import json

import boto3
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def get_token(token_url: str, client_id: str, client_secret: str, scope: str) -> str:
    r = httpx.post(token_url, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }, auth=(client_id, client_secret), timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def to_bedrock_tools(mcp_tools) -> list[dict]:
    """MCP tool defs -> Bedrock Converse toolConfig."""
    return [{
        "toolSpec": {
            "name": t.name,
            "description": t.description or t.name,
            "inputSchema": {"json": t.inputSchema or {"type": "object", "properties": {}}},
        }
    } for t in mcp_tools]


async def run(args) -> None:
    token = get_token(args.token_url, args.client_id, args.client_secret, args.scope)
    headers = {"Authorization": f"Bearer {token}"}
    brt = boto3.client("bedrock-runtime", region_name=args.region)

    async with streamablehttp_client(args.mcp_url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            tool_config = {"tools": to_bedrock_tools(tools)}

            messages = [{"role": "user", "content": [{"text": args.prompt}]}]
            system = [{"text":
                "You answer questions about meeting-room conditions and Teams call "
                "quality by calling the provided tools. Call records appear ~30 min "
                "after a call ends. When correlating, state correlation is "
                "observational, not causal. Be concise."}]

            while True:
                resp = brt.converse(
                    modelId=args.model,
                    messages=messages,
                    system=system,
                    toolConfig=tool_config,
                )
                out = resp["output"]["message"]
                messages.append(out)

                if resp["stopReason"] != "tool_use":
                    text = "".join(b.get("text", "") for b in out["content"])
                    print("\n=== ANSWER ===\n" + text)
                    return

                tool_results = []
                for block in out["content"]:
                    if "toolUse" not in block:
                        continue
                    tu = block["toolUse"]
                    print(f"[tool] {tu['name']} {json.dumps(tu['input'])}")
                    result = await session.call_tool(tu["name"], tu["input"])
                    payload = "".join(c.text for c in result.content if c.type == "text")
                    tool_results.append({"toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"text": payload}],
                    }})
                messages.append({"role": "user", "content": tool_results})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcp-url", required=True)
    ap.add_argument("--token-url", required=True)
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--scope", default="neat-graph-bedrock-api/invoke")
    ap.add_argument("--model", default="anthropic.claude-3-5-sonnet-20241022-v2:0")
    ap.add_argument("--region", required=True)
    ap.add_argument("prompt")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
