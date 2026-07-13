# Neat Sense × MS Graph callRecords — Bedrock AgentCore correlation POC

Natural-language analytics over Neat meeting-room telemetry and Microsoft Teams
call quality, orchestrated by **Amazon Bedrock** through an **AgentCore Gateway**.
The Gateway *is* the MCP server; three Lambda functions are exposed as MCP tools.

```
 NL query
   │  (Bedrock Converse tool-use loop — scripts/agent.py)
   ▼
 AgentCore Gateway  ── MCP endpoint, Cognito JWT inbound auth
   ├── target: neat       → Lambda → Neat Pulse / Sense API   (Bearer, Secrets Mgr)
   ├── target: graph      → Lambda → MS Graph callRecords      (client-credentials)
   └── target: correlate  → Lambda → joins room ↔ call quality
```

Gateway turns each Lambda into MCP tools and injects outbound credentials, so
the model never sees the Neat API key or the Graph client secret.

## Tools

| Tool | Source | Returns |
|------|--------|---------|
| `neat_list_rooms` | Neat Pulse | rooms/spaces + endpoint ids |
| `neat_room_sensors` | Neat Sense | CO₂, temp, humidity, people count, VOC over a window |
| `graph_list_call_records` | Graph | Teams call records started in a UTC window |
| `graph_call_quality` | Graph | per-session jitter / packet loss / RTT / codec |
| `correlate_room_calls` | both | room environment joined to its calls' quality |

## Prerequisites

1. **AWS**: account with Bedrock model access enabled (Claude on Bedrock) and
   AgentCore available in your region. SAM CLI installed.
2. **Neat**: a **paid Pulse plan** (Plus/Pro) — the API is disabled otherwise.
   In Pulse → Settings → API keys, create a **Read**-scope key. Grab the
   org id from Settings.
3. **Entra ID app registration** for Graph:
   - API permission `CallRecords.Read.All` (**Application**, not delegated —
     callRecords does not support delegated auth) with **admin consent granted**.
   - A client secret.

## Deploy

```bash
# 1. build + deploy the Lambdas, secrets, roles, Cognito pool
make deploy REGION=ap-southeast-2

# 2. fill the two secrets (values are placeholders in the template)
aws secretsmanager put-secret-value --secret-id neat-graph-bedrock/neat-pulse \
  --secret-string '{"org_id":"...","api_key":"..."}'
aws secretsmanager put-secret-value --secret-id neat-graph-bedrock/graph-app \
  --secret-string '{"tenant_id":"...","client_id":"...","client_secret":"..."}'

# 3. create the Gateway + Lambda targets (reads stack outputs)
make gateway REGION=ap-southeast-2
```

`make gateway` prints the **MCP endpoint**, **token URL**, and **client id**.

## Ask it something

```bash
pip install -r scripts/requirements-agent.txt
python scripts/agent.py \
  --mcp-url   "<gateway url>" \
  --token-url "<token url>" \
  --client-id "<client id>" --client-secret "<cognito client secret>" \
  --region ap-southeast-2 \
  "Which rooms were over 1000ppm CO2 this afternoon, and did their Teams calls show higher jitter?"
```

## Constraints worth knowing (these shaped the design)

- **Call records lag ~30 min** after a call ends. This is near-real-time
  reporting, not live monitoring. For live, subscribe to callRecord change
  notifications instead.
- **Graph callRecords is org-wide and app-only.** `CallRecords.Read.All` is a
  privileged, tenant-level grant — treat the Graph secret accordingly.
- **Room ↔ call matching is name-substring based in the POC** (`correlate`).
  For production, build an explicit map from Teams Rooms UPNs/resource accounts
  to Neat space ids rather than string-matching.
- **Correlation ≠ causation.** The agent is instructed to report associations
  observationally.
- **Neat region.** API base is `api.pulse.neat.no` (Norway). Latency from
  ap-southeast-2 is fine for this read workload.

## Tests

```bash
make test    # mocks both upstreams; validates handler shapes + the join
```

## Files

```
infra/template.yaml         SAM: Lambdas, layer, secrets, roles, Cognito
scripts/deploy_gateway.py   creates AgentCore Gateway + 3 Lambda targets
scripts/agent.py            Bedrock Converse loop over the MCP tools
scripts/build_layer.sh      assembles the shared layer
src/_shared/clients.py      Graph token, Neat client, secrets, response shaping
src/neat_sense/             Neat tools
src/graph_calls/            Graph tools
src/correlate/              correlation tool
tests/                      pytest, fully mocked
```
