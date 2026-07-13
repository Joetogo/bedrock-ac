# AgentCore Runtime Harness — Design

**Date:** 2026-07-08
**Status:** Approved (design), pending implementation plan
**Depends on:** the deployed AgentCore Gateway MCP (Neat + Graph + ThousandEyes + correlate, read-only) and Cognito app client, us-east-1.

## Goal

Let the operator query the deployed correlation stack in plain English **without running local scripts**. Move the existing `scripts/ask.py` Bedrock Converse tool-use loop off the laptop and host it as an **Amazon Bedrock AgentCore Runtime** agent, built with the **Strands Agents SDK**, connected to the existing AgentCore Gateway MCP, with **AgentCore Memory** for multi-turn recall.

## Non-goals

- No web UI, HTTP API, or chat-bot surface (console + CLI only for now).
- No new tools; the agent consumes only the existing gateway tools.
- No write access to Neat / Graph / ThousandEyes — read-only is preserved by construction.
- Not replacing `ask.py` (it remains a useful local harness).

## Architecture

```
Operator (CLI: `agentcore invoke` / aws bedrock-agentcore invoke-agent-runtime)
   │  {prompt, sessionId}   ← plain English
   ▼
AgentCore Runtime  (hosts the containerized Strands agent)
   ├── Strands Agent → Bedrock (auto-selected Claude inference profile)
   ├── AgentCore Memory  ← recalls prior turns keyed by sessionId
   └── MCPClient ──► existing AgentCore Gateway MCP  (Cognito JWT, streamable HTTP)
                        └── neat_* / graph_* / te_* / correlate_*   (all GET / read-only)
```

## Components

### `agent/agent.py`
The hosted agent, one clear purpose: answer a question by running the Strands tool-use loop over the gateway tools with session memory.

- Uses `BedrockAgentCoreApp` with an `@app.entrypoint` handler receiving `{"prompt": str, "sessionId": str}`.
- Mints a Cognito **client-credentials** token (same logic as `ask.py::get_token`) to authorize to the gateway.
- Opens a Strands `MCPClient` over streamable HTTP at the gateway MCP URL with `Authorization: Bearer <token>`.
- Loads the gateway tools via MCP and hands them to a Strands `Agent`.
- Attaches **AgentCore Memory**, scoped by `sessionId`, so follow-up turns recall context.
- System prompt reused from `ask.py`: answers about room conditions + Teams call quality + ThousandEyes; call records lag ~30 min; correlation is observational, not causal; be concise.
- Returns the answer text.

### `agent/requirements.txt`
`strands-agents`, `bedrock-agentcore`, `boto3`, `mcp`.

### `scripts/deploy_runtime.py`
boto3 script mirroring `scripts/deploy_gateway.py` (existing convention: SAM for Lambdas/Cognito, boto3 scripts for AgentCore resources). Idempotent. Responsibilities:
- Create the **AgentCore Memory** resource (if absent) and capture its id.
- Create/update the **AgentCore Runtime** agent: containerize `agent/` (CodeBuild → ECR), create the runtime with its execution role, wire env (gateway MCP URL, token URL, scope, region, memory id).
- Print the runtime id / ARN and an example `invoke` command.

### `scripts/invoke_agent.py`
Thin CLI convenience wrapper: `python scripts/invoke_agent.py "question" [--session S]` → calls `bedrock-agentcore invoke-agent-runtime`, prints the answer. Optional; raw `aws`/`agentcore` CLI also works.

## Auth & read-only guarantee

- **Operator → Runtime:** IAM / SigV4 (single operator). No new inbound auth to build.
- **Runtime → Gateway:** the agent fetches the Cognito token using a client id/secret read from a **Secrets Manager** entry `neat-graph-bedrock/gateway-client` (`{"client_id":..., "client_secret":...}`), *not* baked into the image or env. The runtime execution role gets `secretsmanager:GetSecretValue` on that secret.
- **Runtime → Bedrock:** granted by the runtime execution role (`bedrock:Converse`, `bedrock:InvokeModel`, `bedrock:ListInferenceProfiles`).
- **Read-only preserved by construction:** the agent can only reach the gateway's tools, all of which are GET-based. There is no write/delete path in scope.

## Data flow

1. Operator runs `agentcore invoke '{"prompt":"...","sessionId":"abc"}'`.
2. Runtime invokes the agent handler.
3. Handler retrieves prior turns for `sessionId` from Memory and builds the message list.
4. Strands `Agent` runs the tool-use loop against the gateway MCP tools, calling Bedrock.
5. The new turn (user + assistant) is written back to Memory.
6. The answer text is returned to the CLI.

## Error handling

- Cognito token / gateway connection failures are caught and returned as a clean agent error message (not a stack trace).
- A Memory read/write failure **degrades to stateless**: log and continue answering, so a memory hiccup never blocks a response.
- Model resolution reuses `ask.py::resolve_model` (auto-select newest Claude inference profile) with the same fallback error if none is available.

## Testing

- **Local pre-deploy:** run the Strands agent locally against the live gateway to confirm tool discovery + a Neat answer before containerizing.
- **Post-deploy (`agentcore invoke`):**
  1. Neat question — e.g. "List the Neat rooms." → returns the 6 rooms.
  2. Memory-dependent follow-up in the same session — "Now the CO₂ and people count for that first room." → resolves "that room" from prior turn.
  3. ThousandEyes question — "List a few ThousandEyes tests." → returns real tests.
- **Read-only assertion:** confirm the discovered tool set contains only `neat_* / graph_* / te_* / correlate_*` (no create/update/delete verbs).

## Region

us-east-1, matching the existing stack (gateway, Cognito, Lambdas, secrets).

## Repo layout (additions)

```
agent/agent.py              Strands agent + Runtime entrypoint
agent/requirements.txt
scripts/deploy_runtime.py   boto3: create Memory + Runtime + exec role
scripts/invoke_agent.py     thin CLI wrapper (optional)
infra/ (unchanged)          new gateway-client secret may be added here or created by deploy_runtime.py
```

## Open items for the implementation plan

- Confirm the Strands + AgentCore Runtime entrypoint contract (payload schema, streaming vs buffered response).
- Decide whether `neat-graph-bedrock/gateway-client` is created in the SAM template or by `deploy_runtime.py` (lean toward the script, to keep AgentCore-adjacent resources together).
- Confirm the AgentCore Memory API surface (event/message store vs summary) used for multi-turn recall.
