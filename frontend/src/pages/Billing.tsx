import { useCallback, useEffect, useState } from 'react';
import {
  CreditCardIcon,
  ReceiptIcon,
  CheckIcon,
  DownloadIcon,
  TrendingUpIcon } from
'lucide-react';
import { Panel } from '../components/ui/Panel';
import { StatusPill } from '../components/ui/StatusPill';
import { Modal, ModalButton } from '../components/ui/Modal';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { Field, inputClass } from '../components/ui/Field';
import { EmptyState, EmptyStateVariant } from '../components/ui/EmptyState';
import { PermissionNote } from '../components/ui/PermissionNote';
import { Spinner } from '../components/ui/LoadingState';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { api, ApiException } from '../api/client';
import { RecordList } from '../components/ui/RecordList';
import { plainStatus } from '../lib/plainLanguage';

interface BillingProps {
  onNavigate?: (id: string) => void;
}

// ---------------------------------------------------------------------------
// Fallback constants — kept only for offline/error, primary is live fetch
// ---------------------------------------------------------------------------
const FALLBACK_PLANS = [
{
  id: 'starter',
  name: 'Starter',
  price: 'RWF 0',
  cadence: 'forever',
  blurb: 'One location, manual and spreadsheet import.',
  features: ['1 location', 'Manual + spreadsheet import', '7-day forecast horizon', 'Email support'],
  limits: { locations: 1, forecasts: 1000, api_calls: 10000, history_days: 30 },
},
{
  id: 'growth',
  name: 'Growth',
  price: 'RWF 48,000',
  cadence: 'per month',
  blurb: 'Multi-site forecasting with live connectors.',
  features: [
  'Up to 10 locations',
  'POS + MoMo connectors',
  '30-day horizon with confidence bands',
  'Scheduled reports and webhooks'],
  limits: { locations: 10, forecasts: 10000, api_calls: 50000, history_days: 1095 },
},
{
  id: 'enterprise',
  name: 'Enterprise',
  price: 'Custom',
  cadence: 'annual',
  blurb: 'Unlimited sites, approvals and data residency.',
  features: [
  'Unlimited locations',
  'Two-person approvals and audit export',
  'Dedicated model tuning',
  'Data residency and SSO'],
  limits: { locations: 999, forecasts: 999999, api_calls: 999999, history_days: 9999 },
}];

const FALLBACK_USAGE = [
{ label: 'Locations', used: 3, limit: 10, unit: '' },
{ label: 'Forecasts this month', used: 2840, limit: 10000, unit: '' },
{ label: 'API calls', used: 18400, limit: 50000, unit: '' },
{ label: 'Stored history', used: 412, limit: 1095, unit: ' days' }];

// ---------------------------------------------------------------------------
// Live shapes
// ---------------------------------------------------------------------------
type Plan = {
  id: string;
  name: string;
  price: string;
  cadence: string;
  blurb: string;
  features: string[];
  limits?: Record<string, number>;
  price_cents?: number | null;
};
type Subscription = {
  id: string;
  workspace_id: string;
  plan: Plan | null;
  plan_id: string;
  status: string;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
  trial_end: string | null;
};
type UsageItem = { label: string; used: number; limit: number; unit: string };
type Invoice = { id: string; period: string; amount: string; status: string; date: string; pdf_url?: string | null };
type PaymentMethod = { brand: string; last4: string; exp_month: number; exp_year: number; is_default?: boolean };
type BillingContact = { email: string; tin: string | null };

function toErrorMeta(e: unknown): { variant: EmptyStateVariant; title: string; description: string; hints?: string[] } {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error', title: 'Sign in required', description: e.error.message || 'Your session expired. Sign in again.', hints: e.reference ? [`ref: ${e.reference}`] : ['Sign in to view billing'] };
    if (e.status === 403) return { variant: 'error', title: 'Admin access required', description: e.error.message || 'Administrator or owner required.', hints: e.reference ? [`ref: ${e.reference}`] : ['Contact a workspace owner'] };
    const msg = e.error.message + (e.reference ? ` (ref: ${e.reference})` : '');
    if (e.status >= 500) return { variant: 'error', title: 'Failed to load billing', description: msg, hints: e.reference ? [`ref: ${e.reference}`] : undefined };
    // 404/400 etc still error variant
    return { variant: 'error', title: 'Failed to load billing', description: msg, hints: e.reference ? [`ref: ${e.reference}`] : undefined };
  }
  if (e instanceof TypeError) return { variant: 'offline', title: 'You are offline', description: 'Check your connection and try again.', hints: ['Verify VITE_API_URL is set', 'Check network connectivity'] };
  if (typeof navigator !== 'undefined' && !navigator.onLine) return { variant: 'offline', title: 'You are offline', description: 'Check your connection and try again.', hints: ['Verify VITE_API_URL is set', 'Check network connectivity'] };
  return { variant: 'offline', title: 'Network error', description: String(e) };
}

