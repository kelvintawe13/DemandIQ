import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  WrenchIcon,
  XIcon,
  ShieldCheckIcon,
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  FilterIcon,
  RefreshCwIcon,
  ScanSearchIcon,
  TrendingUpIcon,
  CopyIcon,
  ClockIcon,
  FileWarningIcon,
  EyeIcon,
  type LucideIcon,
} from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { Toggle } from '../components/ui/Toggle';
import { PageSkeleton } from '../components/ui/Skeleton';
import { StatusPill } from '../components/ui/StatusPill';
import { StatCard, type StatTone } from '../components/ui/StatCard';
import { EmptyState } from '../components/ui/EmptyState';
import { Modal } from '../components/ui/Modal';
import { PermissionNote } from '../components/ui/PermissionNote';
import { AnimatedTitle } from '../components/ui/AnimatedTitle';
import { FilterBar } from '../components/ui/FilterBar';
import { Pagination } from '../components/ui/Pagination';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { SeverityDonut } from '../components/quality/SeverityDonut';
import { CountChart } from '../components/ui/CountChart';
import { useListState, type FilterDef } from '../hooks/useListState';
import { api, ApiException } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { plainStatus, qualityDimensionHint, qualityDimensionLabel } from '../lib/plainLanguage';

/** Sort order for severity: a critical issue outranks an info one. */
const SEVERITY_RANK: Record<string, number> = { critical: 3, warning: 2, info: 1 };

/**
 * How tall the issues list grows before it scrolls internally.
 *
 * Eight two-line rows made this panel roughly twice the height of the analysis
 * beside it, so the page was mostly one list. `ResponsiveTable`'s `maxHeight`
 * pins the header and caps the box at about five rows, so the panel is the same
 * size whether there are three open issues or thirty.
 */
const ISSUE_LIST_MAX_HEIGHT = 'max-h-[380px]';

/**
 * Row actions, spelled out.
 *
 * These were icon-only for a while, because the three labels are ~254px of the
 * ~404px this panel gets when the assistant rail is docked. Words were asked
 * for back, so they are back: the trade is that the row now scrolls sideways at
 * that width instead of the column being pinned. Closing the rail (or the
 * sidebar) gives the panel ~706px, which fits the whole row with no scroll.
 */
const ROW_ACTION =
  'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 text-fine font-semibold transition-colors disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';
const ROW_ACTION_PRIMARY = `${ROW_ACTION} bg-brand-700 text-white hover:bg-brand-800`;
const ROW_ACTION_QUIET = `${ROW_ACTION} border border-slate-300 text-slate-700 hover:bg-slate-50`;

/**
 * The check that raised an issue, as a name.
 *
 * `GET /quality/issues` answers with `rule_id` and nothing else — there is no
 * `rule_name` on the wire — so the id is resolved through the rule list this
 * page already loads. Falling back to the id itself would put a UUID on the
 * "which checks are firing" axis, which is the same defect as printing a
 * machine token anywhere else on the screen.
 */
function checkNameOf(
  issue: Record<string, any>,
  ruleNames: Record<string, string>,
): string {
  const id = issue.rule_id ? String(issue.rule_id) : '';
  return (id && ruleNames[id]) || issue.rule_name || 'Unlabelled check';
}

/** Presentation metadata for one known rule — keys stay machine tokens for a11y/tests. */
interface RuleMeta {
  dimension: string;
  when: string;
  icon: LucideIcon;
  blurb?: string;
}

const RULE_META: Record<string, RuleMeta> = {
  gap_detection: {
    dimension: 'completeness',
    when: 'Every import',
    icon: ScanSearchIcon,
    blurb: 'Finds missing days in a demand series',
  },
  spike_detection: {
    dimension: 'accuracy',
    when: 'Every import',
    icon: TrendingUpIcon,
    blurb: 'Flags implausible jumps in daily counts',
  },
  duplicate_conflict: {
    dimension: 'consistency',
    when: 'Every import',
    icon: CopyIcon,
    blurb: 'Catches the same day counted twice across sources',
  },
  stale_connector: {
    dimension: 'timeliness',
    when: 'Hourly',
    icon: ClockIcon,
    blurb: 'Warns when a connector goes quiet',
  },
  schema_drift: {
    dimension: 'consistency',
    when: 'On upload',
    icon: FileWarningIcon,
    blurb: 'Detects column or type changes in uploads',
  },
};

