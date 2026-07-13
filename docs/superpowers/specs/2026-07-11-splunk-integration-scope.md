# Splunk Integration — Scope & Fit

**Date:** 2026-07-11 · **Status:** Scoped (not built) · **Reference:** AWS APN blog
"Unlocking the Power of Splunk with Amazon Bedrock" (agentic Splunk assistant).

## The reference architecture (what the blog does)

`Streamlit → Bedrock LLM (Claude 3.5 Sonnet) → Bedrock Agent → Action Group → Lambda →
Splunk`, with **AOSS** (OpenSearch Serverless vector DB) and **Secrets Manager**. The
agent runs a **progressive schema-discovery → execute** loop over five read-only tools:

| Tool | Role |
|---|---|
| `search_aws_sourcetypes(query)` | Vector search in **AOSS** to pick the right Splunk sourcetype for a NL query (narrows SPL scope). AOSS indexes "Splunk sourcetype mappings for all AWS data sources" (CloudTrail, VPC FlowLogs, CloudWatch…). |
| `get_splunk_fields(sourcetype)` | Field/schema for a sourcetype → valid SPL fields. |
| `get_splunk_lookups(sourcetype)` / `get_splunk_lookup_values(lookup)` | Discover lookups + values; used when a SPL query returns nothing. |
| `get_splunk_results(spl)` | Execute the generated SPL, format results back to the LLM. |

Splunk creds live in Secrets Manager; the solution is **read-only** (queries existing
logs, no writes). The blog states no explicit guardrails/limits.

## How it maps onto OUR build (the important part)

Our stack is **not** Bedrock Agents + Streamlit — it is **AgentCore Runtime (Strands
agent) → AgentCore Gateway (MCP) → Lambda targets**, with a Next.js console. So we adopt
the *concept*, not the wiring:

| Blog component | Our equivalent |
|---|---|
| Streamlit | existing **Next.js dark console** (no change) |
| Bedrock Agent + Action Group | existing **Strands agent + Gateway MCP** (no change) |
| Action-Group Lambda | **new `src/splunk/handler.py` Lambda target** exposed as MCP tools |
| Secrets Manager | same pattern — new secret `neat-graph-bedrock/splunk` (server-side only) |
| AOSS vector DB | **optional Phase 2** — OpenSearch Serverless collection for sourcetype discovery |

**Splunk becomes the 4th read-only Lambda target**, siblings to Neat / Graph /
ThousandEyes. Tools (MCP names): `splunk_search_sourcetypes`, `splunk_get_fields`,
`splunk_get_lookups`, `splunk_get_lookup_values`, `splunk_run_search`.

### Why it fits well
- **Same target pattern** — one handler, `_shared.clients` gets a `splunk_get()` +
  `splunk_search()` alongside `neat_get`/`graph_get`/`te_get`; register in the Gateway
  exactly like the others (`deploy_gateway.py`).
- **Read-only by construction** — matches our standing "we only read" constraint;
  secret referenced by ARN from Secrets Manager, never in env/logs/browser.
- **Payload bounding is already solved** — Splunk searches can return huge result sets,
  the *same* overflow risk we just fixed. **Reuse `downsample_series`** on
  `splunk_run_search` output. Direct synergy with today's work.
- **Correlation upside** — Splunk logs + Neat rooms + Teams calls + ThousandEyes paths
  in one agent: "did the VPN flap in Splunk correlate with the Teams MOS dip?" This
  deepens the product thesis rather than bolting on a separate assistant.

## Guardrails WE add (the blog omits these; we must not)

`splunk_run_search` must **enforce read-only SPL** — the single biggest risk:
- **Command allowlist / denylist**: reject `| delete`, `| outputlookup`, `| collect`,
  `| sendemail`, `| script`, `| makeresults ... | outputlookup`, external commands.
- **Mandatory time bound** (`earliest`/`latest`) and a **max scan window**.
- **Result cap + `downsample_series`** on the returned events.
- **Search-job timeout** so a heavy SPL can't hang the Lambda (mirror the 120s lesson).
- A **read-only Splunk role/token** scoped to the allowed indexes (defence in depth
  even if a bad command slips the allowlist).

## Connectivity / prerequisites (decision needed)

- **Where is Splunk?** Splunk Cloud (public REST endpoint) vs on-prem/private. If
  private, the Splunk target Lambda needs **VPC attachment + a network path**
  (VPN / Direct Connect / PrivateLink) — an infra prerequisite and the main unknown.
- **Auth method:** Splunk REST API **Bearer token** (recommended) or session key;
  stored in `neat-graph-bedrock/splunk` = `{host, token}` (or `{host, user, password}`).
- **Index/sourcetype scope:** which indexes the read-only role may search.
- **AOSS now or later:** MVP can skip AOSS and use Splunk's own metadata REST
  (`| metadata type=sourcetypes`, `| rest /services/...`) or a curated sourcetype map;
  add AOSS + Bedrock Titan embeddings when the sourcetype space is large enough to need
  semantic discovery.

## Delivery plan (phased)

- **Phase 0 — Connectivity spike.** Confirm Lambda→Splunk reachability + a read-only
  token; a one-off `splunk_get('/services/server/info')` smoke. *Unblocks everything.*
- **Phase 1 — Splunk target (MVP, no AOSS).** `src/splunk/handler.py` with the five
  read-only tools; sourcetype discovery via Splunk metadata REST or a curated map;
  SPL guardrails + `downsample_series`; secret; register in Gateway; local `answer()`
  smoke. *Deliverable: agent can discover schema + run bounded SPL.*
- **Phase 2 — AOSS semantic discovery.** OpenSearch Serverless collection seeded with
  sourcetype/field metadata + embeddings; `splunk_search_sourcetypes` upgraded to
  vector search. *Deliverable: robust sourcetype selection at scale.*
- **Phase 3 — Prompt + E2E + correlation.** Extend `SYSTEM_PROMPT` with the
  discover→fields→lookups→SPL→run→analyse flow; E2E; a cross-source correlation demo
  (Splunk × Teams/ThousandEyes). *Deliverable: shipped, correlating assistant.*

## Open decisions for the user

1. Splunk **Cloud or on-prem/private** (drives the VPC/connectivity work in Phase 0).
2. **Auth**: REST Bearer token vs session key; the read-only role + allowed indexes.
3. **AOSS in scope now** or defer to Phase 2 (recommend defer; MVP via metadata REST).
