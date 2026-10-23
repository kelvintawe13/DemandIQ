import { useCallback, useEffect, useMemo, useState } from 'react';
import { SparklesIcon, GaugeIcon, UsersIcon, MonitorIcon, ShieldCheckIcon, LockIcon, PencilIcon, LogOutIcon, PlusIcon, TrendingUpIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { TabBar } from '../../components/ui/TabBar';
import { Toggle } from '../../components/ui/Toggle';
import { ResponsiveTable } from '../../components/ui/Table';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { PermissionNote } from '../../components/ui/PermissionNote';
import { api, ApiException } from '../../api/client';
import { SESSION_LIMIT_COPY, TIER_BLURB, TOOL_LABELS } from '../../data/agentAdmin';
import type {
  SeatQuota,
  GuardrailRule,
  AgentSession,
  QuotaTier,
  SessionLimits,
} from '../../data/agentAdmin';
import { auditClientLabel, auditLine, auditOutcomeLabel, plainStatus } from '../../lib/plainLanguage';

/**
 * The assistant's enforced allowances, as returned by
 * `GET /api/v1/admin/agent/tiers`.
 *
 * Every figure on this screen is read from here rather than from a local table,
 * because a local table is one edit away from describing a limit the API does
 * not apply. The list used to live in `data/agentAdmin.ts` and had drifted:
 * a different `monthly`, a different `concurrent`, a `viewer` tier the backend
 * has never had, and an `admin` key where the backend says `administrator`, so
 * an administrator's allowance was displayed as the Data Clerk's.
 */
type EnforcedLimits = {
  tiers: QuotaTier[];
  /** Tier a role with no entry of its own is given (the viewer role has none). */
  fallbackTier: string;
  session: SessionLimits | null;
};

/** The tier for an id, falling back to the one the API names as the default. */
function tierIn(limits: EnforcedLimits | null, id: string): QuotaTier | undefined {
  if (!limits) return undefined;
  return (
    limits.tiers.find((tier) => tier.id === id) ??
    limits.tiers.find((tier) => tier.id === limits.fallbackTier)
  );
}

/** The enforced monthly allowance for a seat's tier; 0 when it cannot be read. */
function monthlyLimitFor(tierId: string, limits: EnforcedLimits | null): number {
  return tierIn(limits, tierId)?.monthly ?? 0;
}

type Tab='quotas'|'sessions'|'guardrails';
const tabs:{id:Tab;label:string;icon:typeof UsersIcon}[]=[
  {id:'quotas',label:'Quotas & seats',icon:UsersIcon},
  {id:'sessions',label:'Sessions & limits',icon:MonitorIcon},
  {id:'guardrails',label:'Guardrails',icon:ShieldCheckIcon}
];

function initialsFor(nameOrEmail: string): string {
  const base = nameOrEmail.trim().split('@')[0];
  const parts = base.split(/[\s_.-]+/).filter(Boolean);
  if (parts.length === 0) return '??';
  if (parts.length === 1) return parts[0].slice(0,2).toUpperCase();
  return (parts[0][0] + parts[parts.length-1][0]).toUpperCase();
}

/**
 * "12 min ago" for a session's last-seen timestamp, or `null` when the API
 * reports none. The sessions list used to fall back to a literal dash, so an
 * idle time was printed for sessions whose last-seen the API had never sent.
 */
function idleLabel(value: unknown): string | null {
  if (typeof value !== 'string' || !value) return null;
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return null;
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function UsageBar({used,limit}:{used:number;limit?:number}){
  // An unknown allowance is shown as the usage alone, never as a percentage of
  // zero: the tier list is fetched, and a ratio computed before it arrives would
  // be a figure the API never stated.
  if(limit===undefined || limit<=0) return <span className="text-fine font-semibold tabular-nums text-slate-600">{used} used</span>;
  const pct=Math.min(100,Math.round(used/limit*100));
  const tone=pct>=95?'bg-red-500':pct>=80?'bg-amber-500':'bg-brand-600';
  return <div className="flex items-center gap-2"><div className="h-1.5 w-full min-w-[56px] overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full ${tone}`} style={{width:`${pct}%`}} /></div><span className="shrink-0 text-fine font-semibold tabular-nums text-slate-600">{used}/{limit}</span></div>;
}

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
    return {variant:'error' as const,title:'Failed to load governance',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function AiGovernance(){
  const { toast } = useToast();
  const [tab,setTab]=useState<Tab>('quotas');
  const [seats,setSeats]=useState<SeatQuota[]>([]);
  const [sessions,setSessions]=useState<AgentSession[]>([]);
  const [limits,setLimits]=useState<EnforcedLimits|null>(null);
  const [limitsError,setLimitsError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [rules,setRules]=useState<GuardrailRule[]>([]);
  const [editing,setEditing]=useState<SeatQuota>();
  const [issueOpen,setIssueOpen]=useState(false);
  const [revoking,setRevoking]=useState<string>();
  const [draft,setDraft]=useState({tier:'operator',override:'',reason:''});
  const [grant,setGrant]=useState({scope:'All operators',extra:'50',expiry:'7'});
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [seatsError,setSeatsError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [sessionsError,setSessionsError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [guardrailsError,setGuardrailsError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [usageError,setUsageError]=useState<ReturnType<typeof errMeta>|null>(null);
  // workspace budget derived from /admin/agent/usage
  const [workspaceBudget,setWorkspaceBudget]=useState<{questionsUsed:number;questionsLimit:number;toolCalls:number;avgToolsPerAnswer:number;refusals:number;periodLabel:string} | null>(null);
  const [usageSeries,setUsageSeries]=useState<number[]>([]);
  const [audit,setAudit]=useState<any[]>([]);

  // Undefined rather than a plausible-looking placeholder: the callers render
  // "used" without a limit for a tier the API did not list, instead of inventing
  // an allowance for it.
  const tierOf=useCallback((id:string)=> tierIn(limits, id),[limits]);

  const fetchLimits = useCallback(async (): Promise<EnforcedLimits>=>{
    try{
      const res = await api.get<{data:QuotaTier[]; fallback_tier:string; session:SessionLimits}>('/api/v1/admin/agent/tiers');
      const next: EnforcedLimits = {
        tiers: res.data ?? [],
        fallbackTier: res.fallback_tier ?? '',
        session: res.session ?? null,
      };
      setLimits(next);
      setLimitsError(null);
      return next;
    }catch(e){
      setLimitsError(errMeta(e));
      const empty: EnforcedLimits = { tiers: [], fallbackTier: '', session: null };
      setLimits(empty);
      return empty;
    }
  },[]);

  const fetchSeats = useCallback(async()=>{
    try{
      const res = await api.get<{data:any[]}>('/api/v1/admin/agent/quotas');
      const data = res.data ?? [];
      const mapped: SeatQuota[] = data.map((r:any)=> {
        const tierId = r.tier ?? r.tier_id ?? 'operator';
        return {
          id: String(r.user_id ?? r.id),
          name: r.name ?? r.email ?? `Seat ${String(r.user_id).slice(0,8)}`,
          email: r.email ?? '',
          initials: r.initials ?? initialsFor(r.name ?? r.email ?? r.user_id ?? '?'),
          tier: tierId,
          usedToday: r.used_today ?? r.usedToday ?? 0,
          usedMonth: r.used_month ?? r.usedMonth ?? 0,
          status: (r.status ?? 'active') as SeatQuota['status'],
          override: r.daily_override ?? r.override ?? undefined,
        };
      });
      setSeats(mapped);
      setSeatsError(null);
      return mapped;
    } catch(e){
      setSeatsError(errMeta(e));
      setSeats([]);
      return [];
    }
  },[]);

  // The seat list arrives as an argument rather than a closed-over dependency.
  // It used to be in this callback's dependency array, which made `fetchUsage`
  // (and through it `fetchAll`) a new function on every seats update; the
  // `useEffect([fetchAll])` below then re-ran forever, re-fetching seats and
  // usage in a loop (a `useCallback` that depends on the state it helps set).
  const fetchUsage = useCallback(async (seatsForLimit: SeatQuota[] = [], limitsForBudget: EnforcedLimits | null = null)=>{
    try{
      const res = await api.get<{data:any[]}>('/api/v1/admin/agent/usage', {days:14});
      const data = res.data ?? [];
      const series = data.map((d:any)=> Number(d.questions ?? 0));
      setUsageSeries(series);
      if(data.length){
        const totalQ = data.reduce((s:any,d:any)=> s + Number(d.questions ?? 0),0);
        const totalTools = data.reduce((s:any,d:any)=> s + Number(d.tool_calls ?? d.toolCalls ?? 0),0);
        const totalRefusals = data.reduce((s:any,d:any)=> s + Number(d.refusals ?? 0),0);
        const limit = seatsForLimit.reduce((sum, seat)=> sum + (seat.override ?? monthlyLimitFor(seat.tier, limitsForBudget)),0);
        // The enforced monthly allowance, summed over the seats. A seat whose
        // tier could not be read contributes 0, and the panel then reports no
        // configured allowance instead of a substitute number. This used to read
        // `daily * 30` per seat and fall back to a hardcoded `6000`, a figure
        // derived on screen that matched no configured limit.
        setWorkspaceBudget({
          questionsUsed: totalQ,
          questionsLimit: limit,
          toolCalls: totalTools,
          avgToolsPerAnswer: totalQ ? Number((totalTools/totalQ).toFixed(1)) : 0,
          refusals: totalRefusals,
          periodLabel: data.length ? `${data[0].date} → ${data[data.length-1].date}` : 'Last 14 days',
        });
      } else {
        setWorkspaceBudget(null);
      }
      // also try audit log
      try{
        const aRes = await api.get<{data:any[]}>('/api/v1/audit-log').catch(()=>({data:[] as any}));
        if(aRes.data) setAudit(aRes.data);
      } catch {}
      setUsageError(null);
    } catch(e){
      setUsageError(errMeta(e));
      setUsageSeries([]);
      setWorkspaceBudget(null);
    }
  },[]);

  const fetchSessions = useCallback(async()=>{
    try{
      const res = await api.get<{data:any[]}>('/api/v1/admin/agent/sessions');
      const data = res.data ?? [];
      const mapped: AgentSession[] = data.map((r:any)=> ({
        id: String(r.id),
        // The API sends the seat's name; when it has none, the fallback is
        // explicitly labelled. It used to render the bare user id as the name.
        user: r.user ?? r.user_name ?? (r.user_id ? `Seat ${String(r.user_id).slice(0,8)}` : 'Unknown seat'),
        initials: r.initials ?? initialsFor(r.user ?? r.user_id ?? '?'),
        // No device is recorded against an assistant session, so the row no
        // longer claims one; it names the location the API actually returns.
        location: typeof r.location === 'string' && r.location ? r.location : null,
        startedAt: r.started_at ? new Date(r.started_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : String(r.started_at ?? '—'),
        turns: typeof r.turn_count === 'number' ? r.turn_count : null,
        idleFor: idleLabel(r.last_seen_at ?? r.last_seen ?? null),
        current: false,
      }));
      setSessions(mapped);
      setSessionsError(null);
    } catch(e){
      setSessionsError(errMeta(e));
      setSessions([]);
    }
  },[]);

  const fetchGuardrails = useCallback(async()=>{
    try{
      const res = await api.get<{data:any[]}>('/api/v1/admin/agent/guardrails');
      const data = res.data ?? [];
      const mapped: GuardrailRule[] = data.map((r:any)=> ({
        id: String(r.id),
        label: r.label,
        detail: r.detail,
        enabled: Boolean(r.enabled),
        locked: Boolean(r.locked),
      }));
      setRules(mapped);
      setGuardrailsError(null);
    } catch(e){
      setGuardrailsError(errMeta(e));
      setRules([]);
    }
  },[]);

  const fetchAll = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      // Allowances first: the seat table and the budget both state them, so they
      // must be in hand before either is computed or rendered.
      const [freshLimits, freshSeats] = await Promise.all([fetchLimits(), fetchSeats(), fetchSessions(), fetchGuardrails()]);
      // The budget is a sum over the seats, so usage is read after them.
      await fetchUsage(freshSeats, freshLimits);
    } catch(e){
      setError(errMeta(e));
    } finally { setLoading(false); }
  },[fetchLimits, fetchSeats, fetchSessions, fetchGuardrails, fetchUsage]);

  // `fetchAll` awaits `fetchSeats` and then `fetchUsage`, so the usage budget is
  // computed against the seats it just loaded; the second effect that used to
  // re-run usage "when seats arrive" was what closed the loop.
  useEffect(()=>{ void fetchAll(); },[fetchAll]);

  const atRisk=useMemo(()=> seats.filter(seat=>{ const limit=seat.override ?? tierOf(seat.tier)?.daily; return Boolean(limit && limit>0 && seat.usedToday/limit>=0.8); }).length,[seats,tierOf]);
  // A budget with no configured allowance has no percentage; 0 rather than a
  // division by zero that would report every workspace as fully consumed.
  const budgetPct = workspaceBudget && workspaceBudget.questionsLimit>0 ? Math.round(workspaceBudget.questionsUsed/workspaceBudget.questionsLimit*100) : 0;
  const sessionLimits = limits?.session ?? null;
  const draftTier = tierOf(draft.tier);
  const peak = usageSeries.length ? Math.max(...usageSeries, 1) : 1;

  const openEdit=(seat:SeatQuota)=>{ setEditing(seat); setDraft({tier:seat.tier, override: seat.override? String(seat.override):'', reason:''}); };
  const saveSeat= async ()=>{
    if(!editing) return;
    try{
      await api.patch(`/api/v1/admin/agent/quotas/${editing.id}`,{tier_id: draft.tier, daily_override: draft.override? Number(draft.override): null, reason: draft.reason});
      toast(`${editing.name}’s assistant quota updated`);
      setEditing(undefined);
      void fetchSeats();
      // audit fallback
      try{ const aRes = await api.get<{data:any[]}>('/api/v1/audit-log').catch(()=>({data:[] as any})); if(aRes.data) setAudit(aRes.data); } catch {}
    } catch(e){
      if(e instanceof ApiException) toast(e.error.message + (e.reference?` (ref: ${e.reference})` : ''),'error');
      else if(e instanceof TypeError) toast('You are offline. Save failed.','error');
      else toast(String(e),'error');
    }
  };
  const issueGrant= async ()=>{
    const extra=Number(grant.extra)||0;
    const scopeRole = grant.scope==='All operators' ? 'operator' : 'workspace';
    try{
      // The grant's own id and expiry come back from the endpoint; the admin
      // needs them to quote the grant or find it later.
      const res = await api.post<{ grant_id?: string; expires_at?: string }>('/api/v1/admin/agent/grants',{scope_role: scopeRole, extra_daily: extra, expires_in_days: Number(grant.expiry), reason: 'admin grant'});
      const expires = res?.expires_at ? new Date(res.expires_at).toLocaleDateString() : null;
      const ref = res?.grant_id ? `grant ${res.grant_id.slice(0, 8)}` : 'the grant';
      toast(`Issued +${extra} questions/day to ${grant.scope} for ${grant.expiry} days: ${ref}${expires ? `, expires ${expires}` : ''}`);
      setIssueOpen(false);
      void fetchSeats();
    } catch(e){
      if(e instanceof ApiException) toast(e.error.message + (e.reference?` (ref: ${e.reference})` : ''),'error');
      else if(e instanceof TypeError) toast('You are offline. Grant failed.','error');
      else toast(String(e),'error');
    }
  };
  const endSession= async (id:string)=>{
    try{
      await api.delete(`/api/v1/admin/agent/sessions/${id}`);
      setSessions(cur=>cur.filter(s=>s.id!==id));
      toast('Assistant session ended','info');
      setRevoking(undefined);
    } catch(e){
      if(e instanceof ApiException) toast(e.error.message + (e.reference?` (ref: ${e.reference})` : ''),'error');
      else if(e instanceof TypeError) toast('You are offline. End failed.','error');
      else toast(String(e),'error');
    }
  };
  const toggleGuardrail= async (rule: GuardrailRule)=>{
    if(rule.locked) return;
    const next = !rule.enabled;
    try{
      await api.patch(`/api/v1/admin/agent/guardrails/${rule.id}`,{enabled: next});
      setRules(cur=> cur.map(item=> item.id===rule.id ? {...item, enabled: next}:item));
      toast(`${rule.label} ${next? 'enabled':'disabled'}`,'info');
    } catch(e){
      if(e instanceof ApiException){
        if(e.status===403) toast(e.error.message + (e.reference?` (ref: ${e.reference})` : ''),'error');
        else toast(e.error.message + (e.reference?` (ref: ${e.reference})` : ''),'error');
      } else if(e instanceof TypeError) toast('You are offline. Toggle failed.','error');
      else toast(String(e),'error');
    }
  };

  if(loading) return <LoadingState label="Loading assistant administration" size="sm" />;
  if(error && seats.length===0 && sessions.length===0 && rules.length===0 && !workspaceBudget) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchAll()}} size="md" />;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        {usageError ? (
          <Panel title="Assistant budget" icon={SparklesIcon} description="Budget">
            <EmptyState variant={usageError.variant} title={usageError.title} description={usageError.desc} action={{label:'Retry', onClick:()=>void fetchUsage(seats)}} size="sm" />
          </Panel>
        ) : workspaceBudget ? (
          <Panel title="Assistant budget" icon={SparklesIcon} description={workspaceBudget.periodLabel}>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div><p className="text-display-sm font-bold leading-none tabular-nums tracking-tight text-slate-900">{workspaceBudget.questionsUsed.toLocaleString()}</p><p className="mt-1 text-micro text-slate-500">{workspaceBudget.questionsLimit>0 ? `of ${workspaceBudget.questionsLimit.toLocaleString()} questions used` : 'questions used · no allowance configured for this workspace'}</p></div>
              {workspaceBudget.questionsLimit>0 && <StatusPill tone={budgetPct>=80?'warning':'success'} dot>{budgetPct}% of budget</StatusPill>}
            </div>
            {workspaceBudget.questionsLimit>0 && <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full ${budgetPct>=80?'bg-amber-500':'bg-brand-600'}`} style={{width:`${budgetPct}%`}} /></div>}
            <div className="mt-4 flex h-14 items-end gap-1" aria-hidden="true">{usageSeries.map((v,i)=> <span key={i} className="flex-1 rounded-t-sm bg-brand-200" style={{height:`${v/peak*100}%`}} />)}</div>
            <p className="mt-1.5 text-fine text-slate-500">Questions per day over the last 14 days</p>
            {/* Was a `<div role="status">` inside a `<p>`, which is invalid
                nesting: the browser closes the paragraph early, so the empty
                state escaped its own layout box. */}
            {audit.length===0 && <div className="mt-2"><EmptyState variant="empty" size="sm" title="No audit trail yet" description="Audit events will appear once governance actions occur." /></div>}
          </Panel>
        ) : (
          <Panel title="Assistant budget" icon={SparklesIcon} description="Budget">
            <EmptyState variant="empty" size="sm" title="No usage yet" description="No assistant usage in the last 14 days. Usage will appear here once questions are asked." />
          </Panel>
        )}
        {workspaceBudget ? (
          <Panel title="Health" icon={GaugeIcon}>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-3">{[['Tool calls',workspaceBudget.toolCalls.toLocaleString()],['Tools per answer',workspaceBudget.avgToolsPerAnswer.toFixed(1)],['Refused for lack of data',String(workspaceBudget.refusals)]].map(([label,value])=> <div key={label}><dt className="text-fine leading-snug text-slate-500">{label}</dt><dd className="mt-0.5 text-lead font-bold tabular-nums tracking-tight text-slate-900">{value}</dd></div>)}</dl>
            <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-slate-50 px-2.5 py-2 text-fine leading-relaxed text-slate-600"><TrendingUpIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-600" strokeWidth={2} />Refusals are healthy. Each one is a question the assistant could not source, answered honestly instead of invented.</p>
          </Panel>
        ) : (
          <Panel title="Health" icon={GaugeIcon}>
            {usageError ? <EmptyState variant={usageError.variant} title={usageError.title} description={usageError.desc} size="sm" action={{label:'Retry', onClick:()=>void fetchUsage(seats)}} /> : <EmptyState variant="empty" size="sm" title="No health data" description="Health metrics will appear once the assistant is used." />}
          </Panel>
        )}
      </div>

      <TabBar tabs={tabs} value={tab} onChange={setTab} ariaLabel="Assistant administration" className="self-start" />

      {tab==='quotas' && <div className="flex flex-col gap-3">
          <Panel title="Seat allowances" description={`${seats.length} seats · ${atRisk} at or above 80% of today’s allowance.`} action={<button type="button" onClick={()=>setIssueOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlusIcon className="h-3.5 w-3.5" strokeWidth={2.4} />Issue quota</button>}>
            {/* Without the allowances every cell in this table would state a
                limit the API may not enforce, so the table is not shown at all. */}
            {limitsError ? <EmptyState variant={limitsError.variant} title="Seat allowances unavailable" description="The API did not return the assistant's enforced allowances, so this table would state limits it cannot vouch for." action={{label:'Retry', onClick:()=>void fetchAll()}} size="sm" /> : seatsError ? <EmptyState variant={seatsError.variant} title={seatsError.title} description={seatsError.desc} action={{label:'Retry', onClick:()=>void fetchSeats()}} size="sm" /> : seats.length===0 ? <EmptyState variant="empty" size="md" title="No seats" description="No seats configured. Invite a member to assign a quota." action={{label:'Retry',onClick:()=>void fetchSeats()}} /> : (
            <ResponsiveTable<any>
              caption="Seat allowances"
              minWidth="min-w-[640px]"
              maxHeight="max-h-[420px]"
              rows={seats}
              rowKey={(seat:any)=>String(seat.id)}
              columns={[
                { key:'member', label:'Member', primary:true, render:(seat:any)=> <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-fine font-bold text-slate-600">{seat.initials}</span>
                    <span className="min-w-0"><span className="block truncate text-meta font-semibold text-slate-900">{seat.name}</span><span className="block truncate text-fine text-slate-500">{seat.email || 'No workspace account'}</span></span>
                  </div> },
                { key:'tier', label:'Tier', render:(seat:any)=> <><span className="text-caption text-slate-700">{tierOf(seat.tier)?.name ?? plainStatus(seat.tier)}</span>{seat.override && <span className="ml-1.5 rounded bg-sky-100 px-1.5 py-0.5 text-fine font-semibold text-sky-800">override</span>}</> },
                { key:'today', label:'Today', cellClassName:'md:w-[160px]', render:(seat:any)=> <UsageBar used={seat.usedToday} limit={seat.override ?? tierOf(seat.tier)?.daily} /> },
                { key:'month', label:'This month', cellClassName:'md:w-[160px]', render:(seat:any)=> <UsageBar used={seat.usedMonth} limit={tierOf(seat.tier)?.monthly} /> },
                { key:'status', label:'Status', render:(seat:any)=> <StatusPill tone={seat.status==='active'?'success': seat.status==='throttled'?'warning':'neutral'}>{plainStatus(seat.status)}</StatusPill> },
                { key:'edit', label:'', cellClassName:'pr-0', render:(seat:any)=> <button type="button" onClick={()=>openEdit(seat)} aria-label={`Edit quota for ${seat.name}`} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><PencilIcon className="h-4 w-4" strokeWidth={1.9} /></button> },
              ]}
            />
            )}
          </Panel>
          <Panel title="Tiers" description="A tier bundles a daily allowance with the tools a seat may invoke.">
            {limits && limits.tiers.length>0 ? (
              <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">{limits.tiers.map(tier=> <li key={tier.id} className="flex h-full flex-col rounded-xl border border-slate-200 p-3"><p className="text-body-xs font-bold text-slate-900">{tier.name}</p><p className="mt-0.5 text-fine leading-relaxed text-slate-500">{TIER_BLURB[tier.id] ?? ''}</p><dl className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-fine text-slate-600"><div className="flex gap-1"><dt className="text-slate-500">Daily</dt><dd className="font-semibold tabular-nums">{tier.daily}</dd></div><div className="flex gap-1"><dt className="text-slate-500">Monthly</dt><dd className="font-semibold tabular-nums">{tier.monthly}</dd></div><div className="flex gap-1"><dt className="text-slate-500">Concurrent</dt><dd className="font-semibold tabular-nums">{tier.concurrent}</dd></div></dl><ul className="mt-auto flex flex-wrap gap-1 pt-2.5">{tier.tools.map(tool=> <li key={tool} className="rounded bg-slate-100 px-1.5 py-0.5 text-fine font-medium text-slate-600">{TOOL_LABELS[tool] ?? plainStatus(tool)}</li>)}</ul></li>)}</ul>
            ) : (
              <EmptyState variant={limitsError?.variant ?? 'empty'} size="sm" title="Allowances unavailable" description="The API did not return the assistant's tiers, so any figures shown here would be unverified." action={{label:'Retry', onClick:()=>void fetchAll()}} />
            )}
          </Panel>
        </div>}

      {tab==='sessions' && <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <Panel title="Live assistant sessions" description={`${sessions.length} active right now across the workspace.`}>
            {sessionsError ? <EmptyState variant={sessionsError.variant} title={sessionsError.title} description={sessionsError.desc} size="sm" action={{label:'Retry', onClick:()=>void fetchSessions()}} /> : sessions.length===0 ? <EmptyState variant="empty" size="sm" title="No sessions" description="No assistant sessions are open." /> : <ul className="flex flex-col gap-2">{sessions.map(session=> <li key={session.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-slate-200 px-3 py-2.5"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-fine font-bold text-slate-600">{session.initials}</span><span className="min-w-0 flex-1"><span className="flex items-center gap-1.5"><span className="truncate text-meta font-semibold text-slate-900">{session.user}</span>{session.current && <StatusPill tone="success" dot>active</StatusPill>}</span><span className="block truncate text-fine text-slate-500">{[session.location, `started ${session.startedAt}`].filter(Boolean).join(' · ')}</span></span>{/* Both of these are only stated when the API reports them: an absent turn
              count used to print as "0 turns" and an absent last-seen as "idle" plus a dangling dash. */}<span className="shrink-0 text-right text-fine text-slate-500">{session.turns != null && <span className="block font-semibold tabular-nums text-slate-700">{session.turns} {session.turns === 1 ? 'turn' : 'turns'}</span>}{session.idleFor ? `idle ${session.idleFor}` : ''}</span><button type="button" onClick={()=>setRevoking(session.id)} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:border-red-300 hover:bg-red-50 hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><LogOutIcon className="h-3.5 w-3.5" strokeWidth={1.9} />End</button></li>)}</ul>}
          </Panel>
          <Panel title="Session limits" description="Enforced by the API for every seat, on every session.">
            {/* These were sliders with a "Save limits" button that only showed a
                toast. The API returns them as fixed constants on session create
                and exposes no write endpoint, so editing them did nothing. */}
            {sessionLimits ? (
              <ul className="flex flex-col divide-y divide-slate-100">{SESSION_LIMIT_COPY.map(item=> <li key={item.key} className="flex items-baseline justify-between gap-3 py-2.5 first:pt-0"><span className="min-w-0"><span className="block text-meta font-semibold text-slate-800">{item.label}</span><span className="mt-0.5 block text-fine leading-relaxed text-slate-500">{item.detail}</span></span><span className="shrink-0 text-caption font-bold tabular-nums text-slate-700">{sessionLimits[item.key]} {item.unit}</span></li>)}</ul>
            ) : (
              <EmptyState variant={limitsError?.variant ?? 'empty'} size="sm" title="Limits unavailable" description="The API did not return the assistant's enforced session limits." action={{label:'Retry', onClick:()=>void fetchAll()}} />
            )}
            <PermissionNote className="mt-3">
              These limits are part of the assistant deployment, not a per-workspace setting, so there is nothing here to save. Per-seat allowances are the editable control. They live on Quotas &amp; seats.
            </PermissionNote>
          </Panel>
        </div>}

      {tab==='guardrails' && <Panel title="Answer guardrails" description="Locked rules protect the honesty claims the product is built on and cannot be turned off.">
          {guardrailsError ? <EmptyState variant={guardrailsError.variant} title={guardrailsError.title} description={guardrailsError.desc} size="sm" action={{label:'Retry', onClick:()=>void fetchGuardrails()}} /> : rules.length===0 ? <EmptyState variant="empty" size="sm" title="No guardrails" description="No guardrails configured." /> : <ul className="flex flex-col gap-2">{rules.map(rule=> <li key={rule.id} className="flex items-start gap-3 rounded-xl border border-slate-200 px-3 py-2.5"><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-1.5"><span className="text-meta font-semibold text-slate-900">{rule.label}</span>{rule.locked && <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-fine font-semibold text-slate-600"><LockIcon className="h-2.5 w-2.5" strokeWidth={2.4} />locked</span>}</span><span className="mt-0.5 block text-micro leading-relaxed text-slate-500">{rule.detail}</span></span><Toggle checked={!!rule.enabled} onChange={()=>void toggleGuardrail(rule)} label={rule.label} disabled={rule.locked} title={rule.locked ? 'Locked guardrails protect our honesty claims and cannot be turned off' : undefined} /></li>)}</ul>}
          {audit.length>0 && <div className="mt-3"><p className="text-fine font-semibold text-slate-700">Recent governance activity</p><ul className="mt-2 flex flex-col gap-1.5 text-micro text-slate-600">{audit.slice(0,5).map((a:any)=> <li key={a.id} className="flex items-baseline justify-between gap-2 rounded border border-slate-100 px-2 py-1"><span className="min-w-0 truncate">{auditLine(a.action, a.target)}</span>{/* Same reading as the personal feed: what happened, from which surface. */}<span className="shrink-0 text-slate-500">{[auditOutcomeLabel(a), auditClientLabel(a.client), a.created_at ? new Date(a.created_at).toLocaleDateString() : ''].filter(Boolean).join(' · ')}</span></li>)}</ul></div>}
        </Panel>}

      <Modal open={Boolean(editing)} onClose={()=>setEditing(undefined)} title={editing ? `Quota for ${editing.name}`:''} description="Changing a tier changes both the allowance and the tools this seat may use." footer={<><ModalButton tone="ghost" onClick={()=>setEditing(undefined)}>Cancel</ModalButton><ModalButton onClick={()=>void saveSeat()}>Save quota</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="seat-tier" label="Tier"><select id="seat-tier" value={draft.tier} onChange={e=>setDraft({...draft,tier:e.target.value})} className={inputClass}>{(limits?.tiers ?? []).map(tier=> <option key={tier.id} value={tier.id}>{tier.name} · {tier.daily}/day, {tier.monthly}/month</option>)}</select></Field>
          <Field id="seat-override" label="Daily override" hint={draftTier ? `Leave empty to use the tier default of ${draftTier.daily} questions per day.` : 'Leave empty to use the tier default.'}><input id="seat-override" type="number" min={0} value={draft.override} onChange={e=>setDraft({...draft,override:e.target.value})} placeholder={draftTier ? String(draftTier.daily) : ''} className={inputClass} /></Field>
          <Field id="seat-reason" label="Reason" hint="Written to the audit log alongside the change."><input id="seat-reason" type="text" value={draft.reason} onChange={e=>setDraft({...draft,reason:e.target.value})} placeholder="Month-end reporting push" className={inputClass} /></Field>
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-micro leading-relaxed text-slate-600">Tools unlocked at this tier: <span className="font-semibold text-slate-800">{draftTier ? draftTier.tools.map(tool=> TOOL_LABELS[tool] ?? plainStatus(tool)).join(', ') : '—'}</span>.</p>
        </div>
      </Modal>

      <Modal open={issueOpen} onClose={()=>setIssueOpen(false)} title="Issue a quota grant" description="A temporary top-up on top of existing allowances. It expires on its own." footer={<><ModalButton tone="ghost" onClick={()=>setIssueOpen(false)}>Cancel</ModalButton><ModalButton onClick={()=>void issueGrant()} disabled={!grant.extra}>Issue grant</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="grant-scope" label="Who receives it"><select id="grant-scope" value={grant.scope} onChange={e=>setGrant({...grant,scope:e.target.value})} className={inputClass}><option>All operators</option><option>Everyone in the workspace</option></select></Field>
          <Field id="grant-extra" label="Extra questions per day" hint="Added on top of the seat’s current daily allowance."><input id="grant-extra" type="number" min={1} value={grant.extra} onChange={e=>setGrant({...grant,extra:e.target.value})} className={inputClass} /></Field>
          <Field id="grant-expiry" label="Expires after"><select id="grant-expiry" value={grant.expiry} onChange={e=>setGrant({...grant,expiry:e.target.value})} className={inputClass}><option value="1">1 day</option><option value="7">7 days</option><option value="30">30 days</option></select></Field>
        </div>
      </Modal>

      <ConfirmDialog open={Boolean(revoking)} onClose={()=>setRevoking(undefined)} onConfirm={()=> revoking && void endSession(revoking)} title="End this assistant session?" body="The member stays signed in to DemandIQ. Their assistant conversation is closed and its context is discarded." confirmLabel="End session" tone="danger" />
    </div>
  );
}
