#!/usr/bin/env python3
"""Provision the AgentCore Runtime harness' AWS-side resources (idempotent).

Mirrors scripts/deploy_gateway.py conventions: reads SAM stack outputs, then
uses boto3 to create/reuse the gateway-client secret, an AgentCore Memory
resource, and a runtime execution role. Container build + runtime creation is
done by the `agentcore` starter-toolkit CLI using the emitted agent/.env.runtime.

  python scripts/deploy_runtime.py --stack neat-graph-bedrock --region us-east-1 \
      --mcp-url https://...gateway.../mcp

Requires: pip install -r scripts/requirements-deploy.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

SECRET_NAME = "neat-graph-bedrock/gateway-client"
MEMORY_NAME = "neatGraphBedrockSessionMemory"


def stack_outputs(cf, stack: str) -> dict[str, str]:
    out = cf.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def ensure_gateway_client_secret(sm, cognito, user_pool_id, client_id) -> str:
    desc = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id, ClientId=client_id)["UserPoolClient"]
    client_secret = desc.get("ClientSecret")
    if not client_secret:
        sys.exit("app client has no secret; regenerate the Cognito app client "
                 "with a secret enabled.")
    payload = json.dumps({"client_id": client_id, "client_secret": client_secret})
    try:
        sm.create_secret(Name=SECRET_NAME, SecretString=payload)
        print(f"created secret {SECRET_NAME}")
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=SECRET_NAME, SecretString=payload)
        print(f"updated secret {SECRET_NAME}")
    return sm.describe_secret(SecretId=SECRET_NAME)["ARN"]


def ensure_memory(region) -> str:
    from bedrock_agentcore.memory import MemoryClient
    mc = MemoryClient(region_name=region)
    for m in mc.list_memories():
        if m.get("name") == MEMORY_NAME or str(m.get("id", "")).startswith(MEMORY_NAME):
            mid = m.get("id") or m.get("memoryId")
            print(f"reusing memory {mid}")
            return mid
    created = mc.create_memory_and_wait(
        name=MEMORY_NAME,
        description="Short-term per-session conversation memory for the "
                    "neat-graph-bedrock runtime agent.",
        strategies=[],                 # short-term event store only
        event_expiry_days=7,
    )
    mid = created.get("id") or created.get("memoryId")
    print(f"created memory {mid}")
    return mid


def ensure_exec_role(iam, region, account_id, secret_arn, memory_id) -> str:
    role_name = "neat-graph-bedrock-runtime-exec"
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": secret_arn},
            {"Effect": "Allow", "Action": [
                "bedrock:Converse", "bedrock:ConverseStream",
                "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                "bedrock:ListInferenceProfiles"], "Resource": "*"},
            {"Effect": "Allow", "Action": [
                "bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetMemory", "bedrock-agentcore:RetrieveMemoryRecords"],
             "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/{memory_id}"},
            {"Effect": "Allow", "Action": [
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"},
            # Runtime pulls its container image from the toolkit-created ECR repo.
            {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"],
             "Resource": "*"},
            {"Effect": "Allow", "Action": [
                "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability"],
             "Resource": f"arn:aws:ecr:{region}:{account_id}:repository/bedrock-agentcore-*"},
        ],
    }
    try:
        iam.create_role(RoleName=role_name,
                        AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Execution role for neat-graph-bedrock AgentCore Runtime")
        print(f"created role {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=role_name,
                                      PolicyDocument=json.dumps(trust))
        print(f"reusing role {role_name}")
    iam.put_role_policy(RoleName=role_name, PolicyName="runtime-inline",
                        PolicyDocument=json.dumps(policy))
    return iam.get_role(RoleName=role_name)["Role"]["Arn"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--mcp-url", required=True, help="gateway MCP URL")
    args = ap.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    sm = boto3.client("secretsmanager", region_name=args.region)
    cognito = boto3.client("cognito-idp", region_name=args.region)
    iam = boto3.client("iam")
    account_id = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]

    o = stack_outputs(cf, args.stack)
    secret_arn = ensure_gateway_client_secret(
        sm, cognito, o["UserPoolId"], o["AppClientId"])
    memory_id = ensure_memory(args.region)
    role_arn = ensure_exec_role(iam, args.region, account_id, secret_arn, memory_id)

    env_lines = [
        f"MCP_URL={args.mcp_url}",
        f"TOKEN_URL={o['TokenUrl']}",
        "OAUTH_SCOPE=neat-graph-bedrock-api/invoke",
        f"AWS_REGION={args.region}",
        f"GATEWAY_CLIENT_SECRET_ARN={secret_arn}",
        f"MEMORY_ID={memory_id}",
    ]
    env_path = Path(__file__).resolve().parent.parent / "agent" / ".env.runtime"
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"\nwrote {env_path}")

    print("\nExecution role ARN:", role_arn)
    print("\nNext: build + launch the container with the starter toolkit:")
    print("  agentcore configure --entrypoint agent/agent.py \\")
    print(f"      --execution-role {role_arn} \\")
    print("      --env-file agent/.env.runtime --region", args.region)
    print("  agentcore launch")
    print("\nThen invoke:")
    print('  python scripts/invoke_agent.py "List the Neat rooms." --session demo-1')
    return 0


if __name__ == "__main__":
    sys.exit(main())
