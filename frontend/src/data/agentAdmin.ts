/**
 * Administration of the assistant: the *wording* for its allowances, limits and
 * governance rules, plus the types the API's payloads are read through. Quotas
 * are counted in *questions*, because that is the unit an operator understands —
 * token accounting stays behind the scenes.
 *
 * **Wording lives here; figures do not.** This module used to carry its own
 * tables of allowances, seat usage, sessions, budget, usage series and guardrail
 * state. The quota one had drifted from what the API enforces — a different
 * `monthly` and `concurrent`, a `viewer` tier the backend has never had, and an
 * `admin` key where the backend says `administrator`, so a seat's allowance was
 * displayed as the Data Clerk's. The remaining datasets had no importers at all
 * and still described a `viewer` tier and a `6000`-question budget, so they were
 * removed rather than left to be imported later: a fabricated figure that is
 * merely unreferenced is one import away from being on screen again.
 *
 * The figures now come from the API — `GET /api/v1/admin/agent/tiers` for the
 * per-tier allowances and the session limits, `GET /api/v1/admin/agent/quotas`
 * for per-seat usage — which read them out of the same tables the request path
 * enforces.
 */

/** A tier as the API describes it (`GET /api/v1/admin/agent/tiers`). */
export interface QuotaTier {
  id: string;
  name: string;
  /** Questions allowed per seat per day. */
  daily: number;
  /** Questions allowed per seat per month. */
  monthly: number;
  /** Concurrent assistant sessions a seat may hold. */
  concurrent: number;
  /** Tools this tier may invoke; `*` means every tool. */
  tools: string[];
}

/** Blurb per tier, keyed by the id the API returns. Copy only, no numbers. */
export const TIER_BLURB: Record<string, string> = {
  operator: 'Everything needed to decide tomorrow’s prep.',
  analyst: 'Adds data quality and model health for investigation work.',
  administrator: 'Full read surface, including pipeline and drift.',
  owner: 'Everything, including administration of the assistant itself.',
};

/**
 * A tool token as an operator should read it.
 *
 * The API lists the tools a tier may invoke by their machine names, and `*` for
 * the tiers that may use all of them — which rendered as a literal asterisk.
 */
export const TOOL_LABELS: Record<string, string> = {
  '*': 'Everything',
  forecast: 'Forecasts',
  history: 'History',
  explain: 'Explanations',
  policy: 'Policies',
  quality: 'Data quality',
  model: 'Model health',
  pipeline: 'Pipeline',
};

/** The session limits, as the API reports them on session create and to admins. */
export interface SessionLimits {
  idle_timeout_min: number;
  max_duration_h: number;
  turns: number;
  burst_per_min: number;
}

/**
 * Copy for each enforced session limit, keyed by the API's field name.
 *
 * These were previously four hardcoded values in this file (30/4/40/6) that
 * happened to match the server's constants. The values now come from
 * `GET /api/v1/admin/agent/tiers`'s `session` block, so an edit to a server
 * constant cannot leave this screen describing a limit the API does not apply.
 */
export const SESSION_LIMIT_COPY: Array<{
  key: keyof SessionLimits;
  label: string;
  detail: string;
  unit: string;
}> = [
  {
    key: 'idle_timeout_min',
    label: 'Idle timeout',
    detail: 'End an assistant session after this much inactivity.',
    unit: 'min',
  },
  {
    key: 'max_duration_h',
    label: 'Maximum session length',
    detail: 'Hard cap regardless of activity. The user can start a new one.',
    unit: 'hrs',
  },
  {
    key: 'turns',
    label: 'Turns per session',
    detail: 'Keeps a runaway conversation from draining a monthly allowance.',
    unit: 'turns',
  },
  {
    key: 'burst_per_min',
    label: 'Burst rate',
    detail: 'Questions allowed in any 60-second window, per seat.',
    unit: '/min',
  },
];

/** A seat's quota row, as `GET /api/v1/admin/agent/quotas` returns it. */
export interface SeatQuota {
  id: string;
  name: string;
  email: string;
  initials: string;
  tier: string;
  usedToday: number;
  /** Questions used so far this calendar month, summed per seat. */
  usedMonth: number;
  /** Per-seat override of the tier's daily allowance, when set. */
  override?: number;
  status: 'active' | 'throttled' | 'suspended';
}

/** A live assistant session, as the sessions list returns it. */
export interface AgentSession {
  id: string;
  user: string;
  initials: string;
  /** The location the session is scoped to, when the API names one. */
  location: string | null;
  startedAt: string;
  /** Turns answered, or null when the API has no count for the session. */
  turns: number | null;
  /** How long since the session was last seen, or null when unknown. */
  idleFor: string | null;
  current?: boolean;
}

/** One guardrail rule as the API reports it. */
export interface GuardrailRule {
  id: string;
  label: string;
  detail: string;
  enabled: boolean;
  locked?: boolean;
}
