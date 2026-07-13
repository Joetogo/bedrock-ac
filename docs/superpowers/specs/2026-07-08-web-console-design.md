---
title: neat-graph-bedrock Web Console — Phase 1 Design
date: 2026-07-08
status: approved
supersedes: none
related:
  - 2026-07-08-agentcore-runtime-harness-design.md
---

# neat-graph-bedrock Web Console — Phase 1 Design

## Goal

A hosted web UI (AWS) that lets authorized users query the neat-graph-bedrock
deployment in plain English — a browser-based front end for the existing
AgentCore Runtime harness — so no one has to run `scripts/invoke_agent.py`
locally. Phase 1 ships an authenticated chat console with saved per-user
history. Enterprise identity (Entra ID / Microsoft Graph group-gating) is
explicitly deferred to later phases.

## Non-Goals (Phase 1)

- Entra ID / Azure AD federation (Phase 2).
- Microsoft Graph group-based authorization (Phase 3).
- Streaming/token-by-token responses (Phase 4).
- Any change to the agent's read-only behavior against Neat / ThousandEyes /
  Graph, or to the AgentCore Gateway path.
- Multi-region deploy — built in us-east-1 alongside the live runtime; the
  ap-southeast-2 migration moves this stack with the runtime later.

## Context

The AgentCore Runtime harness is built, hosted, and E2E-validated live in
us-east-1 (runtime `<runtime-id>`). It is invoked today
via `scripts/invoke_agent.py`, which signs a `bedrock-agentcore
invoke_agent_runtime` call with SigV4/IAM and passes
`{"prompt","sessionId"}` → `{"answer"}` (buffered). The runtime supports
multi-turn memory keyed on `sessionId` (AgentCore Memory), and
`runtimeSessionId` must be **≥33 characters**.

The existing SAM stack (`infra/template.yaml`) already defines a Cognito user
pool whose app client uses the **client_credentials** flow — machine auth for
the Gateway's inbound JWT. A human-facing login needs a **separate**
authorization-code app client + hosted UI on that same pool.

## Architecture

```
Browser SPA (Next.js static export)      static assets in S3 behind CloudFront (OAC, HTTPS)
   │  1. Cognito Hosted UI login (authorization code + PKCE) → JWT
   │  2. fetch() with  Authorization: Bearer <id token>
   ▼
API Gateway (HTTP API)  ── Cognito JWT authorizer (validate token, extract `sub`)
   ▼
Lambda ("web tier", Python)
   ├─ POST   /chat                  invoke runtime (SigV4) → {answer}; persist the turn
   ├─ GET    /conversations         list this user's threads
   ├─ GET    /conversations/{id}    messages in a thread
   └─ DELETE /conversations/{id}    delete a thread
   ▼
DynamoDB (per-user chat history)         Bedrock AgentCore Runtime (existing, unchanged)
```

The browser never holds AWS credentials or upstream secrets. The **only**
component that calls `invoke_agent_runtime` is the Lambda, via its execution
role (SigV4) — the same trust model as `invoke_agent.py` today.

## Key Design Decisions

1. **`conversationId` is the runtime `sessionId`.** A new thread mints a
   36-character UUID; it doubles as the runtime `runtimeSessionId` (satisfies
   the ≥33-char rule). One key ties the saved thread and the AgentCore Memory
   session together, so in-thread follow-ups ("and its call quality?") recall
   context automatically.

2. **Reuse the invoke logic.** Extract `build_payload` / `parse_response` and
   the `invoke_agent_runtime` call out of `scripts/invoke_agent.py` into a
   small shared `runtime_client.py` packaged with the Lambda; the CLI imports
   the same module. One source of truth for the runtime contract.

3. **Reuse the Cognito pool, add a client.** Add a **second** app client
   (authorization-code + PKCE, hosted UI, callback/logout URLs) and a
   hosted-UI domain to the existing pool. The gateway's client_credentials
   client is untouched.

4. **Buffered, not streaming.** Matches the runtime entrypoint and the API
   Gateway 29s integration timeout; observed answers are <20s. Streaming is
   Phase 4.

5. **Next.js as a static export.** The frontend is Next.js (App Router) built
   with `output: 'export'` — all client components, no SSR/server components/
   Next API routes — so it ships as static files to S3 + CloudFront, keeping
   the serverless SAM hosting decision. Next is used here for its React DX and
   ecosystem, not its server tier (that stays the API Gateway + Lambda web
   tier). Frontend stack: **Next.js + React + Tailwind CSS + Framer Motion +
   lucide-react**.

## Read-Only Guarantee (standing constraint)

The agent remains read-only against Neat / ThousandEyes / Graph. The web
tier's **only** writes are to its own per-user DynamoDB chat-history table —
never to any upstream system, and it never sees upstream creds. Every API
route sits behind the Cognito JWT authorizer, and history is partitioned by
the token's `sub` claim so a user can only read/write their own threads.

## Components / New Files

- `webapp/frontend/` — Next.js (App Router, `output: 'export'`) SPA, styled
  with Tailwind CSS, animated with Framer Motion, icons from lucide-react:
  - Cognito Hosted UI login redirect + token handling (PKCE).
  - Chat thread view (message stack, input box, send).
  - Conversations sidebar (list, open, delete, "new chat").
  - Error toasts; loading state while a `/chat` call is in flight.
  - Motion, used sparingly: message enter transitions, sidebar open/close,
    send-button feedback (respecting `prefers-reduced-motion`).
