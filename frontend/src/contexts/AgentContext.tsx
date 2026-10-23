import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiException } from '../api/client';
import { useToast } from './ToastContext';
import type { AgentAnswer, AgentInsight } from '../data/agent';
import { agentPrompts as staticPrompts } from '../data/agent';
import { useSession } from './SessionContext';

export interface AgentMessage {
  id: string;
  author: 'user' | 'agent';
  text?: string;
  answer?: AgentAnswer;
  pending?: boolean;
  scope?: string;
  turnId?: string;
  refused?: boolean;
  latencyMs?: number;
  toolsUsed?: string[];
}

export interface PinnedAnswer {
  id: string;
  question: string;
  answer: AgentAnswer;
  scope: string;
  turnId: string;
}

export interface AgentQuota {
  used: number;
  limit: number;
  tier: string;
  resetsAt: string | null;
}

export interface AgentLimits {
  idle_timeout_min: number;
  max_duration_h: number;
  turns: number;
  burst_per_min: number;
}

export interface AgentErrorState {
  variant: 'error' | 'offline' | 'empty';
  title: string;
  description: string;
  reference?: string | null;
  status?: number | null;
}

interface AgentContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  /** Set once a user gesture has opened the panel; see `userOpenedRef`. */
  userOpenedRef: React.MutableRefObject<boolean>;
  toggle: () => void;
  messages: AgentMessage[];
  ask: (question: string) => Promise<void>;
  reset: () => Promise<void>;
  thinking: boolean;
  scope: string;
  setScope: (scope: string) => void;
  scopeOptions: string[];
  scopeIdMap: Record<string, string>;
  pinned: PinnedAnswer[];
  pin: (message: AgentMessage) => Promise<void>;
  unpin: (id: string) => void;
  quota: AgentQuota;
  remaining: number;
  exhausted: boolean;
  quotaPct: number;
  insights: AgentInsight[];
  prompts: Record<string, string[]>;
  promptsForRoute: (route: string) => string[];
  sessionId: string | null;
  limits: AgentLimits | null;
  loading: boolean;
  error: AgentErrorState | null;
  quotaError: AgentErrorState | null;
  insightsError: AgentErrorState | null;
  unread: number;
  clearUnread: () => void;
  refreshQuota: () => Promise<void>;
  refreshInsights: () => Promise<void>;
  openSession: (locationName?: string) => Promise<void>;
  /**
   * The assistant's opening line. Comes from the API so the wording lives in
   * one place; `FALLBACK_GREETING` covers an older server that omits it.
   */
  greeting: string;
}

/**
 * Used only when the API does not supply a greeting (an older server, or a
 * session restored without opening one). Kept identical in intent to the
 * server's `_greeting`, not to a marketing tone.
 */
const FALLBACK_GREETING =
'I answer only from this workspace\u2019s data: demand history, forecasts and their intervals, the production model and its calibration, the preparation policies, and ingestion health. Ask me what to prepare tomorrow, why a number moved, or whether something needs your attention \u2014 if I cannot source an answer I will say so rather than guess.';

const AgentContext = createContext<AgentContextValue | undefined>(undefined);

const DEFAULT_SCOPE = 'All locations';

function toAgentAnswer(raw: any): AgentAnswer {
  const headline: string = raw.headline ?? raw.answer_json?.headline ?? raw.answer?.headline ?? 'Answer';
  const body: string[] = raw.body ?? raw.answer_json?.body ?? raw.answer?.body ?? (raw.headline ? [raw.headline] : []);
  // Backend figures/sources already shaped
  const figures = raw.figures ?? raw.answer_json?.figures ?? [];
  const sources = (raw.sources ?? raw.answer_json?.sources ?? []).map((s: any) => ({
    label: s.label ?? s.name ?? 'Source',
    detail: s.detail ?? s.ref ?? s.id ?? '',
  }));
  const id = String(raw.id ?? raw.turn_id ?? Math.random().toString(36).slice(2));
  return {
    id,
    match: [],
    headline,
    body: body.length ? body : [headline],
    sources: sources.length ? sources : [{ label: 'Workspace data', detail: 'demand, models, policies' }],
    figures: figures.length ? figures : undefined,
    action: raw.action ?? undefined,
  };
}

