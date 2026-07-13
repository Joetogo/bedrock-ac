# Cisco extension: Webex + ThousandEyes as AgentCore tools

**Date:** 2026-07-01
**Status:** Approved design, ready for implementation planning
**Scope:** Extend the existing Neat × MS Graph Bedrock AgentCore POC with two new Cisco data sources — Cisco Webex and Cisco ThousandEyes — surfaced as additional MCP tools on the same agent.

## Context

The existing POC ([README.md](../../../README.md)) is a natural-language analytics agent: Bedrock Converse tool-use loop → AgentCore Gateway (which *is* the MCP server, Cognito JWT inbound auth) → Lambda targets → upstream REST APIs. Secrets are injected server-side, so the model never sees upstream credentials. Today it correlates **Neat** room telemetry (CO₂, temp, humidity, occupancy) with **MS Graph** Teams call quality (jitter, loss, RTT).

This extension adds two axes to that story so the agent can reason across **room conditions ↔ meeting/call quality ↔ network-path health**:

- **Cisco Webex** — meeting media quality, device/workspace presence, and workspace environment sensors.
- **Cisco ThousandEyes** — voice/RTP results, network results, path visualization, and tests/alerts inventory.

## Goals

- One unified agent: Webex + ThousandEyes tools sit alongside Neat/Graph on the same Gateway.
- Consistency with the existing one-Lambda-per-source pattern (Approach A).
- Robust cross-source correlation via an **explicit location map**, replacing the current name-substring heuristic.
- Honest degradation: missing ids, absent Pro Pack, or missing permissions return clean `tool_err`/`null`, never hard failures.

## Non-goals

- Federating an external MCP server (decided against for now; see Alternatives).
- Webex Calling CDR (deliberately excluded).
- Live/streaming monitoring — this remains near-real-time reporting (Graph call records already lag ~30 min).

## Architecture

Unchanged shape: Bedrock agent → AgentCore Gateway → Lambda targets. Two new source Lambdas, two new Gateway targets, one evolved `correlate` Lambda, one new mapping config.

```
src/
  _shared/clients.py        MODIFY: webex_get() + refresh-token cache, te_get(), load_location_map()
  webex/                    NEW Lambda (mirrors neat_sense/)
    handler.py
    requirements.txt
  thousandeyes/             NEW Lambda (mirrors graph_calls/)
    handler.py
    requirements.txt
  correlate/handler.py      EVOLVE: map-driven multi-source join
config/
  locations.json            NEW: explicit site→ids mapping
infra/template.yaml         MODIFY: 2 Lambdas, 2 secrets, IAM, env, outputs
scripts/deploy_gateway.py   MODIFY: 2 new TARGETS entries (webex, thousandeyes)
tests/                      NEW/MODIFY: test_webex, test_thousandeyes, extend test_correlate
README.md                   MODIFY: tools table + prerequisites
```

Each source Lambda owns its own secret, so a leak of one credential cannot reach another source.

## Webex tools (`src/webex/handler.py`)

**Auth:** Webex **service-app OAuth** token. Secret `neat-graph-bedrock/webex` = `{client_id, client_secret, refresh_token}`. Access tokens expire (~14h), so `webex_get()` performs a **refresh-token grant** and caches the access token (same shape as `graph_token()` but refresh-based). Admin scopes required for analytics/devices; **Meeting Qualities requires Control Hub Pro Pack**.

| Tool | Input | Returns |
|------|-------|---------|
| `webex_list_meetings` | `fromDateTime`, `toDateTime` | meetings in window + `meetingId`s (enumeration helper) |
| `webex_meeting_quality` | `meetingId` | per-participant jitter / packet loss / latency / resolution |
| `webex_device_presence` | *(none)* | room devices/workspaces + status + in-call/active presence |
| `webex_workspace_environment` | `workspace_id`, optional `fromDateTime`/`toDateTime` | temperature / humidity / air quality / ambient sound |

If the tenant lacks Pro Pack, `webex_meeting_quality` returns `tool_err("meeting quality requires Webex Pro Pack")`.

## ThousandEyes tools (`src/thousandeyes/handler.py`)

**Auth:** ThousandEyes **API bearer token** (long-lived, no refresh). Secret `neat-graph-bedrock/thousandeyes` = `{bearer_token}`. Base `https://api.thousandeyes.com/v7`. Requires the `API Access` permission. Note the org-wide rate limit (240 req/min).

