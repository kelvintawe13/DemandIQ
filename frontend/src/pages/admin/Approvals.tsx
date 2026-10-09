import { useCallback, useEffect, useState } from 'react';
import { GitPullRequestIcon, CheckIcon, XIcon, ArrowRightIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { TabBar } from '../../components/ui/TabBar';
import { StatusPill } from '../../components/ui/StatusPill';
import type { StatusTone } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { plainStatus } from '../../lib/plainLanguage';

const statusTone:Record<string,StatusTone>={ pending:'warning', approved:'success', rejected:'critical', open:'warning' };

/**
 * Approval wording, which is not pipeline wording.
 *
 * `plainStatus` maps `pending` to "Waiting to start" — right for a queued job,
 * wrong for a request that is waiting on a *person*. The kinds come from
 * `create_change_request` (`model_promotion`, `policy_change`) and would
 * otherwise reach the screen as snake_case.
 */
const APPROVAL_STATUS:Record<string,string>={ pending:'Awaiting a decision', open:'Awaiting a decision', approved:'Approved', rejected:'Rejected' };
const APPROVAL_KIND:Record<string,string>={ model_promotion:'Model promotion', policy_change:'Preparation policy change' };

const approvalStatus=(s?:string|null)=> s ? APPROVAL_STATUS[s] ?? plainStatus(s) : '—';
const approvalKind=(k?:string|null)=> k ? APPROVAL_KIND[k] ?? plainStatus(k) : '';

/**
 * Who acted, as a person rather than a fragment of a UUID.
 *
 * The API returns `requested_by`/`decided_by` ids alongside the resolved
 * `*_name`. The card used to print the first eight characters of the id, which
 * renders as "Decided by c6f6e213" — indistinguishable from a name at a glance.
 * When no name can be resolved (the actor's row could not be read) the short id
 * is still shown, but labelled "Seat", the wording the seat list already uses
 * for a member it only knows by id.
 */
const actorLabel=(name?:string|null, id?:string|null)=> name?.trim() || (id ? `Seat ${String(id).slice(0, 8)}` : '');
const tabs=['Pending','Decided'] as const;

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load approvals',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function Approvals(){
  const [tab,setTab]=useState<(typeof tabs)[number]>('Pending');
  const [requests,setRequests]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [actionId,setActionId]=useState<string|null>(null);
  const { toast } = useToast();

  const fetch = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const res = await api.get<{data:any[]}>('/api/v1/change-requests');
      setRequests(res.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[]);

  useEffect(()=>{ void fetch(); },[fetch]);

  const visible = requests.filter(r=> tab==='Pending' ? r.status==='pending' : r.status!=='pending');
  const pendingCount = requests.filter(r=>r.status==='pending').length;

  const decide = async (id:string, action:'approve'|'reject')=>{
    setActionId(id);
    try{
      await api.post(`/api/v1/change-requests/${id}/${action}`);
      toast(action==='approve' ? 'Approved' : 'Rejected');
      void fetch();
    }catch(e){
      if(e instanceof ApiException){
        if(e.error.code==='two_person_rule' || e.status===422) {
          toast(e.error.message,'error');
          // show inline two-person rule error via toast + keep pending
        } else if(e.status===409) toast(e.error.message,'error');
        else toast(e.error.message,'error');
      } else toast(String(e),'error');
    } finally{ setActionId(null); }
  };

  if(loading) return <LoadingState label="Loading approvals" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetch()}} size="md" />;

  return (
    <div className="flex flex-col gap-3">
      <TabBar
        tabs={tabs.map(option => ({
          id: option,
          label:
            option === 'Pending' && pendingCount > 0 ? (
              <span className="inline-flex items-center gap-1.5">
                {option}
                <span className={['rounded-full px-1.5 py-0.5 text-fine font-bold', tab === option ? 'bg-white/20 text-white' : 'bg-amber-100 text-amber-800'].join(' ')}>
                  {pendingCount}
                </span>
              </span>
            ) : (
              option
            ),
        }))}
        value={tab}
        onChange={setTab}
        ariaLabel="Change requests"
        className="self-start"
      />

      <Panel title="Change requests" icon={GitPullRequestIcon} description="Model promotions, policy changes and access grants need a second pair of eyes.">
        {visible.length===0 ? (
          <EmptyState variant="empty" size="md" title={tab==='Pending' ? 'No pending approvals' : 'No decisions yet'} description={tab==='Pending' ? 'All caught up. No changes awaiting review.' : 'No approved or rejected requests yet.'} hints={["Promotions need a second admin","Policy changes are audited"]} />
        ) : (
          <ul className="flex flex-col gap-2">{visible.map((request)=>
          <li key={request.id} className="rounded-xl border border-slate-200 px-3.5 py-3">
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                  <div className="min-w-0"><p className="text-body-xs font-semibold text-slate-900">{request.title}</p><p className="text-micro text-slate-500">{[approvalKind(request.kind), actorLabel(request.requested_by_name ?? request.requestedByName, request.requested_by ?? request.requestedBy), request.created_at ? new Date(request.created_at).toLocaleString() : request.requestedAt ?? ''].filter(Boolean).join(' · ')}</p></div>
                  <StatusPill tone={statusTone[request.status] ?? 'neutral'} dot>{approvalStatus(request.status)}</StatusPill>
                </div>
                {/* The arrow carries the meaning visually, so each value needs
                    its own name for a screen reader: "0.82 → 0.9" read aloud
                    with no idea which is the current setting. */}
                <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
                  <span className="sr-only">Current value</span>
                  <code className="rounded bg-white px-1.5 py-0.5 font-mono text-micro text-slate-600">{request.before_value ?? request.before ?? '—'}</code>
                  <span className="sr-only">changes to</span>
                  <ArrowRightIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
                  <span className="sr-only">Proposed value</span>
                  <code className="rounded bg-brand-50 px-1.5 py-0.5 font-mono text-micro font-semibold text-brand-800">{request.after_value ?? request.after ?? '—'}</code>
                </div>
                <p className="mt-2 text-micro leading-relaxed text-slate-600">{request.impact ?? ''}</p>
                {request.status==='pending' ? (
                  <div className="mt-2.5 flex gap-2">
                    <button type="button" disabled={actionId===request.id} onClick={()=>void decide(request.id,'approve')} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:opacity-60"><CheckIcon className="h-3.5 w-3.5" strokeWidth={2.5} />Approve</button>
                    <button type="button" disabled={actionId===request.id} onClick={()=>void decide(request.id,'reject')} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"><XIcon className="h-3.5 w-3.5" strokeWidth={2.5} />Reject</button>
                  </div>
                ) : request.decided_by ? (
                  <p className="mt-2 text-fine text-slate-500">{[`Decided by ${actorLabel(request.decided_by_name ?? request.decidedByName, request.decided_by)}`.trim(), request.decided_at ? new Date(request.decided_at).toLocaleString() : ''].filter(Boolean).join(' · ')}</p>
                ) : null}
              </li>
          )}</ul>
        )}
        {/* The card shows the change, who asked, who decided and when; there is
            no location on a change request, so the copy no longer promises one. */}
        <p className="mt-2 text-fine text-slate-500">{requests.length} requests · each shows the change, who asked and who decided</p>
      </Panel>
    </div>
  );
}