function errToState(e: unknown, fallbackTitle = 'Failed to load'): AgentErrorState {
  if (e instanceof ApiException) {
    const ref = e.reference ? ` (ref: ${e.reference})` : '';
    const desc = `${e.error.message}${ref}`;
    if (e.status >= 500) return { variant: 'error', title: fallbackTitle, description: desc, reference: e.reference, status: e.status };
    if (e.status === 404) return { variant: 'empty', title: 'Not found', description: desc, reference: e.reference, status: e.status };
    return { variant: 'error', title: fallbackTitle, description: desc, reference: e.reference, status: e.status };
  }
  if (e instanceof TypeError) return { variant: 'offline', title: 'You are offline', description: 'Check your connection and try again.', status: null };
  return { variant: 'offline', title: 'Network error', description: String(e), status: null };
}

/**
 * Width at which an open assistant docks as a third column instead of
 * overlaying the page. It must match the `min-[1180px]:` classes in
 * `agent/AgentPanel`. Chosen over Tailwind's `xl` (1280) so an iPad in landscape
 * and every laptop get the rail, while 1024px — where a 236px sidebar, a 320px
 * rail and the content region do not coexist — stays an overlay.
 */
export const RAIL_MIN_WIDTH = 1180;

export function AgentProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useSession();
  const { toast } = useToast();
  // Closed on load at every width: a 320px rail is opened by a gesture (the
  // header launcher, Ctrl+J, a suggestion), never taken over the page on a page
  // view nobody asked a question on. Docking only decides *how* it appears
  // once open — beside the content above 1180px, over it below.
  const [open, setOpen] = useState(false);
  /**
   * Whether the *user* has opened the panel.
   *
   * The panel creates an assistant session when it opens, and a session on mount
   * is exactly what was removed before: React StrictMode's double-mount alone
   * created two, and the concurrent-session allowance eventually 409'd. Every
   * route into `open` is a gesture today, so this is true whenever the panel is,
   * but it stays as the explicit gate the panel reads: a future programmatic
   * open (a deep link, a notification) must not start spending sessions on
   * render. A ref rather than state because it is read by an effect that already
   * re-runs when `open` changes.
   */
  const userOpenedRef = useRef(false);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [pinned, setPinned] = useState<PinnedAnswer[]>([]);
  const [thinking, setThinking] = useState(false);
  const [scope, setScope] = useState<string>(DEFAULT_SCOPE);
  const [scopeOptions, setScopeOptions] = useState<string[]>([DEFAULT_SCOPE, 'Kimironko Kitchen', 'Nyarugenge Canteen', 'Remera Fresh']);
  const [scopeIdMap, setScopeIdMap] = useState<Record<string, string>>({});
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [greeting, setGreeting] = useState<string>(FALLBACK_GREETING);
  const [limits, setLimits] = useState<AgentLimits | null>(null);
  const [quota, setQuota] = useState<AgentQuota>({ used: 0, limit: 60, tier: 'operator', resetsAt: null });
  const [insights, setInsights] = useState<AgentInsight[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AgentErrorState | null>(null);
  const [quotaError, setQuotaError] = useState<AgentErrorState | null>(null);
  const [insightsError, setInsightsError] = useState<AgentErrorState | null>(null);
  const [unread, setUnread] = useState(0);
  const nextId = useRef(1);

  const remaining = Math.max(0, quota.limit - quota.used);
  const exhausted = remaining === 0;
  const quotaPct = quota.limit > 0 ? Math.min(100, Math.round((quota.used / quota.limit) * 100)) : 0;

  // Fetch locations for scope selector (GET /locations)
  const refreshScopes = useCallback(async () => {
    try {
      const res = await api.get<{ data: Array<{ id: string; name: string }> }>('/api/v1/locations', { limit: 100 });
      const data = res.data ?? [];
      if (data.length) {
        const names = [DEFAULT_SCOPE, ...data.map((l) => l.name)];
        setScopeOptions(names);
        const map: Record<string, string> = {};
        data.forEach((l) => { map[l.name] = l.id; });
        setScopeIdMap(map);
      }
    } catch {
      // keep fallback; offline handled per panel via TypeError check if needed
    }
  }, []);

  const refreshQuota = useCallback(async () => {
    try {
      const q = await api.get<{ used: number; limit: number; tier: string; resets_at: string }>('/api/v1/agent/quota');
      setQuota({ used: q.used ?? 0, limit: q.limit ?? 60, tier: q.tier ?? 'operator', resetsAt: q.resets_at ?? null });
      setQuotaError(null);
    } catch (e) {
      setQuotaError(errToState(e, 'Failed to load quota'));
      if (e instanceof TypeError) setError({ variant: 'offline', title: 'You are offline', description: 'Quota unavailable offline.' });
    }
  }, []);

  const refreshInsights = useCallback(async () => {
    try {
      const res = await api.get<{ data: any[] }>('/api/v1/agent/insights');
      const data = res.data ?? [];
      // Map to AgentInsight shape; adapt backend camel vs snake
      const mapped: AgentInsight[] = data.map((d: any) => ({
        id: String(d.id),
        tone: (d.tone ?? 'signal') as AgentInsight['tone'],
        title: d.title ?? d.headline ?? 'Insight',
        detail: d.detail ?? d.body ?? '',
        route: d.route ?? d.action?.route,
      }));
      setInsights(mapped);
      if (mapped.length > unread) setUnread(mapped.length);
      setInsightsError(null);
    } catch (e) {
      setInsightsError(errToState(e, 'Failed to load insights'));
    }
  }, [unread]);

  const replay = useCallback(async (sid: string) => {
    try {
      const res = await api.get<{ data: any[] }>('/api/v1/agent/messages', { session_id: sid });
      const data = res.data ?? [];
      const mapped: AgentMessage[] = [];
      for (const turn of data) {
        // turn may have question + answer_json
        const q: string = turn.question ?? turn.question_text ?? '';
        const ansRaw = typeof turn.answer_json === 'string' ? JSON.parse(turn.answer_json) : turn.answer_json ?? turn.answer ?? turn;
        if (q) mapped.push({ id: `u-${turn.id}`, author: 'user', text: q, scope });
        const answer = toAgentAnswer(ansRaw ?? turn);
        mapped.push({
          id: String(turn.id),
          author: 'agent',
          answer,
          scope,
          turnId: String(turn.id),
          refused: ansRaw?.refused ?? false,
          latencyMs: ansRaw?.latency_ms,
          toolsUsed: ansRaw?.tools_used,
        });
      }
      setMessages(mapped);
      setError(null);
    } catch (e) {
      setError(errToState(e, 'Failed to replay conversation'));
      if (e instanceof ApiException) toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      else if (e instanceof TypeError) toast('You are offline. Transcript unavailable.', 'error');
    }
  }, [scope, toast]);

  const openSession = useCallback(async (locationName?: string) => {
    const name = locationName ?? scope;
    const locId = scopeIdMap[name];
    setLoading(true);
    setError(null);
    try {
      const res = await api.post<{ session_id: string; greeting?: string; limits: AgentLimits; quota: { used: number; limit: number; tier: string } }>('/api/v1/agent/sessions', {
        location_id: locId ?? null,
      });
      setSessionId(res.session_id);
      // Greeting is API-sourced so the wording has one home; fall back locally
      // rather than showing an empty panel if the field is absent.
      if (res.greeting) setGreeting(res.greeting);
      if (res.limits) setLimits(res.limits);
      if (res.quota) setQuota((prev) => ({ ...prev, used: res.quota.used, limit: res.quota.limit, tier: res.quota.tier }));
      // replay will load messages for this session (empty initially)
      await replay(res.session_id);
      await refreshQuota();
    } catch (e) {
      const state = errToState(e, 'Failed to open session');
      setError(state);
      if (e instanceof ApiException) {
        const code = (e.error as any)?.code ?? e.error?.code;
        if (code === 'quota_exhausted_daily' || e.status === 429) {
          toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
        } else if (code === 'session_limit_reached' || e.status === 409) {
          toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
        } else {
          toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
        }
      } else if (e instanceof TypeError) {
        toast('You are offline. Session unavailable.', 'error');
      }
    } finally {
      setLoading(false);
    }
  }, [scope, scopeIdMap, replay, refreshQuota, toast]);

  // Initial bootstrap + re-bootstrap on auth transition. The provider wraps
  // every route (mounted pre-login), so the first pass runs unauthenticated
  // (locations/quota/insights all 401 and the panel would keep showing that
  // stale error forever). Re-running when the session becomes authenticated
  // refreshes everything the panel reads.
  useEffect(() => {
    if (!isAuthenticated) return;
    void refreshScopes();
    void refreshQuota();
    void refreshInsights();
  }, [isAuthenticated, refreshScopes, refreshQuota, refreshInsights]);

  // Open session on mount / when scope changes if no session
  // Sessions are created lazily by ask() when the first question is sent.
  // Auto-opening on mount burned the small concurrent-session allowance on
  // every page view (React StrictMode double-mount alone created two
  // sessions) and eventually 409'd ("session_limit_reached").

  const handleScopeChange = useCallback((next: string) => {
    setScope(next);
    // open new session for new scope
    void openSession(next);
  }, [openSession]);

  const ask = useCallback(async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed) return;
    if (exhausted) {
      toast('Daily allowance reached. Ask an administrator for more.', 'error');
      return;
    }
    // Ensure a session: if none yet, create one inline and use the returned id
    // (state updates are async, so a follow-up openSession() call would still
    // see no session here).
    let sid = sessionId;
    if (!sid) {
      const name = scope;
      const locId = scopeIdMap[name];
      try {
        const res = await api.post<{ session_id: string; greeting?: string; limits: AgentLimits; quota: { used: number; limit: number; tier: string } }>('/api/v1/agent/sessions', {
          location_id: locId ?? null,
        });
        setSessionId(res.session_id);
        // Same greeting handling as openSession(): this lazy path is the common
        // one, because a session is created on the first question rather than
        // when the panel opens.
        if (res.greeting) setGreeting(res.greeting);
        if (res.limits) setLimits(res.limits);
        if (res.quota) setQuota((prev) => ({ ...prev, used: res.quota.used, limit: res.quota.limit, tier: res.quota.tier }));
        sid = res.session_id;
      } catch (e) {
        const state = errToState(e, 'Failed to open session');
        setError(state);
        if (e instanceof ApiException) {
          toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
        }
        return;
      }
    }
    // sid now holds the existing id or the freshly-created one (state updates
    // are async, so reading sessionId here would still be null).
    if (!sid) return;

    const userId = `u-${nextId.current++}`;
    const agentId = `a-${nextId.current++}`;
    setMessages((cur) => [...cur, { id: userId, author: 'user', text: trimmed, scope }, { id: agentId, author: 'agent', pending: true, scope }]);
    setThinking(true);
    setError(null);

    try {
      const raw = await api.post<any>('/api/v1/agent/messages', { session_id: sid, question: trimmed });
      const answer = toAgentAnswer(raw);
      setMessages((cur) => cur.map((m) => (m.id === agentId ? { ...m, pending: false, answer, turnId: String(raw.id), refused: raw.refused, latencyMs: raw.latency_ms, toolsUsed: raw.tools_used } : m)));
      setThinking(false);
      void refreshQuota();
      void refreshInsights();
    } catch (e) {
      // remove pending and surface error as EmptyState + toast
      setThinking(false);
      if (e instanceof ApiException) {
        const code = (e.error as any)?.code ?? e.error?.code;
        const ref = e.reference ? ` (ref: ${e.reference})` : '';
        const retryAfter = (e as any)?.headers?.get?.('Retry-After') ?? null;
        let title = 'Failed to answer';
        if (e.status === 429 && (code === 'quota_exhausted_daily' || code === 'rate_limited' || code === 'turn_limit')) {
          title = code === 'quota_exhausted_daily' ? 'Daily allowance reached' : 'Rate limited';
          setError({ variant: 'error', title, description: e.error.message + ref, reference: e.reference, status: e.status });
          toast(`${e.error.message}${retryAfter ? ` (retry after ${retryAfter}s)` : ''}${ref}`, 'error');
          setMessages((cur) => cur.filter((m) => m.id !== agentId));
          void refreshQuota();
          return;
        }
        if (e.status === 409 && code === 'session_limit_reached') {
          setError({ variant: 'error', title: 'Session limit reached', description: e.error.message + ref, reference: e.reference, status: e.status });
          toast(e.error.message + ref, 'error');
          setMessages((cur) => cur.filter((m) => m.id !== agentId));
          return;
        }
        // Compatibility path: the server now returns a guardrail decline as a
        // normal answer with `refused: true`, so this no longer fires against it.
        // Kept for a server that still answers 422 guardrail_block.
        if (e.status === 422 && code === 'guardrail_block') {
          setError({ variant: 'error', title: 'Guardrail blocked', description: e.error.message + ref, reference: e.reference, status: e.status });
          toast(e.error.message + ref, 'error');
          // Keep message but mark refused
          const blockedAnswer: AgentAnswer = {
            id: agentId,
            match: [],
            headline: 'Blocked by guardrail',
            body: [e.error.message],
            sources: [],
          };
          setMessages((cur) => cur.map((m) => (m.id === agentId ? { ...m, pending: false, answer: blockedAnswer, refused: true } : m)));
          return;
        }
        setError({ variant: 'error', title, description: e.error.message + ref, reference: e.reference, status: e.status });
        toast(e.error.message + ref, 'error');
        setMessages((cur) => cur.filter((m) => m.id !== agentId));
      } else if (e instanceof TypeError) {
        setError({ variant: 'offline', title: 'You are offline', description: 'Your question was not sent. Check your connection and try again.', status: null });
        toast('You are offline. Question not sent.', 'error');
        setMessages((cur) => cur.filter((m) => m.id !== agentId));
      } else {
        setError({ variant: 'offline', title: 'Network error', description: String(e), status: null });
        toast(String(e), 'error');
        setMessages((cur) => cur.filter((m) => m.id !== agentId));
      }
    }
  }, [sessionId, scope, scopeIdMap, exhausted, refreshQuota, refreshInsights, toast]);

  const reset = useCallback(async () => {
    if (sessionId) {
      try {
        await api.delete(`/api/v1/agent/sessions/${sessionId}`);
      } catch {}
      setSessionId(null);
    }
    setMessages([]);
    setThinking(false);
    setError(null);
    // open new session for current scope
    void openSession(scope);
  }, [sessionId, scope, openSession]);

  const pin = useCallback(async (message: AgentMessage) => {
    if (!message.answer || !message.turnId) {
      // fallback to local pin when no turnId (e.g., offline)
      const msgIdx = messages.findIndex((m2) => m2.id === message.id);
      const question = (msgIdx > 0 ? messages[msgIdx - 1]?.text : '') ?? 'Saved answer';
      if (!message.answer) return;
      setPinned((items) => (items.some((it) => it.id === message.id) ? items : [...items, { id: message.id, question, answer: message.answer!, scope: message.scope ?? scope, turnId: message.turnId ?? message.id }]));
      toast('Answer saved', 'success');
      return;
    }
    try {
      await api.post(`/api/v1/agent/messages/${message.turnId}/pin`, { note: null });
      const idx = messages.findIndex((m) => m.id === message.id);
      const question = idx > 0 ? messages[idx - 1]?.text ?? 'Saved answer' : 'Saved answer';
      setPinned((items) => (items.some((it) => it.turnId === message.turnId) ? items : [...items, { id: message.id, question, answer: message.answer!, scope: message.scope ?? scope, turnId: message.turnId! }]));
      toast('Answer saved', 'success');
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      else if (e instanceof TypeError) toast('You are offline. Save failed.', 'error');
      else toast(String(e), 'error');
    }
  }, [messages, scope, toast]);

  const unpin = useCallback((id: string) => {
    setPinned((items) => items.filter((it) => it.id !== id && it.turnId !== id));
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'j') {
        event.preventDefault();
        // A gesture, so it counts as the user opening the panel even though it
        // bypasses `requestOpen` (which this effect predates and cannot call
        // without a TDZ reference).
        setOpen((v) => {
          if (!v) userOpenedRef.current = true;
          return !v;
        });
        setUnread(0);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const prompts = useMemo(() => staticPrompts, []);

  /**
   * Every route into `open` goes through here so "the user opened this" is
   * recorded in one place. Accepts the updater form too, because the Ctrl+J
   * handler toggles from the current value.
   */
  const requestOpen = useCallback((next: boolean | ((current: boolean) => boolean)) => {
    setOpen((current) => {
      const resolved = typeof next === 'function' ? next(current) : next;
      if (resolved) userOpenedRef.current = true;
      return resolved;
    });
  }, []);

  const value = useMemo(() => ({
    open,
    setOpen: requestOpen,
    userOpenedRef,
    toggle: () => requestOpen((c) => !c),
    messages,
    ask,
    reset,
    thinking,
    scope,
    setScope: handleScopeChange,
    scopeOptions,
    scopeIdMap,
    pinned,
    pin,
    unpin,
    quota,
    remaining,
    exhausted,
    quotaPct,
    insights,
    prompts,
    promptsForRoute: (route: string) => prompts[route] ?? prompts.default ?? [],
    sessionId,
    limits,
    loading,
    error,
    quotaError,
    insightsError,
    unread,
    clearUnread: () => setUnread(0),
    refreshQuota,
    refreshInsights,
    openSession,
    greeting,
  }), [open, requestOpen, messages, ask, reset, thinking, scope, handleScopeChange, scopeOptions, scopeIdMap, pinned, pin, unpin, quota, remaining, exhausted, quotaPct, insights, prompts, sessionId, limits, loading, error, quotaError, insightsError, unread, refreshQuota, refreshInsights, openSession, greeting]);

  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}

export function useAgent() {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error('useAgent must be used within AgentProvider');
  return ctx;
}

// Keep for backwards compat if any file imports agentScopes
export const agentScopes = [DEFAULT_SCOPE, 'Kimironko Kitchen', 'Nyarugenge Canteen', 'Remera Fresh'];