| Tool | Input | Returns |
|------|-------|---------|
| `te_list_tests_alerts` | optional `fromDateTime`/`toDateTime` | configured tests/agents + active alerts in window |
| `te_network_results` | `test_id`, `fromDateTime`, `toDateTime` | loss / latency / jitter per agent |
| `te_voice_results` | `test_id`, `fromDateTime`, `toDateTime` | MOS / jitter / loss / latency (wraps RTP-stream **test-results** endpoint) |
| `te_path_visualization` | `test_id`, optional window | hop-by-hop path with per-hop latency/loss |

ThousandEyes metrics are **site/agent-level**, not per-room; they join at the site tier of the location map.

## Location map (`config/locations.json`)

Site-keyed, rooms nested, ThousandEyes at the site tier. Any source id may be omitted.

```json
{
  "sites": [
    {
      "site": "sydney-hq",
      "thousandeyes": { "network_test_id": "12345", "voice_test_id": "12346" },
      "rooms": [
        {
          "room": "Sydney L5 Boardroom",
          "neat_space_id": "sp_abc",
          "webex_workspace_id": "Y2lzY29z...",
          "teams_room_upn": "boardroom-l5@contoso.com"
        }
      ]
    }
  ]
}
```

Bundled into the `correlate` Lambda; read via `LOCATIONS_PATH` env var so it can later move to S3/DynamoDB without a code change. `load_location_map()` lives in `_shared/clients.py`.

## `correlate` evolution

Signature: `correlate(site?, room?, fromDateTime, toDateTime)` — supply either `site` (whole building) or `room` (one space).

1. Load the map, resolve the site/room entry to its set of source ids.
2. Fan out **server-side** via the shared clients: Neat sensors, Webex environment + meeting quality, Graph call records/quality, ThousandEyes network + voice results (+ path-vis on request). Each call is guarded — a missing id, absent Pro Pack, or missing permission degrades to `null`, never a hard failure.
3. Return one joined object:
   `{ site, room, window, environment, meeting_quality[], call_quality[], network: {loss, latency, jitter, mos}, path_summary }`
   plus the standing **"observational, not causal"** note.

**Known subtlety (recorded honestly):** associating a specific meeting/call to a room is identity-based via the room's `webex_workspace_id` / `teams_room_upn` in the map. The map removes name-substring guesswork, but a meeting only ties to a room if it was hosted on that room's device/account.

## Infra (`infra/template.yaml`)

- **2 secrets** (placeholder values, filled post-deploy like the existing two).
- **2 Lambdas** `WebexFn`, `ThousandEyesFn` on the existing shared layer; env `WEBEX_SECRET_ARN` / `THOUSANDEYES_SECRET_ARN`; IAM `secretsmanager:GetSecretValue` scoped to their own secret.
- **`correlate` Lambda** gains env `LOCATIONS_PATH`, `WEBEX_SECRET_ARN`, `THOUSANDEYES_SECRET_ARN`, and read access to **all four** secrets (it fans out server-side).
- **New outputs** `WebexFnArn`, `ThousandEyesFnArn`.

## Deploy wiring (`scripts/deploy_gateway.py`)

Add two entries to the `TARGETS` dict — `webex` → `WebexFnArn` and `thousandeyes` → `ThousandEyesFnArn` — each with inline tool schemas for the new tools. No structural change to the deploy script.

## Testing (`tests/`, fully mocked)

- `test_webex.py` — mock `webex_get`; assert the 4 tool shapes + the refresh-token cache path.
- `test_thousandeyes.py` — mock `te_get`; assert network/voice/path/list shapes.
- `test_correlate.py` — extend with a `locations.json` fixture; mock all four sources; assert the joined shape **and** graceful degradation when a source returns `null`.

## Alternatives considered

- **Federate an existing MCP server.** ThousandEyes ships an official remote MCP server (`https://api.thousandeyes.com/mcp`, OAuth bearer). Rejected for now: breaks the single-Gateway / server-side credential-injection model and prevents the `correlate` Lambda from reaching TE data server-side. Legitimate future option for the ThousandEyes axis only. (Webex has no comparable production MCP server, so it is Lambda-wrapped regardless.)
- **One combined `cisco` Lambda / per-capability Lambdas.** Rejected: mixes secrets/upstreams or over-fragments for a POC.

## Success criteria

- `make deploy` + `make gateway` provision the two new targets; all 8 new tools (4 Webex + 4 ThousandEyes) show up in the agent's tool list.
- The agent can answer a cross-source question, e.g. "Which Sydney rooms had high CO₂ this afternoon, did their Webex meeting quality drop, and did ThousandEyes show packet loss on the site's voice test in the same window?"
- `make test` passes with the new and extended tests, including graceful-degradation cases.