function formatPeriodEnd(iso?: string | null): string {
  if (!iso) return '1 Oct 2026';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function Billing({ onNavigate: _onNavigate }: BillingProps) {
  const [pendingPlan, setPendingPlan] = useState<string>();
  const [cardOpen, setCardOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const { toast } = useToast();
  const { user } = useSession();
  // Every read on this page is open to any seat (Billing is reachable from the
  // user menu), but changing the plan, card or billing contact is the owner's
  // call — the API answers 403 "Owner role required" for anyone else, so the
  // controls that would send those writes are disabled rather than left to fail.
  const canManage = (user.permissions ?? []).includes('billing:manage');
  const [billingEnabled, setBillingEnabled] = useState<boolean | null>(null);

  // live data
  const [livePlans, setLivePlans] = useState<Plan[] | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usageLive, setUsageLive] = useState<UsageItem[] | null>(null);
  const [invoicesLive, setInvoicesLive] = useState<Invoice[] | null>(null);
  const [paymentLive, setPaymentLive] = useState<PaymentMethod | null>(null);
  // Value not read yet (the contact card renders from the form); the setter is.
  const [, setContactLive] = useState<BillingContact | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof toErrorMeta> | null>(null);

  // payment form
  const [pmBrand, setPmBrand] = useState('visa');
  const [pmLast4, setPmLast4] = useState('');
  const [pmExpMonth, setPmExpMonth] = useState('');
  const [pmExpYear, setPmExpYear] = useState('');
  const [pmErrors, setPmErrors] = useState<Record<string, string>>({});
  const [pmSaving, setPmSaving] = useState(false);

  // contact form
  const [contactEmail, setContactEmail] = useState('');
  const [contactTin, setContactTin] = useState('');
  const [contactErrors, setContactErrors] = useState<Record<string, string>>({});
  const [contactSaving, setContactSaving] = useState(false);

  const [switching, setSwitching] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  // billingEnabled probe
  useEffect(() => {
    let cancelled_ = false;
    // Whether the probe has already reached a verdict. This lives in the
    // effect's own closure rather than in `billingEnabled` state, because a
    // `[]`-dep effect captures the first render's value: reading state here
    // would see `null` forever, so the timeout below would always believe the
    // probe was still pending and overwrite a successful one — the page would
    // render the real console and then flip to "not configured" 900ms later.
    let settled = false;
    const settle = (enabled: boolean) => {
      if (settled) return;
      settled = true;
      if (!cancelled_) setBillingEnabled(enabled);
    };

    void (async () => {
      try {
        await api.get('/api/v1/billing/status');
        settle(true);
      } catch {
        // A probe that fails, a 404/501 (billing not wired), a 5xx, or no
        // network all mean the same thing to this page: there is nothing to
        // bill against, so the fallback plans it already carries are the
        // honest thing to show.
        settle(false);
      }
    })();

    // A probe that never answers must not leave the page on a spinner
    // forever. First verdict wins, so this can only fire while the request is
    // genuinely still in flight.
    const t = window.setTimeout(() => settle(false), 900);
    return () => {
      cancelled_ = true;
      window.clearTimeout(t);
    };
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [plansRes, subRes, usageRes, invoicesRes, contactRes] = await Promise.all([
        api.get<{ data: Plan[] }>('/api/v1/billing/plans'),
        api.get<Subscription>('/api/v1/billing/subscription'),
        api.get<{ usage: UsageItem[]; plan: Plan; subscription: { plan_id: string; status: string } }>('/api/v1/billing/usage'),
        api.get<{ data: Invoice[] }>('/api/v1/billing/invoices'),
        api.get<BillingContact>('/api/v1/billing/contact'),
      ]);
      setLivePlans(plansRes.data ?? []);
      setSubscription(subRes);
      setUsageLive(usageRes.usage ?? []);
      setInvoicesLive(invoicesRes.data ?? []);
      setContactLive(contactRes);
      setContactEmail(contactRes.email ?? '');
      setContactTin(contactRes.tin ?? '');

      // payment-method is optional (404 if none)
      try {
        const pm = await api.get<PaymentMethod>('/api/v1/billing/payment-method');
        setPaymentLive(pm);
        setPmBrand(pm.brand ?? 'visa');
        setPmLast4(pm.last4 ?? '');
        setPmExpMonth(String(pm.exp_month ?? ''));
        setPmExpYear(String(pm.exp_year ?? ''));
      } catch (e) {
        if (e instanceof ApiException && e.status === 404) {
          setPaymentLive(null);
        } else {
          throw e;
        }
      }
    } catch (e) {
      setError(toErrorMeta(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (billingEnabled === true) void fetchAll();
    if (billingEnabled === false) setLoading(false);
  }, [billingEnabled, fetchAll]);

  // derived
  const plansToRender: Plan[] = livePlans && livePlans.length > 0 ? livePlans : FALLBACK_PLANS;
  const currentPlanId = subscription?.plan_id ?? subscription?.plan?.id ?? 'growth';
  const active: Plan = plansToRender.find((p) => p.id === currentPlanId) ?? plansToRender[1] ?? FALLBACK_PLANS[1];
  const cancelled = Boolean(subscription?.cancel_at_period_end);
  const periodEndLabel = formatPeriodEnd(subscription?.current_period_end);
  const usageToRender: UsageItem[] = usageLive ?? FALLBACK_USAGE;
  const invoicesToRender: Invoice[] = invoicesLive ?? [];

  // use invoicesLive null means fallback? but we want empty handling primary live
  const hasInvoicesLive = invoicesLive !== null;
  const showInvoicesEmpty = hasInvoicesLive ? invoicesLive!.length === 0 : false;

  const handleSwitch = async () => {
    if (!pendingPlan) return;
    setSwitching(true);
    try {
      const res = await api.post<{ plan_id: string; status: string; message?: string }>('/api/v1/billing/subscription', { plan_id: pendingPlan });
      if (res.status === 'pending_sales') {
        toast(res.message ?? 'Sales will be in touch within one business day.', 'info');
      } else {
        toast(res.message ?? 'Plan updated.');
        const sub = await api.get<Subscription>('/api/v1/billing/subscription');
        setSubscription(sub);
        try {
          const u = await api.get<{ usage: UsageItem[] }>('/api/v1/billing/usage');
          setUsageLive(u.usage ?? []);
        } catch { /* ignore */ }
      }
    } catch (e) {
      if (e instanceof ApiException) {
        if (e.status === 401) toast('Sign in required. Please sign in again.', 'error');
        else if (e.status === 403) toast('Admin access required. Contact a workspace owner.', 'error');
        else toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      } else if (e instanceof TypeError || !navigator.onLine) {
        toast('You are offline. Check your connection.', 'error');
      } else toast(String(e), 'error');
    } finally {
      setSwitching(false);
      setPendingPlan(undefined);
    }
  };

  const handleDownload = async (id: string) => {
    try {
      const res = await api.get<{ url: string }>('/api/v1/billing/invoices/' + encodeURIComponent(id) + '/download');
      if (res.url) window.open(res.url, '_blank', 'noopener');
      toast(`${id} downloaded.`);
    } catch (e) {
      if (e instanceof ApiException && e.status === 404) toast('Invoice not found.', 'error');
      else if (e instanceof ApiException) toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      else if (e instanceof TypeError || !navigator.onLine) toast('You are offline.', 'error');
      else toast(String(e), 'error');
    }
  };

  const handleSavePayment = async () => {
    const errs: Record<string, string> = {};
    if (!/^\d{4}$/.test(pmLast4.trim())) errs.last4 = 'Last 4 must be exactly 4 digits';
    const m = parseInt(pmExpMonth, 10);
    if (Number.isNaN(m) || m < 1 || m > 12) errs.exp_month = 'Month must be 1–12';
    const y = parseInt(pmExpYear, 10);
    if (Number.isNaN(y) || y < 2024 || y > 2040) errs.exp_year = 'Year must be 2024–2040';
    if (!pmBrand.trim()) errs.brand = 'Brand required';
    if (Object.keys(errs).length) { setPmErrors(errs); return; }
    setPmErrors({});
    setPmSaving(true);
    try {
      const res = await api.post<PaymentMethod>('/api/v1/billing/payment-method', { brand: pmBrand.trim().toLowerCase(), last4: pmLast4.trim(), exp_month: m, exp_year: y });
      setPaymentLive(res);
      setCardOpen(false);
      toast('Payment method updated.');
    } catch (e) {
      if (e instanceof ApiException) {
        if (e.status === 401) toast('Sign in required.', 'error');
        else if (e.status === 403) toast('Admin access required.', 'error');
        else if (e.error.field) setPmErrors({ [e.error.field]: e.error.message });
        else toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      } else if (e instanceof TypeError || !navigator.onLine) toast('You are offline.', 'error');
      else toast(String(e), 'error');
    } finally { setPmSaving(false); }
  };

  const handleSaveContact = async () => {
    const errs: Record<string, string> = {};
    if (!contactEmail.trim() || !contactEmail.includes('@')) errs.email = 'Enter a valid email';
    if (contactTin && contactTin.length > 32) errs.tin = 'TIN must be at most 32 characters';
    if (Object.keys(errs).length) { setContactErrors(errs); return; }
    setContactErrors({});
    setContactSaving(true);
    try {
      const res = await api.post<BillingContact>('/api/v1/billing/contact', { email: contactEmail.trim(), tin: contactTin.trim() || null });
      setContactLive(res);
      setContactEmail(res.email ?? '');
      setContactTin(res.tin ?? '');
      toast('Billing contact saved.');
    } catch (e) {
      if (e instanceof ApiException) {
        if (e.status === 401) toast('Sign in required.', 'error');
        else if (e.status === 403) toast('Admin access required.', 'error');
        else if (e.error.field) setContactErrors({ [e.error.field]: e.error.message });
        else toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      } else if (e instanceof TypeError || !navigator.onLine) toast('You are offline.', 'error');
      else toast(String(e), 'error');
    } finally { setContactSaving(false); }
  };

  const handleCancel = async () => {
    setCancelling(true);
    try {
      const res = await api.post<{ status: string; current_period_end: string; message?: string }>('/api/v1/billing/cancel');
      setSubscription((prev) => prev ? { ...prev, cancel_at_period_end: true, current_period_end: res.current_period_end ?? prev.current_period_end } : prev);
      toast(res.message ?? `Plan cancellation scheduled for ${formatPeriodEnd(res.current_period_end)}.`, 'info');
      setCancelOpen(false);
    } catch (e) {
      if (e instanceof ApiException) {
        if (e.status === 401) toast('Sign in required.', 'error');
        else if (e.status === 403) toast('Admin access required.', 'error');
        else toast(e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''), 'error');
      } else if (e instanceof TypeError || !navigator.onLine) toast('You are offline.', 'error');
      else toast(String(e), 'error');
    } finally { setCancelling(false); }
  };

  if (billingEnabled === null || (billingEnabled === true && loading && !error)) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner label="Loading billing" className="h-10 w-10" />
      </div>
    );
  }

  if (billingEnabled === false) {
    return (
      <EmptyState
        variant="empty"
        size="md"
        title="Billing not configured"
        description="Invoicing will be available when billing is enabled"
        hints={['Contact an owner to enable billing', 'Plans and invoices are hidden until configured']}
      />
    );
  }

  if (error) {
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.description}
        hints={error.hints}
        action={{ label: 'Retry', onClick: () => void fetchAll() }}
        size="md"
      />
    );
  }

  // when billing enabled but livePlans empty and loading done, still render
  return (
    <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        {!canManage && (
          <PermissionNote>
            Only the owner can change the plan, the payment method or the billing contact. Everything on this page stays readable.
          </PermissionNote>
        )}
        <Panel>
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-h4 font-bold tracking-tight text-slate-900">
                  {active.name} plan
                </h2>
                <StatusPill tone={cancelled ? 'warning' : 'success'} dot>
                  {cancelled ? `Ends ${periodEndLabel}` : 'Active'}
                </StatusPill>
              </div>
              <p className="mt-1 text-meta text-slate-500">
                {active.blurb} Renews {periodEndLabel}.
              </p>
            </div>
            <p className="text-right">
              <span className="block text-h3-lg font-bold tabular-nums text-slate-900">
                {active.price}
              </span>
              <span className="block text-micro text-slate-500">
                {active.cadence}
              </span>
            </p>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {usageToRender.map((item) => {
              const pct = item.limit > 0 ? Math.min(100, Math.round(item.used / item.limit * 100)) : 0;
              const near = pct >= 80;
              return (
                <div
                  key={item.label}
                  className="rounded-lg border border-slate-200 px-3 py-2.5">

                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-micro text-slate-500">{item.label}</span>
                    <span className="text-caption font-semibold tabular-nums text-slate-900">
                      {item.used.toLocaleString('en-US')}
                      <span className="font-normal text-slate-500">
                        {' '}
                        / {item.limit.toLocaleString('en-US')}
                        {item.unit}
                      </span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                    <div
                      className={[
                      'h-full rounded-full',
                      near ? 'bg-amber-500' : 'bg-brand-600'].
                      join(' ')}
                      style={{ width: `${pct}%` }} />

                  </div>
                </div>);

            })}
          </div>
        </Panel>

        <Panel
          title="Plans"
          icon={TrendingUpIcon}
          description="Change at any time. Upgrades take effect immediately, downgrades at the next renewal.">

          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
            {plansToRender.map((plan) => {
              const isCurrent = plan.id === currentPlanId;
              return (
                <div
                  key={plan.id}
                  className={[
                  'flex min-w-0 flex-col rounded-xl border p-3.5',
                  isCurrent ? 'border-brand-500 bg-brand-50/50' : 'border-slate-200'].
                  join(' ')}>

                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-body-sm font-bold text-slate-900">
                      {plan.name}
                    </h3>
                    {isCurrent && <StatusPill tone="success">Current</StatusPill>}
                  </div>
                  <p className="mt-1.5 text-lead font-bold tabular-nums text-slate-900">
                    {plan.price}
                    <span className="ml-1 text-fine font-medium text-slate-500">
                      {plan.cadence}
                    </span>
                  </p>
                  <ul className="mt-2.5 flex flex-1 flex-col gap-1.5">
                    {plan.features.map((feature) =>
                    <li
                      key={feature}
                      className="flex items-start gap-1.5 text-micro leading-relaxed text-slate-600">

                        <CheckIcon
                        className="mt-[3px] h-3 w-3 shrink-0 text-brand-600"
                        strokeWidth={2.6} />

                        {feature}
                      </li>
                    )}
                  </ul>
                  <button
                    type="button"
                    disabled={isCurrent || switching || !canManage}
                    title={canManage ? undefined : 'Only the owner can change the plan'}
                    onClick={() => setPendingPlan(plan.id)}
                    className={[
                    'mt-3 w-full rounded-lg px-3 py-2 text-meta font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2',
                    isCurrent || !canManage ?
                    'cursor-not-allowed border border-slate-200 text-slate-500' :
                    'bg-brand-700 text-white hover:bg-brand-800'].
                    join(' ')}>

                    {isCurrent ?
                    'Your plan' :
                    plan.id === 'enterprise' ?
                    'Talk to sales' :
                    `Switch to ${plan.name}`}
                  </button>
                </div>);

            })}
          </div>
        </Panel>

        <Panel
          title="Invoices"
          icon={ReceiptIcon}
          description="Every charge on this workspace.">

          {showInvoicesEmpty ?
          <EmptyState
            variant="empty"
            size="sm"
            title="No invoices yet"
            description="Your first invoice appears after the trial ends." /> :


          !hasInvoicesLive && invoicesToRender.length === 0 ?
          <EmptyState
            variant="empty"
            size="sm"
            title="No invoices yet"
            description="Your first invoice appears after the trial ends." /> :

          invoicesToRender.length === 0 ?
          <EmptyState
            size="sm"
            title="No invoices yet"
            description="Your first invoice appears after the trial ends." /> :


          <RecordList<any>
              stateKey="business-invoices"
              caption="Invoices"
              minWidth="min-w-[440px]"
              items={invoicesToRender}
              countNoun="invoices"
              rowKey={(invoice:any)=>String(invoice.id)}
              searchFields={(invoice:any)=> [invoice.id, invoice.period, invoice.amount, invoice.status].filter(Boolean).join(' ')}
              searchPlaceholder="Search invoices…"
              filters={[{ key:'status', label:'Status', match:(invoice:any,v:string)=> String(invoice.status ?? '')===v,
                options:[{value:'',label:'Any status'},{value:'paid',label:'Paid'},{value:'open',label:'Open'},{value:'void',label:'Void'}] }]}
              sortValue={(invoice:any,k:string)=> k==='amount' ? Number(String(invoice.amount ?? '').replace(/[^0-9.-]/g,'')) : k==='invoice' ? String(invoice.id ?? '') : String(invoice[k] ?? '')}
              columns={[
                { key:'invoice', label:'Invoice', primary:true, sortable:true, render:(invoice:any)=> <span className="font-mono text-micro text-slate-700">{invoice.id}</span> },
                { key:'period', label:'Period', sortable:true, render:(invoice:any)=> invoice.period },
                { key:'date', label:'Date', sortable:true, render:(invoice:any)=> invoice.date },
                { key:'amount', label:'Amount', sortable:true, render:(invoice:any)=> <span className="font-semibold tabular-nums text-slate-900">{invoice.amount}</span> },
                { key:'status', label:'Status', sortable:true, render:(invoice:any)=> <StatusPill tone={invoice.status === 'paid' ? 'success' : 'neutral'}>{plainStatus(invoice.status)}</StatusPill> },
                { key:'download', label:'', cellClassName:'pr-0', render:(invoice:any)=> (
                  <button
                    type="button"
                    aria-label={`Download ${invoice.id}`}
                    onClick={() => void handleDownload(invoice.id)}
                    className="tap-expand rounded-md p-1 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    <DownloadIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                  </button>) },
              ]}
            />
          }
        </Panel>
      </div>

      <div className="flex flex-col gap-3">
        <Panel title="Payment method" icon={CreditCardIcon}>
          {paymentLive ? (
          <div className="flex items-center gap-3 rounded-xl border border-slate-200 px-3 py-2.5">
            <span className="flex h-9 w-12 shrink-0 items-center justify-center rounded-md bg-slate-900 text-fine font-bold tracking-wide text-white">
              {paymentLive.brand.toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-meta font-semibold text-slate-900">
                •••• •••• •••• {paymentLive.last4}
              </span>
              <span className="block text-fine text-slate-500">Expires {String(paymentLive.exp_month).padStart(2,'0')} / {paymentLive.exp_year}</span>
            </span>
          </div>
          ) : (
          <EmptyState variant="empty" size="sm" title="No payment method" description="Add a card to keep your subscription active." />
          )}
          <button
            type="button"
            disabled={!canManage}
            title={canManage ? undefined : 'Only the owner can change the payment method'}
            onClick={() => setCardOpen(true)}
            className="mt-2.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-500 disabled:hover:bg-transparent">

            Update payment method
          </button>
          <p className="mt-2 text-fine leading-relaxed text-slate-500">
            Mobile money billing via MTN MoMo is available on Growth and above.
          </p>
        </Panel>

        <Panel title="Billing contact" description="Where invoices are sent.">
          <div className="flex flex-col gap-2.5">
            <Field id="bill-email" label="Email">
              <input
                id="bill-email"
                type="email"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="finance@demandiq.app"
                className={inputClass} />
              {contactErrors.email && <p className="mt-1 text-fine text-red-600">{contactErrors.email}</p>}
            </Field>
            <Field id="bill-tin" label="Tax ID (TIN)" hint="Printed on every invoice.">
              <input id="bill-tin" type="text" value={contactTin} onChange={(e) => setContactTin(e.target.value)} placeholder="102938475" className={inputClass} />
              {contactErrors.tin && <p className="mt-1 text-fine text-red-600">{contactErrors.tin}</p>}
            </Field>
            <button
              type="button"
              disabled={contactSaving || !canManage}
              title={canManage ? undefined : 'Only the owner can change the billing contact'}
              onClick={() => void handleSaveContact()}
              className="rounded-lg bg-brand-700 px-3 py-2 text-meta font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">

              {contactSaving ? 'Saving…' : 'Save contact'}
            </button>
          </div>
        </Panel>

        <Panel title="Cancel plan" description="Keeps your data, stops the billing.">
          <p className="text-micro leading-relaxed text-slate-600">
            Cancelling keeps your workspace open until the end of the current period.
            Forecasting stops after that, and your demand history stays exportable for
            30 days.
          </p>
          <button
            type="button"
            disabled={cancelled || cancelling || !canManage}
            title={canManage ? undefined : 'Only the owner can cancel the plan'}
            onClick={() => setCancelOpen(true)}
            className="mt-2.5 w-full rounded-lg border border-red-300 px-3 py-2 text-meta font-semibold text-red-600 transition-colors duration-150 ease-out hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-500">

            {cancelled ? 'Cancellation scheduled' : cancelling ? 'Cancelling…' : 'Cancel plan'}
          </button>
          {cancelled && <p className="mt-2 text-fine text-amber-600">Cancellation scheduled for {periodEndLabel}.</p>}
        </Panel>
      </div>

      <Modal
        open={Boolean(pendingPlan)}
        onClose={() => setPendingPlan(undefined)}
        title={
        pendingPlan === 'enterprise' ?
        'Talk to sales' :
        `Switch to ${plansToRender.find((p) => p.id === pendingPlan)?.name ?? ''}`
        }
        description={
        pendingPlan === 'enterprise' ?
        'We’ll reply within one business day.' :
        'Your next invoice is prorated automatically.'
        }
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => setPendingPlan(undefined)}>
              Cancel
            </ModalButton>
            <ModalButton
            onClick={() => void handleSwitch()}
            disabled={switching}>

              {switching ? 'Switching…' : pendingPlan === 'enterprise' ? 'Send request' : 'Confirm switch'}
            </ModalButton>
          </>
        }>

        <p className="text-meta leading-relaxed text-slate-600">
          {pendingPlan === 'enterprise' ?
          'Tell us roughly how many locations you run and which systems you need connected, and we’ll scope it with you.' :
          'Location limits apply immediately. If you are over the new limit, the extra locations become read-only until you remove them.'}
        </p>
      </Modal>

      <Modal
        open={cardOpen}
        onClose={() => { setCardOpen(false); setPmErrors({}); }}
        title="Update payment method"
        description="Card details are handled by our payment processor."
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => { setCardOpen(false); setPmErrors({}); }}>
              Cancel
            </ModalButton>
            <ModalButton
            onClick={() => void handleSavePayment()}
            disabled={pmSaving}>

              {pmSaving ? 'Saving…' : 'Save card'}
            </ModalButton>
          </>
        }>

        <div className="flex flex-col gap-3">
          <Field id="card-brand" label="Brand">
            <select id="card-brand" value={pmBrand} onChange={(e)=>setPmBrand(e.target.value)} className={inputClass}>
              <option value="visa">Visa</option>
              <option value="mastercard">Mastercard</option>
              <option value="amex">Amex</option>
              <option value="momo">MTN MoMo</option>
            </select>
            {pmErrors.brand && <p className="mt-1 text-fine text-red-600">{pmErrors.brand}</p>}
          </Field>
          <Field id="card-last4" label="Last 4 digits">
            <input id="card-last4" inputMode="numeric" maxLength={4} placeholder="4242" value={pmLast4} onChange={(e)=>setPmLast4(e.target.value.replace(/\D/g,'').slice(0,4))} className={inputClass} />
            {pmErrors.last4 && <p className="mt-1 text-fine text-red-600">{pmErrors.last4}</p>}
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="card-exp-month" label="Exp month (1-12)">
              <input id="card-exp-month" inputMode="numeric" placeholder="9" value={pmExpMonth} onChange={(e)=>setPmExpMonth(e.target.value.replace(/\D/g,'').slice(0,2))} className={inputClass} />
              {pmErrors.exp_month && <p className="mt-1 text-fine text-red-600">{pmErrors.exp_month}</p>}
            </Field>
            <Field id="card-exp-year" label="Exp year (2024-2040)">
              <input id="card-exp-year" inputMode="numeric" placeholder="2028" value={pmExpYear} onChange={(e)=>setPmExpYear(e.target.value.replace(/\D/g,'').slice(0,4))} className={inputClass} />
              {pmErrors.exp_year && <p className="mt-1 text-fine text-red-600">{pmErrors.exp_year}</p>}
            </Field>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Cancel your plan?"
        body={`Billing stops at the end of the current period on ${periodEndLabel}. Forecasting stops then too, and your history stays exportable for 30 days.`}
        confirmLabel={cancelling ? 'Cancelling…' : 'Cancel plan'}
        tone="danger"
        onConfirm={() => void handleCancel()} />

    </div>);

}
