# Admin Observability Console — Scope & Delivery Plan

**Date:** 2026-07-11 · **Status:** Scoped (not built) · **Branch target:** `agentcore-runtime-harness`

## Intent (from the user)

A **separate, admin-only interface** that surfaces operational health **across ALL
user sessions** — "output issues, agent crashes or hangs, general issues or
infrastructure failures." Explicitly **NOT** user input/output content. The UI must
be **consistent with the existing dark console UX** (`.ngb-*` design system: metric
tiles, eyebrow-dot labels, accent stripes, status pills, visx charts).

This is the productionised version of "bake in logging" — but scoped to **operational
metadata only**, viewable fleet-wide by an admin.

## Non-goals (privacy boundary)

- **No prompt text, no answer text, no chart data, no tool arguments** are stored or
  shown. The console reasons over *events about* turns, never *contents of* turns.
- Session identity is an **opaque hash** (`sha256(sessionId)[:12]`), not the raw id,
  so an admin can correlate a failure to "a session" without reading who/what.
- This surface is **not reachable by normal users** — a distinct authorization
  boundary from the user tier (see §Access).

## What we observe (signal inventory)

| Source | Emits | Already exists? |
|---|---|---|
| Runtime (`agent.py`) | per-invocation: `duration_ms`, `status` (ok/overflow/error), `tool_calls` count, `context_overflow` bool, `error_type`, `model_id`, token in/out counts | **new** — add structured emit |
| Target Lambdas (Neat/Graph/TE/Splunk) | per-call: `tool`, `duration_ms`, `upstream_status`, `rows_in`/`rows_returned`, `error_type` | **new** — add structured emit |
| Web-API job store (DynamoDB) | job `status` (pending/done/error) + timestamps | exists (sanitise error → `error_type`) |
| CloudWatch | Lambda errors/throttles/duration, runtime crash/OOM, timeouts | exists |

**Key metrics** (the ones that would have made today's bug a one-glance diagnosis):
invocations, error rate, p95/p99 latency, **timeout count**, **context-overflow count**,
per-tool latency + error rate, upstream (Neat/Graph/TE/Splunk) availability.

## Architecture

```
Runtime + target Lambdas  --EMF/structured logs-->  CloudWatch Logs + Metrics
                                                          |
                          Admin API Lambda  <-- Logs Insights / GetMetricData + jobs table
                                                          |
                    /admin  (Next.js, admin-gated)  <-- dark UX, visx charts, auto-refresh
                                                          |
                          CloudWatch Alarms --> SNS (email/Teams) on spikes
```

- **Emit layer:** structured JSON (or CloudWatch EMF) with operational fields only.
  A tiny shared `telemetry.emit(event)` helper in `_shared` for the Lambdas; the
  runtime logs one structured line per `answer()` (wrap in `agent.py`).
- **Admin API:** one read-only Lambda behind API Gateway (`GET /admin/health`,
  `GET /admin/failures?since=`, `GET /admin/tools`) that aggregates CloudWatch
  metrics + the jobs table into rollups. Returns metadata only.
- **Admin UI:** a new `/admin` route in the existing Next.js app (or a sibling
  static export), reusing the `.ngb-*` system — metric tiles for the headline
  numbers, a component status board (Runtime/Gateway/Neat/Graph/TE/Splunk pills:
  READY/DEGRADED/DOWN), a recent-failures table (`ts · session-hash · component ·
  error_type` — no content), and visx timeseries (error rate, latency, overflow).

## Access (distinct authz boundary)

- Gate on a **Cognito admin group** now (`admins`), swappable for an **Entra App
  Role** once SSO lands (ties into the deferred Entra work). The API Gateway JWT
  authorizer must require the admin claim; the `/admin` routes 403 without it.
- Separate API path + separate authorizer scope from the user `/chat` API so a normal
  user token can never reach fleet-wide data.

## UX consistency

Same fonts (Inter Tight + JetBrains Mono), same tokens/shading, same components
(eyebrow-dots, metric tiles, accent stripes, READY/DEGRADED/DOWN pills, visx charts)
as the user console. It should read as the "operations bay" of the same product, not a
bolt-on. AgentMark logo reused; nav gains an admin-only "Ops" entry (hidden for
non-admins).

## Delivery plan (phased, each independently shippable)

- **Phase 0 — Telemetry emission.** Add `_shared/telemetry.py` + structured emit in
  the runtime and every target Lambda; publish EMF metrics (overflow, timeout,
  latency, tool errors). No UI. *Deliverable: metrics visible in CloudWatch.*
- **Phase 1 — Admin API + authz.** Admin-group gate; read-only aggregation Lambda
  (`/admin/health`, `/admin/failures`, `/admin/tools`). *Deliverable: JSON health
  rollups, admin-only.*
- **Phase 2 — Admin console UI.** `/admin` dark surface, metric tiles + status board
  + failures table + visx charts, auto-refresh. *Deliverable: the interface.*
- **Phase 3 — Alarms.** CloudWatch Alarms → SNS (email/Teams webhook) on error-rate /
  overflow / timeout thresholds. *Deliverable: proactive paging.*

## Open decisions for the user

1. Admin console as a **route in the existing app** vs a **separate static-export app**
   (recommend: separate export at `/admin` — cleaner authz + deploy isolation).
2. Alarm delivery channel (email vs Teams webhook vs both).
3. Retention window for the failures view (recommend 30 days via CloudWatch).