function ruleDisplayName(name?: string | null): string {
  return plainStatus(name);
}

function errMeta(e: unknown) {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error' as const, title: 'Sign in required', desc: e.error.message };
    if (e.status === 403) return { variant: 'error' as const, title: 'Admin access required', desc: e.error.message };
    return {
      variant: 'error' as const,
      title: 'Failed to load quality',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  if (e instanceof TypeError) return { variant: 'offline' as const, title: 'You are offline', desc: 'Check your connection' };
  return { variant: 'offline' as const, title: 'Network error', desc: String(e) };
}

function scoreTone(score: number): 'success' | 'warning' | 'critical' {
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'critical';
}

function scoreColor(score: number): string {
  if (score >= 90) return '#0f6d67';
  if (score >= 70) return '#d97706';
  return '#dc2626';
}

/** The same three bands as `scoreColor`, as classes — an HTML bar, not an SVG one. */
function scoreBarClass(score: number): string {
  if (score >= 90) return 'bg-brand-700';
  if (score >= 70) return 'bg-amber-500';
  return 'bg-red-500';
}

function severityTone(severity: string): 'critical' | 'warning' | 'info' | 'neutral' {
  if (severity === 'critical') return 'critical';
  if (severity === 'warning') return 'warning';
  if (severity === 'info') return 'info';
  return 'neutral';
}

function ageLabel(iso?: string | null): string {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/**
 * One row per quality dimension: label, score, a bar against the healthy 90, and
 * the plain-language hint.
 *
 * This used to be a hand-built SVG with the 70/90 thresholds drawn as dashed
 * guides. The bar, the score and the hint are HTML now, which keeps the hint in
 * the accessibility tree as real text (it was SVG `<text>` before) and matches
 * the progress-bar idiom the forecast table already uses for its confidence bar.
 */
function DimensionBars({
  dims,
}: {
  dims: Array<{ key: string; label: string; score: number; hint: string }>;
}) {
  if (dims.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-meta text-slate-500">
        Nothing scored yet. Import demand data to run these checks.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-3.5">
      {dims.map((d) => {
        const gap = d.score - 90;
        return (
          <li key={d.key}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-meta font-semibold text-slate-800">{d.label}</span>
              <span className="flex items-baseline gap-1.5">
                <span className="tabular-nums text-meta font-bold text-slate-900">{d.score}</span>
                <span className={`text-fine ${gap >= 0 ? 'text-emerald-700' : 'text-amber-700'}`}>
                  {gap >= 0 ? `+${gap}` : gap} vs healthy
                </span>
              </span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${scoreBarClass(d.score)}`}
                style={{ width: `${Math.max(2, Math.min(100, d.score))}%` }}
              />
            </div>
            <p className="mt-1 text-fine text-slate-500">{d.hint}</p>
          </li>
        );
      })}
    </ul>
  );
}
/**
 * One validation rule, as a card rather than a table row.
 *
 * The four-column table said the same thing five times: every row carried the
 * same coverage, and the one cell that varied was the switch — so the section
 * read as a settings grid when it should explain what is being checked. A card
 * answers the three questions someone deciding whether to keep a rule on
 * actually has: what does it check, when does it run, and what has it caught.
 *
 * The switch keeps the raw machine token (`gap_detection`) as its accessible
 * name — that is the contract RULE_META and the tests key off — and only the
 * visible text is humanised.
 */
function RuleCard({
  rule,
  meta,
  findings,
  canConfigure,
  busy,
  onToggle,
}: {
  rule: Record<string, any>;
  meta?: RuleMeta;
  findings: number;
  canConfigure: boolean;
  busy: boolean;
  /**
   * Given the value the switch should move *to*.
   *
   * `Toggle` reports the next value precisely so callers do not invert it
   * themselves — and `toggleRule` writes what it is given. Handing it the
   * current value instead made every click a no-op: the switch flicked back and
   * the PATCH re-sent the state it was already in.
   */
  onToggle: (next: boolean) => void;
}) {
  // The API answers with `name`; `rule_key` is the older spelling and never
  // resolved here, which is why every rule used to fall back to its own
  // description and to "On import" regardless of when it actually runs.
  const token = String(rule.name ?? rule.rule_key ?? rule.key ?? rule.label ?? rule.id);
  const on = Boolean(rule.enabled);
  const Icon = meta?.icon ?? ShieldCheckIcon;
  const when = rule.coverage ?? rule.schedule ?? meta?.when ?? 'On import';
  const name = ruleDisplayName(token);

  return (
    <li
      className={[
        'flex min-w-0 flex-col gap-2 rounded-xl border p-3.5 transition-colors',
        on ? 'border-slate-200 bg-white' : 'border-dashed border-slate-300 bg-slate-50/70',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-start gap-2.5">
          <span
            aria-hidden="true"
            className={[
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
              on ? 'bg-brand-50 text-brand-700' : 'bg-slate-100 text-slate-500',
            ].join(' ')}
          >
            <Icon className="h-4 w-4" strokeWidth={1.9} />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-control font-semibold text-slate-900" title={name}>
              {name}
            </span>
            {/*
              The dimension is spelled "Completeness check" rather than the bare
              label, so the same word never appears twice on the page — once in
              the dimension analysis panel above and once here.
            */}
            <span className="mt-0.5 block truncate text-fine text-slate-500">
              {meta ? `${qualityDimensionLabel(meta.dimension)} check` : 'Quality check'}
            </span>
          </span>
        </span>
        <Toggle
          checked={on}
          onChange={onToggle}
          label={token}
          disabled={!canConfigure || busy}
          title={canConfigure ? undefined : 'Your seat cannot change validation rules'}
        />
      </div>

      <p className="text-caption leading-relaxed text-slate-600">
        {rule.description ?? meta?.blurb ?? 'No description provided for this rule.'}
      </p>

      <div className="mt-auto flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-t border-slate-100 pt-2.5">
        <span className="inline-flex items-center gap-1.5 text-fine text-slate-500">
          <ClockIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
          {when}
        </span>
        <StatusPill tone={on ? 'success' : 'neutral'} dot>
          {on ? 'On' : 'Paused'}
        </StatusPill>
        <span className="text-fine text-slate-500">
          {findings > 0 ? `${findings} open ${findings === 1 ? 'issue' : 'issues'}` : 'Nothing open'}
        </span>
      </div>
    </li>
  );
}

export function QualityMonitor(_props: { onNavigate?: (id: string) => void }) {
  const [score, setScore] = useState<{ overall: number; dimensions: Record<string, number>; open_issues?: number } | null>(null);
  const [issues, setIssues] = useState<any[]>([]);
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta> | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loaded, setLoaded] = useState(false);
  const [locationNames, setLocationNames] = useState<Record<string, string>>({});
  const [ruleBusyId, setRuleBusyId] = useState<string | null>(null);
  const [scannedAt, setScannedAt] = useState<Date | null>(null);
  const { toast } = useToast();
  const { user } = useSession();
  const canConfigure = (user.permissions ?? []).includes('quality:configure');
  const circumference = 2 * Math.PI * 42;

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sRes, iRes, rRes, lRes] = await Promise.all([
        api.get<{ overall: number; dimensions: Record<string, number>; open_issues: number }>('/api/v1/quality/score'),
        api.get<{ data: any[] }>('/api/v1/quality/issues', { limit: 20 }),
        api.get<{ data: any[] }>('/api/v1/quality/rules'),
        api.get<{ data: any[] }>('/api/v1/locations', { limit: 100 }).catch((e) => {
          console.warn('quality: could not load location names', e);
          return { data: [] } as any;
        }),
      ]);
      setScore({ overall: sRes.overall, dimensions: sRes.dimensions, open_issues: sRes.open_issues });
      setIssues(iRes.data ?? []);
      setRules(rRes.data ?? []);
      setLocationNames(
        Object.fromEntries(
          (lRes.data ?? []).map((l: any) => [String(l.id), l.name ?? l.location_name ?? String(l.id).slice(0, 8)]),
        ),
      );
      setScannedAt(new Date());
    } catch (e) {
      setError(errMeta(e));
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const applyFix = async (id: string) => {
    setActionLoading(id);
    try {
      await api.post(`/api/v1/quality/issues/${id}/apply-fix`);
      toast('Fix applied');
      void fetchAll();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast(String(e), 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const dismiss = async (id: string) => {
    setActionLoading(id);
    try {
      await api.post(`/api/v1/quality/issues/${id}/dismiss`);
      toast('Issue dismissed', 'info');
      void fetchAll();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  /**
   * Sets a rule's state to `nextEnabled` — it does **not** invert what it is
   * given. Named `setRuleEnabled` rather than `toggleRule` on purpose: `Alerts`
   * has a `toggleRule(id, current)` that flips the value itself, and the two
   * contracts sharing one name is how this one got handed the value the switch
   * was already showing.
   */
  const setRuleEnabled = async (id: string, nextEnabled: boolean) => {
    const previous = rules;
    setRuleBusyId(id);
    // Optimistic — the control should feel instant; refetch confirms.
    setRules((cur) => cur.map((r) => (r.id === id ? { ...r, enabled: nextEnabled } : r)));
    try {
      await api.patch(`/api/v1/quality/rules/${id}`, { enabled: nextEnabled });
      toast(nextEnabled ? 'Rule enabled' : 'Rule paused', nextEnabled ? 'success' : 'info');
    } catch (e) {
      setRules(previous);
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setRuleBusyId(null);
    }
  };


  const displayDims = useMemo(
    () =>
      score
        ? Object.entries(score.dimensions)
            .map(([key, value]) => ({
              key,
              label: qualityDimensionLabel(key),
              score: Math.round(value),
              hint: qualityDimensionHint(key),
            }))
            .sort((a, b) => a.score - b.score)
        : [],
    [score],
  );

  const severityCounts = useMemo(() => {
    const counts = { critical: 0, warning: 0, info: 0 };
    for (const issue of issues) {
      const s = String(issue.severity ?? 'info').toLowerCase();
      if (s === 'critical') counts.critical += 1;
      else if (s === 'warning') counts.warning += 1;
      else counts.info += 1;
    }
    return counts;
  }, [issues]);

  /** Rule id → name, so an issue can name the check that raised it. */
  const ruleNames = useMemo(
    () =>
      Object.fromEntries(
        rules.map((rule: any) => [String(rule.id), String(rule.name ?? rule.label ?? rule.id)]),
      ),
    [rules],
  );

  /*
    Chart inputs. Each is derived from the same issue list and location names
    the table below uses, so the panels cannot disagree with the rows.
  */
  const issuesByCheck = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of issues) {
      const key = ruleDisplayName(checkNameOf(issue, ruleNames));
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].
    map(([label, count]) => ({ label, count })).
    sort((a, b) => b.count - a.count).
    slice(0, 6);
  }, [issues, ruleNames]);

  /**
   * Open issues per rule, keyed by the rule's machine name.
   *
   * The rules panel lists what each check *does*; this says what it has actually
   * caught, so a rule that is on but silent and a rule that is on and screaming
   * stop looking identical. Keyed by name because that is the one identifier the
   * issues list (`rule_id`) and the rule list (`name`) both resolve to.
   */
  const findingsByRule = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of issues) {
      const key = checkNameOf(issue, ruleNames);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [issues, ruleNames]);

  const issuesByLocation = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of issues) {
      const key = issue.location_id ?
      locationNames[String(issue.location_id)] ?? `location ${String(issue.location_id).slice(0, 8)}` :
      'All locations';
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].
    map(([name, count]) => ({
      // The axis is 110px wide; a full restaurant name would be clipped, and the
      // tooltip still carries the whole thing in the table below.
      label: name.length > 18 ? `${name.slice(0, 17)}…` : name,
      count,
    })).
    sort((a, b) => b.count - a.count).
    slice(0, 6);
  }, [issues, locationNames]);

  const issueAgeBuckets = useMemo(() => {
    // A backlog that is not moving is what a quality screen should surface
    // before anyone stops trusting the score.
    const buckets = [
    { label: 'Under 1 day', count: 0 },
    { label: '1–3 days', count: 0 },
    { label: '3–7 days', count: 0 },
    { label: 'Over a week', count: 0 }];

    for (const issue of issues) {
      const at = issue.created_at ? new Date(issue.created_at).getTime() : Number.NaN;
      const days = (Date.now() - at) / 86_400_000;
      const index = Number.isNaN(days) ? 3 : days < 1 ? 0 : days < 3 ? 1 : days < 7 ? 2 : 3;
      buckets[index].count += 1;
    }
    return buckets;
  }, [issues]);

  /*
    The issues table's toolbar. Search and named filters come from the shared
    list state, so this table behaves like every other list in the app.
  */
  const issueFilters = useMemo<FilterDef<Record<string, any>>[]>(() => {
    // Grouped by name, not by rule id: two rules can share a name (there are
    // two `gap_detection` rows), and an operator thinks in checks, not ids.
    const checks = [...new Set(issues.map((issue) => checkNameOf(issue, ruleNames)))];
    return [
    {
      key: 'severity',
      label: 'Severity',
      options: [
      { value: '', label: 'Every severity' },
      { value: 'critical', label: 'Critical' },
      { value: 'warning', label: 'Warning' },
      { value: 'info', label: 'Info' }],

      match: (issue, value) => String(issue.severity ?? 'info').toLowerCase() === value
    },
    {
      key: 'check',
      label: 'Check',
      options: [
      { value: '', label: 'Every check' },
      ...checks.filter(Boolean).map((check) => ({ value: check, label: ruleDisplayName(check) }))],

      match: (issue, value) => checkNameOf(issue, ruleNames) === value
    },
    {
      key: 'location',
      label: 'Location',
      options: [
      { value: '', label: 'Every location' },
      { value: 'all', label: 'All locations' },
      ...Object.entries(locationNames).map(([id, name]) => ({ value: id, label: name }))],

      match: (issue, value) =>
      value === 'all' ? !issue.location_id : String(issue.location_id ?? '') === value
    }];
  }, [issues, ruleNames, locationNames]);

  const issueList = useListState<Record<string, any>>({
    key: 'quality-issues',
    items: issues,
    filters: issueFilters,
    searchFields: (issue) =>
    [
    issue.summary,
    issue.title,
    issue.suggested_fix,
    issue.suggestion,
    issue.severity,
    issue.created_at,
    checkNameOf(issue, ruleNames),
    issue.location_id ? locationNames[String(issue.location_id)] : 'All locations'].
    filter(Boolean).
    join(' '),
    sortValue: (issue, key) => {
      if (key === 'severity') return SEVERITY_RANK[String(issue.severity ?? 'info').toLowerCase()] ?? 1;
      if (key === 'age') return issue.created_at ? new Date(issue.created_at).getTime() : 0;
      return String(issue.summary ?? issue.title ?? '');
    },
    defaultSort: { key: 'severity', dir: 'desc' },
    pageSize: 8
  });

  const weakest = displayDims[0];
  const rulesOn = rules.filter((r) => r.enabled).length;
  const SCORE = score?.overall ?? 0;

  const issueColumns: Column<any>[] = useMemo(
    () => [
      {
        key: 'severity',
        label: 'Severity',
        sortable: true,
        render: (issue) => (
          <StatusPill tone={severityTone(String(issue.severity ?? 'info'))}>
            {issue.severity ?? 'info'}
          </StatusPill>
        ),
      },
      {
        key: 'issue',
        label: 'Issue',
        primary: true,
        sortable: true,
        render: (issue) => (
          <span className="min-w-0">
            <span className="block truncate text-meta font-semibold text-slate-900">
              {issue.summary ?? issue.title}
            </span>
            <span className="block truncate text-fine text-slate-500">
              {issue.suggested_fix ?? issue.suggestion ?? 'No suggested fix'}
            </span>
          </span>
        ),
      },
      {
        key: 'location',
        label: 'Location',
        render: (issue) => (
          <span className="text-caption text-slate-700">
            {issue.location_id
              ? locationNames[String(issue.location_id)] ?? `location ${String(issue.location_id).slice(0, 8)}`
              : 'All locations'}
          </span>
        ),
      },
      {
        key: 'age',
        label: 'Age',
        sortable: true,
        render: (issue) => (
          <span className="tabular-nums text-caption text-slate-600">{ageLabel(issue.created_at)}</span>
        ),
      },
      {
        key: 'actions',
        label: '',
        // `whitespace-nowrap` is load-bearing. With a wrapping flex row the
        // cell's widest content is *one* button, so the table sized the column
        // to 95px and then stacked the three buttons over three lines — 156px
        // per row, which is what made this panel twice the height of the
        // analysis beside it. The labels wrap sideways now, never vertically.
        cellClassName: 'whitespace-nowrap',
        render: (issue) => (
          <span className="flex items-center justify-end gap-1.5">
            <button
              type="button"
              aria-label="Apply fix"
              title="Apply fix"
              disabled={actionLoading === issue.id}
              onClick={() => void applyFix(issue.id)}
              className={ROW_ACTION_PRIMARY}
            >
              <WrenchIcon className="h-3.5 w-3.5" strokeWidth={2} />
              Apply fix
            </button>
            <button
              type="button"
              aria-label="Dismiss"
              title="Dismiss"
              disabled={actionLoading === issue.id}
              onClick={() => void dismiss(issue.id)}
              className={ROW_ACTION_QUIET}
            >
              <XIcon className="h-3.5 w-3.5" strokeWidth={2} />
              Dismiss
            </button>
            <button
              type="button"
              aria-label="View details"
              title="View details"
              onClick={() => setDetail(issue)}
              className={ROW_ACTION_QUIET}
            >
              <EyeIcon className="h-3.5 w-3.5" strokeWidth={2} />
              Details
            </button>
          </span>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [actionLoading, locationNames],
  );

  if (loading && !loaded) return <PageSkeleton label="Loading quality data" />;
  if (error) {
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.desc}
        action={{ label: 'Retry', onClick: () => void fetchAll() }}
        size="md"
      />
    );
  }

  const healthLabel = SCORE >= 90 ? 'Healthy' : SCORE >= 70 ? 'Watch' : 'At risk';

  return (
    <>
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <AnimatedTitle as="h1" variant="words" className="text-h2 tracking-tight text-slate-900 sm:text-display-sm">
            Quality Monitor
          </AnimatedTitle>
          <p className="animate-subtitle-in mt-1.5 max-w-xl text-body-xs text-slate-500">
            Analytical view of freshness, completeness and consistency. Catch bad data before it reaches the forecast.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <p className="text-micro text-slate-500">
            {scannedAt ? `Last scan ${scannedAt.toLocaleTimeString()}` : 'Scan pending'}
          </p>
          <button
            type="button"
            onClick={() => void fetchAll()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 bg-white px-3 py-2 text-caption font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <RefreshCwIcon className={['h-3.5 w-3.5', loading ? 'animate-spin' : ''].join(' ')} strokeWidth={2} />
            Rescan
          </button>
        </div>
      </div>

      {/* KPI strip — the same StatCard tile the forecast and console screens use. */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          {
            label: 'Overall score',
            value: String(SCORE),
            detail: healthLabel,
            icon: ActivityIcon,
            tone: scoreTone(SCORE),
          },
          {
            label: 'Open issues',
            value: String(issues.length),
            detail: `${severityCounts.critical} critical`,
            icon: AlertTriangleIcon,
            tone: issues.length === 0 ? 'success' : severityCounts.critical > 0 ? 'critical' : 'warning',
          },
          {
            label: 'Weakest check',
            value: weakest ? String(weakest.score) : '—',
            detail: weakest ? 'Lowest vs healthy (≥90)' : 'No data',
            icon: FilterIcon,
            tone: weakest ? scoreTone(weakest.score) : 'neutral',
          },
          {
            label: 'Rules active',
            value: `${rulesOn}/${rules.length}`,
            detail: canConfigure ? 'Configurable' : 'Read-only',
            icon: ShieldCheckIcon,
            tone: rulesOn === rules.length && rules.length > 0 ? 'success' : 'warning',
          },
          {
            label: 'Passing checks',
            value: String(displayDims.filter((d) => d.score >= 90).length),
            detail: `of ${displayDims.length || '—'} scored`,
            icon: CheckCircle2Icon,
            tone: 'success',
          },
        ].map((kpi) => (
          <StatCard
            key={kpi.label}
            label={kpi.label}
            value={kpi.value}
            sub={kpi.detail}
            icon={kpi.icon}
            tone={kpi.tone as StatTone} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1.35fr)]">
        {/* Score + dimensions */}
        <Panel title="Dimension analysis" description="Each check against healthy (≥90) and watch (≥70) thresholds.">
          <div className="mb-4 flex items-center gap-4 rounded-2xl border border-slate-200 bg-slate-50/60 px-3.5 py-3">
            <div className="relative h-[96px] w-[96px] shrink-0">
              <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
                <circle cx="50" cy="50" r="42" fill="none" stroke="#e2e8f0" strokeWidth="9" />
                <circle
                  cx="50"
                  cy="50"
                  r="42"
                  fill="none"
                  stroke={scoreColor(SCORE)}
                  strokeWidth="9"
                  strokeLinecap="round"
                  strokeDasharray={`${(circumference * SCORE) / 100} ${circumference}`}
                  className="transition-[stroke-dasharray] duration-700 ease-out"
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="font-display text-stat-md leading-none tracking-tight text-slate-900">{SCORE}</span>
                <span className="mt-0.5 text-fine font-semibold uppercase tracking-wide text-slate-500">score</span>
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-control font-semibold text-slate-900">Composite health</p>
              <p className="mt-1 text-caption leading-relaxed text-slate-500">
                Built from the checks below, updated as imports land. Use the bars to see which check is dragging the score.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <StatusPill tone={scoreTone(SCORE)} dot>
                  {healthLabel}
                </StatusPill>
                <span className="text-fine text-slate-500">
                  · {issues.length} open · {rulesOn}/{rules.length} rules on
                </span>
              </div>
            </div>
          </div>

          <DimensionBars dims={displayDims} />

          {/*
            The severity mix moved to its own gallery panel as a donut. It was a
            hand-rolled stacked bar here, which said the same thing twice as
            loudly once the donut existed.
          */}
        </Panel>

        {/* Issues table */}
        <Panel
          title="Open issues"
          description="Sorted by impact on your forecast."
          action={
          <span className="text-caption text-slate-500">
              {issueList.filtered !== issueList.total ?
            `${issueList.filtered} of ${issueList.total}` :
            `${issueList.total} shown`}
            </span>
          }>
          
          {/* The severity buttons were replaced by the shared toolbar, so a
              severity, a check and a location are all one chip away. */}
          <FilterBar<Record<string, any>>
            search={issueList.search}
            onSearch={issueList.setSearch}
            searchPlaceholder="Search issues…"
            filters={issueFilters}
            values={issueList.filterValues}
            onFilterChange={issueList.setFilter}
            onClear={issueList.clearFilters}
            activeCount={issueList.activeFilterCount} />
          

          <div className="mt-3">
            {issueList.filtered === 0 ?
            <EmptyState
              variant="empty"
              size="md"
              title={issues.length === 0 ? 'No open issues, all checks passed' : 'No issues match these filters'}
              description={
              issues.length === 0 ?
              'No outstanding quality issues across your sources.' :
              'Clear a filter to bring the issues back.'
              }
              hints={issues.length === 0 ? ['All validation rules passed', 'Recent imports were clean'] : undefined}
              action={
              issueList.activeFilterCount > 0 ?
              { label: 'Clear filters', onClick: issueList.clearFilters } :
              undefined
              } /> :

            <ResponsiveTable
              columns={issueColumns}
              rows={issueList.pageRows}
              rowKey={(issue) => String(issue.id)}
              caption="Open quality issues"
              minWidth="min-w-[440px]"
              sort={issueList.sort}
              onSort={issueList.setSortKey}
              maxHeight={ISSUE_LIST_MAX_HEIGHT} />

            }
          </div>

          <Pagination
            page={issueList.page}
            pageCount={issueList.pageCount}
            onPage={issueList.setPage}
            total={issueList.filtered}
            from={issueList.from}
            to={issueList.to}
            noun="issues"
            pageSize={issueList.pageSize}
            pageSizeOptions={[5, 8, 20]}
            onPageSize={issueList.setPageSize} />
          
        </Panel>
      </div>

      {/*
        The quality picture, four ways. Each panel reads the issue list and the
        location names already on the page, so the charts cannot disagree with
        the table above them.
      */}
      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel
          title="Issue severity mix"
          description="How the open issues divide by how much they hurt.">
          
          <SeverityDonut mix={severityCounts} />
        </Panel>

        <Panel
          title="Which checks are firing"
          description="The validation checks finding the most problems right now.">
          
          {/* Horizontal: up to six check names (`Spike detection`,
              `Duplicate conflict`, `Stale connector`) do not fit across a
              phone's upright x-axis. Measured at 320px all four consecutive
              pairs collided and `Gap detection` was clipped outside the svg;
              three pairs still collided at 414px. As rows each label gets its
              own band. */}
          <CountChart
            data={issuesByCheck}
            layout="bars"
            noun="open issues"
            categoryLabel="Check"
            emptyTitle="No issues to break down"
            emptyDescription="Every check is passing, so there is nothing to group here." />
          
        </Panel>

        <Panel
          title="Issues by location"
          description="Where the problems are concentrated.">
          
          <CountChart
            data={issuesByLocation}
            layout="bars"
            noun="open issues"
            categoryLabel="Restaurant"
            emptyTitle="No issues by location"
            emptyDescription="Nothing is failing, so no site is carrying more than its share." />
          
        </Panel>

        <Panel
          title="How long issues have been open"
          description="A backlog that is not moving shows up here first.">
          
          <CountChart
            data={issueAgeBuckets}
            noun="open issues"
            categoryLabel="Age"
            emptyTitle="No issues to age"
            emptyDescription="Nothing has been sitting open, so there is no backlog to show." />
          
        </Panel>
      </div>

      {/* Rules */}
      <Panel
        className="mt-3"
        title="Validation rules"
        icon={ShieldCheckIcon}
        description="Rules run automatically before data reaches the forecast. Each card shows when it runs and what it has caught."
        action={
          rules.length > 0 ? (
            <span className="text-caption text-slate-500">
              {rulesOn} of {rules.length} running
            </span>
          ) : undefined
        }
      >
        {rules.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="No rules"
            description="No validation rules configured."
            hints={['Validation rules belong to owners and administrators']}
          />
        ) : (
          /*
            A card per rule rather than a row: the grid is what makes the
            section scannable, and the icon, the dimension it feeds, the cadence
            and the open-finding count are all things a table cell would have
            had to truncate.
          */
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {rules.map((rule) => {
              const token = String(
                rule.name ?? rule.rule_key ?? rule.key ?? rule.label ?? rule.id,
              );
              return (
                <RuleCard
                  key={String(rule.id)}
                  rule={rule}
                  meta={RULE_META[token]}
                  findings={findingsByRule.get(token) ?? 0}
                  canConfigure={canConfigure}
                  busy={ruleBusyId === rule.id}
                  onToggle={(next) => void setRuleEnabled(rule.id, next)}
                />
              );
            })}
          </ul>
        )}
        {!canConfigure && (
          <PermissionNote className="mt-3">
            Your seat can see which validation rules run but not change them. Rule configuration belongs to owners and
            administrators.
          </PermissionNote>
        )}
      </Panel>

      <Modal
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        title={detail?.summary ?? detail?.title ?? 'Quality issue'}
        description={
          detail
            ? `${detail.severity ?? 'warning'} · detected ${detail.created_at ? new Date(detail.created_at).toLocaleString() : '—'}`
            : undefined
        }
      >
        {detail && (
          <div className="flex flex-col gap-4 text-meta text-slate-700">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {(
                [
                  ['Severity', detail.severity ?? 'warning'],
                  [
                    'Location',
                    detail.location_id
                      ? locationNames[String(detail.location_id)] ?? String(detail.location_id)
                      : 'All locations',
                  ],
                  ['Detected', detail.created_at ? new Date(detail.created_at).toLocaleString() : '—'],
                  ['Rule', ruleDisplayName(checkNameOf(detail, ruleNames))],
                  ['Status', detail.resolved_at ? 'Resolved' : detail.dismissed_at ? 'Dismissed' : 'Open'],
                  ['Rows affected', detail.affected_rows ?? detail.rows ?? '—'],
                ] as [string, string][]
              ).map(([label, value]) => (
                <div key={label}>
                  <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="break-words text-slate-800">{value}</dd>
                </div>
              ))}
            </dl>
            {(detail.suggested_fix ?? detail.suggestion) && (
              <div>
                <p className="mb-1 text-fine font-semibold uppercase tracking-wide text-slate-500">Suggested fix</p>
                <p className="rounded-lg bg-slate-50 px-2.5 py-2 leading-relaxed">
                  {detail.suggested_fix ?? detail.suggestion}
                </p>
              </div>
            )}
            {detail.evidence && (
              <div>
                <p className="mb-1 text-fine font-semibold uppercase tracking-wide text-slate-500">Evidence</p>
                <pre className="overflow-x-auto rounded-lg bg-slate-50 p-2.5 text-fine">
                  {JSON.stringify(detail.evidence, null, 2)}
                </pre>
              </div>
            )}
            <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-3">
              <button
                type="button"
                onClick={async () => {
                  await applyFix(detail.id);
                  setDetail(null);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3.5 py-2 text-meta font-semibold text-white transition-colors hover:bg-brand-800"
              >
                <WrenchIcon className="h-3.5 w-3.5" strokeWidth={2} />
                Apply fix
              </button>
              <button
                type="button"
                onClick={async () => {
                  await dismiss(detail.id);
                  setDetail(null);
                }}
                className="rounded-lg border border-slate-300 px-3.5 py-2 text-meta font-semibold text-slate-700 transition-colors hover:bg-slate-50"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
