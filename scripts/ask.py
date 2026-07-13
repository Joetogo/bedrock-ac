#!/usr/bin/env python3
"""One-shot natural-language runner. Reads config from .env so you only type the prompt.

  python scripts/ask.py "List the Neat rooms and their current CO2 levels."

.env (next to this repo root) must define MCP_URL, TOKEN_URL, CLIENT_ID,
CLIENT_SECRET, AWS_REGION, OAUTH_SCOPE. MODEL_ID is optional - if absent the
runner picks a current Claude inference profile from your account.

Deps: boto3, httpx, mcp   (pip install boto3 httpx mcp)
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import boto3
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


# Use the SAME system prompt the deployed runtime uses, so local ask.py testing
# faithfully reflects production (charting, no-emoji, correlation caveats).
# Loaded by path to avoid a package install — mirrors invoke_agent.py.
_agent_spec = importlib.util.spec_from_file_location(
    "neat_agent", Path(__file__).resolve().parent.parent / "agent" / "agent.py")
_agent = importlib.util.module_from_spec(_agent_spec)
sys.modules[_agent_spec.name] = _agent  # let @dataclass resolve its own module
_agent_spec.loader.exec_module(_agent)
SYSTEM_PROMPT = _agent.SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Minimal .env loader (no python-dotenv dependency)
# --------------------------------------------------------------------------- #
def load_env() -> None:
    # look for .env at repo root (parent of scripts/) then cwd
    for candidate in (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def need(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        sys.exit(f"missing required env var: {key} (set it in .env)")
    return val


# --------------------------------------------------------------------------- #
# Auto-resolve a usable Claude inference profile if MODEL_ID isn't pinned
# --------------------------------------------------------------------------- #
def resolve_model(region: str) -> str:
    pinned = os.environ.get("MODEL_ID")
    if pinned:
        return pinned
    bedrock = boto3.client("bedrock", region_name=region)
    profiles = bedrock.list_inference_profiles().get("inferenceProfileSummaries", [])
    ids = [p["inferenceProfileId"] for p in profiles]
    # prefer newest Sonnet, then any Sonnet, then any Claude.
    for pref in ("claude-sonnet-4-6", "claude-sonnet-4", "claude-sonnet", "claude"):
        for pid in ids:
            if pref in pid:
                print(f"[model] auto-selected {pid}")
                return pid
    sys.exit("no Claude inference profile found in this account/region; "
             "enable model access in the Bedrock console or set MODEL_ID in .env")


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
    return [{
        "toolSpec": {
            "name": t.name,
            "description": t.description or t.name,
            "inputSchema": {"json": t.inputSchema or {"type": "object", "properties": {}}},
        }
    } for t in mcp_tools]


async def run(prompt: str) -> None:
    region = need("AWS_REGION")
    model = resolve_model(region)
    token = get_token(need("TOKEN_URL"), need("CLIENT_ID"),
                      need("CLIENT_SECRET"), os.environ.get("OAUTH_SCOPE", ""))
    headers = {"Authorization": f"Bearer {token}"}
    brt = boto3.client("bedrock-runtime", region_name=region)

    async with streamablehttp_client(need("MCP_URL"), headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"[tools] {', '.join(t.name for t in tools)}")
            tool_config = {"tools": to_bedrock_tools(tools)}

            messages = [{"role": "user", "content": [{"text": prompt}]}]
            system = [{"text": SYSTEM_PROMPT}]

            while True:
                resp = brt.converse(modelId=model, messages=messages,
                                    system=system, toolConfig=tool_config)
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
                    print(f"[tool] {tu['name']} {tu['input']}")
                    result = await session.call_tool(tu["name"], tu["input"])
                    payload = "".join(c.text for c in result.content if c.type == "text")
                    tool_results.append({"toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"text": payload}],
                    }})
                messages.append({"role": "user", "content": tool_results})


def main() -> None:
    # Windows consoles default to cp1252; model answers often contain emoji.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    load_env()
    if len(sys.argv) < 2:
        sys.exit('usage: python scripts/ask.py "your question here"')
    prompt = " ".join(sys.argv[1:])
    asyncio.run(run(prompt))


if __name__ == "__main__":
    main()