- `webapp/api/`
  - `handler.py` — route dispatch for the four endpoints; reads `sub` from the
    authorizer claims; maps exceptions to HTTP status.
  - `runtime_client.py` — shared `build_payload` / `parse_response` /
    `invoke(prompt, session, region)`; imported by the Lambda and by the CLI.
  - `store.py` — DynamoDB access (put message, list threads, get thread
    messages, delete thread, upsert thread metadata/title).
- `webapp/infra/template.yaml` — new SAM stack:
  - S3 bucket (private) + CloudFront distribution with Origin Access Control.
  - HTTP API + Cognito JWT authorizer.
  - Lambda (Python) + its execution role
    (`bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN; DynamoDB CRUD
    on the table; CloudWatch Logs).
  - DynamoDB table.
  - Cognito app client (authorization-code) + hosted-UI domain, referencing
    the existing pool id (stack parameter).
  - Outputs: CloudFront URL, API base URL, Cognito domain, app client id.
- Tests: `webapp/api/tests/` — pytest with `moto` for `store.py`, stubbed
  `bedrock-agentcore` for `handler.py`; reuse existing `build_payload` /
  `parse_response` tests. A manual E2E checklist in the README section.

## DynamoDB Data Model

Single table, partition key `PK = USER#<sub>`, sort key `SK`:

| Item type | PK | SK | Attributes |
|---|---|---|---|
| Thread metadata | `USER#<sub>` | `CONV#<id>` | `title`, `createdAt`, `updatedAt` |
| Message | `USER#<sub>` | `CONV#<id>#MSG#<ts>` | `role` (`user`/`assistant`), `text`, `ts` |

`<ts>` is an ISO-8601 UTC timestamp with millisecond precision and a trailing
`Z` (e.g. `2026-07-08T05:39:12.004Z`); this format is fixed-width and
lexicographically sortable, so `SK` range order equals chronological order.

- **Sidebar list:** query `PK = USER#<sub>` with `begins_with(SK,"CONV#")`,
  keep items whose `SK` has no `#MSG#` segment (metadata rows).
- **Open a thread:** query `PK = USER#<sub>` with
  `begins_with(SK,"CONV#<id>#MSG#")`, ordered by `SK` (chronological).
- **New turn:** put the user message and the assistant message, and upsert the
  thread metadata (`updatedAt`, and `title` from the first user message if
  absent).
- **Delete a thread:** query the thread's items and batch-delete them.

## Request / Response Contracts

- `POST /chat` — request `{ "prompt": str, "conversationId": str | null }`.
  If `conversationId` is null, the Lambda mints a new 36-char UUID. It calls
  the runtime with that id as `runtimeSessionId`, persists the user + assistant
  turn, and responds `{ "answer": str, "conversationId": str }`.
- `GET /conversations` — `{ "conversations": [ { "id", "title", "updatedAt" } ] }`,
  newest first.
- `GET /conversations/{id}` — `{ "id", "messages": [ { "role", "text", "ts" } ] }`.
- `DELETE /conversations/{id}` — `204` on success.

## Error Handling

- Missing/invalid/expired token → `401` from the JWT authorizer → SPA clears
  local tokens and redirects to the Cognito Hosted UI login.
- Runtime invoke error or timeout → Lambda catches, returns `502` with a
  human-readable `{"error": ...}` → SPA shows a toast, keeps the typed prompt.
- Agent-level error (the runtime returns `{"error"}` not `{"answer"}`) →
  surfaced to the UI as an assistant-side error bubble, not persisted as a
  normal answer.
- DynamoDB failure on persist → the answer is still returned to the user; the
  persist error is logged (answer delivery is not blocked by history write
  failure).

## Build Order (within Phase 1)

1. `runtime_client.py` refactor (extract from `invoke_agent.py`; keep CLI
   green).
2. SAM stack skeleton: Cognito app client + hosted UI domain, HTTP API + JWT
   authorizer, Lambda stub, DynamoDB table, S3 + CloudFront.
3. `store.py` + tests (moto).
4. `handler.py` `POST /chat` (invoke + persist) + tests.
5. `handler.py` conversation routes (list/get/delete) + tests.
6. Frontend (Next.js static export + Tailwind + Framer Motion + lucide-react):
   login flow → chat thread → sidebar → error toasts.
7. Deploy to us-east-1; manual E2E checklist; README "Web Console" section.

## Testing Strategy

- **Unit:** `store.py` against `moto` DynamoDB; `handler.py` with a stubbed
  `bedrock-agentcore` client and a synthetic authorizer-claims event; reuse the
  existing `build_payload` / `parse_response` tests via the shared module.
- **Manual E2E checklist:** log in through Hosted UI; ask a Neat question; ask
  an in-thread follow-up and confirm memory recall; reload and confirm the
  thread reappears from history; delete a thread; confirm a second Cognito user
  cannot see the first user's threads.

## Later Phases (out of scope here, recorded for continuity)

- **Phase 2:** federate the Cognito pool to Entra ID (OIDC/SAML) — no change to
  the SPA, API, or Lambda; login just gains an IdP button.
- **Phase 3:** group-gating via Microsoft Graph `/me/memberOf` (e.g. require a
  `NeatGraph-Operators` group) enforced in a pre-token-generation Lambda or the
  authorizer.
- **Phase 4:** streaming responses (runtime → API → SPA).
