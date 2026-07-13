#!/usr/bin/env python3
"""Create the AgentCore Gateway and attach the three Lambda tool targets.

Run AFTER the SAM stack deploys. Reads stack outputs, then uses the
bedrock-agentcore-control client to create a gateway (MCP protocol, Cognito
JWT inbound auth) and one Lambda target per tool group, supplying the tool
schema upfront so targets are immediately READY.

  python scripts/deploy_gateway.py --stack neat-graph-bedrock --region ap-southeast-2
"""
from __future__ import annotations

import argparse
import sys
import time

import boto3

# Tool schemas. AgentCore Lambda targets accept inline JSON-schema tool defs;
# the model sees these names/descriptions when choosing tools.
TARGETS = {
    "neat": {
        "fn_output": "NeatSenseFnArn",
        "tools": [
            {
                "name": "neat_list_rooms",
                "description": "List Neat-managed rooms/spaces with their endpoint ids.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "neat_room_sensors",
                "description": "Get environmental + occupancy telemetry (CO2, temp, "
                               "humidity, people count, VOC) for one room over a time window.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "room_id": {"type": "string"},
                        "fromDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                        "toDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                    "required": ["room_id"],
                },
            },
        ],
    },
    "graph": {
        "fn_output": "GraphCallsFnArn",
        "tools": [
            {
                "name": "graph_list_call_records",
                "description": "List Teams call records that started within a UTC window. "
                               "Records appear ~30 min after a call ends.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fromDateTime": {"type": "string"},
                        "toDateTime": {"type": "string"},
                    },
                    "required": ["fromDateTime", "toDateTime"],
                },
            },
            {
                "name": "graph_call_quality",
                "description": "Per-session media quality (jitter, packet loss, round-trip, "
                               "codec) for a single call record id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"call_id": {"type": "string"}},
                    "required": ["call_id"],
                },
            },
        ],
    },
    "correlate": {
        "fn_output": "CorrelateFnArn",
        "tools": [
            {
                "name": "correlate_room_calls",
                "description": "Join a room's environment/occupancy with the quality of "
                               "Teams calls in that room over a window. Use to test whether "
                               "room conditions track call quality.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "room_id": {"type": "string"},
                        "fromDateTime": {"type": "string"},
                        "toDateTime": {"type": "string"},
                    },
                    "required": ["room_id", "fromDateTime", "toDateTime"],
                },
            },
        ],
    },
    "thousandeyes": {
        "fn_output": "ThousandEyesFnArn",
        "tools": [
            {
                "name": "te_list_tests_alerts",
                "description": "List configured ThousandEyes tests and any active alerts "
                               "in a UTC window. Use to discover test ids before fetching "
                               "results.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fromDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                        "toDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                },
            },
            {
                "name": "te_network_results",
                "description": "Network-layer results (loss, latency, jitter per agent) for "
                               "one ThousandEyes test over a UTC window.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "test_id": {"type": "string"},
                        "fromDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                        "toDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                    "required": ["test_id", "fromDateTime", "toDateTime"],
                },
            },
            {
                "name": "te_voice_results",
                "description": "RTP/voice server metrics (MOS, jitter, loss, latency) for one "
                               "ThousandEyes voice test over a UTC window.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "test_id": {"type": "string"},
                        "fromDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                        "toDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                    "required": ["test_id", "fromDateTime", "toDateTime"],
                },
            },
            {
                "name": "te_path_visualization",
                "description": "Hop-by-hop network path with per-hop latency/loss for one "
                               "ThousandEyes test over a UTC window.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "test_id": {"type": "string"},
                        "fromDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                        "toDateTime": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                    "required": ["test_id"],
                },
            },
        ],
    },
}


def stack_outputs(cf, stack: str) -> dict[str, str]:
    out = cf.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--region", required=True)
    args = ap.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    ac = boto3.client("bedrock-agentcore-control", region_name=args.region)
    o = stack_outputs(cf, args.stack)

    gw_name = f"{args.stack}-gw"

    # 1. Find an existing gateway by name, else create one. Idempotent so this
    #    script can be re-run safely after a partial failure.
    gw_id = None
    gw_url = None
    paginator_items = []
    try:
        resp = ac.list_gateways(maxResults=100)
        paginator_items = resp.get("items", resp.get("gateways", []))
    except Exception:
        pass
    for g in paginator_items:
        if g.get("name") == gw_name:
            gw_id = g.get("gatewayId")
            gw_url = g.get("gatewayUrl")
            print(f"reusing existing gateway: {gw_id}")
            break

    if gw_id is None:
        gw = ac.create_gateway(
            name=gw_name,
            roleArn=o["GatewayRoleArn"],
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "discoveryUrl": o["DiscoveryUrl"],
                    "allowedClients": [o["AppClientId"]],
                }
            },
            description="Neat x Graph correlation tools",
        )
        gw_id = gw["gatewayId"]
        gw_url = gw.get("gatewayUrl")
        print(f"gateway: {gw_id}  url: {gw_url}")

    # 1b. Wait for the gateway to leave CREATING/UPDATING before adding targets.
    print("waiting for gateway to become READY...", end="", flush=True)
    for _ in range(60):  # up to ~5 min
        g = ac.get_gateway(gatewayIdentifier=gw_id)
        status = g.get("status")
        if status == "READY":
            gw_url = g.get("gatewayUrl", gw_url)
            print(" READY")
            break
        if status in ("FAILED", "DELETING"):
            print(f"\ngateway entered {status}; aborting. Details: "
                  f"{g.get('statusReasons') or g.get('statusReason')}")
            return 1
        print(".", end="", flush=True)
        time.sleep(5)
    else:
        print("\ntimed out waiting for gateway READY; rerun the script later.")
        return 1

    # 2. Attach one Lambda target per tool group. Skip any that already exist.
    existing = set()
    try:
        for t in ac.list_gateway_targets(gatewayIdentifier=gw_id).get(
                "items", []):
            existing.add(t.get("name"))
    except Exception:
        pass

    for key, spec in TARGETS.items():
        tname = f"{args.stack}-{key}"
        if tname in existing:
            print(f"  target {key}: already exists, skipping")
            continue
        tgt = ac.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=tname,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": o[spec["fn_output"]],
                        "toolSchema": {"inlinePayload": spec["tools"]},
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        print(f"  target {key}: {tgt['targetId']} -> {tgt.get('status')}")

    print("\nMCP endpoint:", gw_url)
    print("Token URL   :", o["TokenUrl"])
    print("Client id   :", o["AppClientId"])
    print("Get a client_credentials token from the Token URL, then call the MCP "
          "endpoint with Authorization: Bearer <token>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())