# DemandIQ Assistant — Service Implementation

How the assistant is built, metered and governed on the server. Read
`ml/AGENT_DESIGN.md` first for what it is allowed to say; this document is how
the service enforces it.

## Position in the system

The assistant is a **read-only orchestration service**. It never writes to the
domain. It authenticates as the calling user, resolves a question into a
sequence of typed tool calls against existing endpoints, and returns a
structured answer. Anything that changes state is handed back to the UI as a
deep link into the screen where a human does it.

```
client → POST /agent/messages
       → quota check → session check → guardrail preflight
       → planner (LLM) → tool calls (parallel where independent)
       → composer (LLM) → guardrail postflight → persist turn → stream out
```

Both guardrail passes are non-optional and run outside the model.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/agent/sessions` | Open a session; returns id, limits, remaining quota |
| `GET` | `/agent/sessions` | List the caller's sessions (admins: workspace-wide) |
| `DELETE` | `/agent/sessions/{id}` | End a session; discards its context |
| `POST` | `/agent/messages` | Ask a question; streams the answer |
| `GET` | `/agent/messages?session_id=` | Replay a transcript |
| `POST` | `/agent/messages/{id}/pin` | Save an answer to the caller's workspace |
| `GET` | `/agent/insights` | Proactive cards for the caller's scope |
| `GET` | `/agent/quota` | Remaining allowance for the caller |
| `GET` | `/admin/agent/quotas` | Seat allowances and consumption |
| `PATCH` | `/admin/agent/quotas/{user_id}` | Change tier or set a daily override |
| `POST` | `/admin/agent/grants` | Issue a temporary top-up with an expiry |
| `GET` | `/admin/agent/sessions` | All live sessions in the workspace |
| `DELETE` | `/admin/agent/sessions/{id}` | Force-end any session |
| `GET`/`PATCH` | `/admin/agent/guardrails` | Read and toggle unlockable rules |
| `GET` | `/admin/agent/usage` | Daily question volume, tool calls, refusals, latency |

## Quota model

Quota is counted in **questions**, not tokens. Operators cannot reason about
tokens, and a question is the unit an administrator actually wants to cap.

```
effective_daily_limit = override ?? tier.daily
              + sum(active_grants.extra)
```

Checked before the planner runs, so a rejected question costs nothing:

| Check | Failure |
| --- | --- |
| Daily allowance | `429 quota_exhausted_daily` |
| Monthly allowance | `429 quota_exhausted_monthly` |
| Burst rate (per 60s) | `429 rate_limited`, with `Retry-After` |
| Concurrent sessions | `409 session_limit_reached` |
| Tool not in tier | tool silently omitted from the plan, never an error |

A question that returns a refusal (no data to support an answer) **is** counted
— it consumed a model call. A question rejected by a quota check is not, and
neither is one declined by the guardrail preflight: nothing was planned or
composed, so charging the operator for our own decline would be billing them for
the refusal.

Grants are additive, carry an expiry, and are stored as rows rather than
mutations of the tier, so the original allowance is recoverable and the grant
shows in the audit log with its issuer and reason.

## Session model

A session is the unit of conversational context and the unit an administrator
can revoke.

| Limit | Default | Enforced |
| --- | --- | --- |
| Idle timeout | 30 min | Sweeper job; context discarded |
| Maximum length | 4 hrs | Hard close regardless of activity |
| Turns per session | 40 | Further turns require a new session |
| Burst rate | 6/min per seat | Token bucket in Redis |
| Concurrent sessions | Per tier | Oldest is closed when exceeded |

Ending a session does **not** sign the user out of DemandIQ. It discards the
conversational context only — the distinction matters, and the UI says so.

## Authorisation

The assistant holds no credentials of its own. Every tool call carries the
caller's `workspace_id`, `role` and location scope, and hits the same
authorisation layer as the UI. Three consequences:

1. There is no prompt that widens access. The model cannot request a scope it
   was not given.
2. A tier's tool list is a *subset* of what the role already permits. It can
   restrict, never expand.
3. Assistant turns are written to the audit log with the tools invoked, so a
   privileged read through conversation is as traceable as one through a screen.

## Guardrails

Preflight rejects a question that would require data outside scope, returning
the **refusal answer** (`200`, `refused: true`) rather than an HTTP error — the
caller should not have to tell a decline apart from a failure. Postflight scans
the drafted answer and blocks release on:

- kilogram or weight claims about food waste;
- causal verbs applied to a SHAP attribution;
- a point forecast with no interval;
- a preparation recommendation with no stated trade-off;
- any claim with no resolvable source id.

Locked rules (the first three) cannot be disabled by an administrator, because
they are the honesty claims the product is sold on. A blocked answer is
regenerated once, then downgraded to the refusal template.

## Persistence

```
agent_sessions(id, workspace_id, user_id, started_at, ended_at, end_reason,
               device, ip_hash, turn_count)
agent_turns(id, session_id, question, answer_json, tools_used[], latency_ms,
            refused, guardrail_blocks[], created_at)
agent_quota_grants(id, workspace_id, scope, extra_daily, expires_at,
                   issued_by, reason)
agent_pins(id, user_id, turn_id, created_at)
```

Transcripts are retained 90 days by default, redacted of member names and
emails when the PII rule is on. Quota counters live in Redis with a nightly
reconciliation into Postgres, so a cache loss cannot hand out free allowance
permanently.

## Observability

Per workspace and per seat: questions, tool calls, tools per answer, refusal
rate, guardrail block rate, p50/p95 latency, and cost. The refusal rate is
tracked as a **health** metric, not a failure metric — a rate near zero
suggests the guardrails are not firing when they should.
