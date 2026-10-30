import {
  HomeIcon,
  BarChart3Icon,
  GaugeIcon,
  DownloadIcon,
  LayersIcon,
  SearchCheckIcon,
  BellIcon,
  FileTextIcon,
  SettingsIcon,
  BrainCircuitIcon,
  FolderSearchIcon,
  FlaskConicalIcon,
  PlugZapIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  ActivityIcon,
  MapPinIcon,
  GitPullRequestIcon,
  WebhookIcon,
  UserIcon,
  CreditCardIcon,
  MonitorIcon,
  CpuIcon,
  SlidersHorizontalIcon,
  TargetIcon,
  SparklesIcon } from
'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface PageMeta {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  footerLeft: string;
  footerRight: string;
}

export const pageMeta: Record<string, PageMeta> = {
  overview: {
    title: 'Overview',
    subtitle: 'Where demand stands today and what needs your attention.',
    icon: HomeIcon,
    footerLeft: 'Live data · last ingest 12 minutes ago',
    // There is no single "model vN" to name here: the served export is whatever
    // this deployment loads, and it is published on the Model Registry and on
    // every forecast row. A frozen version string in a static map goes stale
    // silently, which is what "Forecast model v4.2" had done.
    footerRight: 'Model version is shown on every forecast'
  },
  analytics: {
    title: 'Analytics',
    subtitle: 'Deep insights into your demand and visitor patterns.',
    icon: BarChart3Icon,
    footerLeft: 'Data refreshed 12 minutes ago · all locations included',
    footerRight: 'Comparisons use the previous 30 days'
  },
  forecast: {
    title: 'Demand Forecast',
    subtitle: 'AI-powered visitor and demand predictions.',
    icon: GaugeIcon,
    footerLeft: 'Forecasts refresh nightly from your ingested demand data.',
    // Was "Model v4.2 · last trained Jun 3, 2025" while the API was serving
    // lightgbm-fs-2026.09.2-it332 — a version and a date that nothing in the
    // system reported. The served version is per-forecast data, so it is named
    // beside each row rather than guessed at here.
    footerRight: 'Model version is shown on every forecast'
  },
  ingestion: {
    title: 'Import Demand Data',
    subtitle: 'Upload or enter demand data from multiple sources.',
    icon: DownloadIcon,
    footerLeft: 'Secure upload · your data is encrypted and protected.',
    footerRight: 'Dates: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY · ambiguous dates default to day-first'
  },
  sources: {
    title: 'Sources',
    subtitle: 'Every channel feeding demand data into DemandIQ.',
    icon: LayersIcon,
    footerLeft: 'Nightly sync at 02:00 · reconciliation is automatic',
    footerRight: 'All connections encrypted via OAuth 2.0'
  },
  quality: {
    title: 'Quality Monitor',
    subtitle: 'Catch bad data before it reaches your forecast.',
    icon: SearchCheckIcon,
    footerLeft: 'Last scan 8 minutes ago',
    footerRight: 'Rules run on every import'
  },
  alerts: {
    title: 'Alerts',
    subtitle: 'What changed, what broke, and what needs a decision.',
    icon: BellIcon,
    footerLeft: 'Alert rules run continuously',
    footerRight: 'Delivery: in-app and email'
  },
  reports: {
    title: 'Reports',
    subtitle: 'Package demand insight for the people who act on it.',
    icon: FileTextIcon,
    footerLeft: 'Exports include the confidence band by default',
    footerRight: 'Scheduled reports arrive at 07:00 local time'
  },
  settings: {
    title: 'Settings',
    subtitle: 'Account, workspace and delivery preferences.',
    icon: SettingsIcon,
    footerLeft: 'Changes apply to this workspace only',
    footerRight: 'Demo environment'
  },
  profile: {
    title: 'Your Profile',
    subtitle: 'How you appear to your team, and what you can reach.',
    icon: UserIcon,
    footerLeft: 'Profile changes are visible to your workspace',
    footerRight: 'Location access is set by an administrator'
  },
  devices: {
    title: 'Devices & Sessions',
    subtitle: 'Everywhere this account is signed in, and how to end any of them.',
    icon: MonitorIcon,
    footerLeft: 'Signing a device out ends that session immediately',
    footerRight: 'A trusted device is only asked for a code less often'
  },
  'settings-preferences': {
    title: 'Preferences',
    subtitle: 'Theme, language and how the product behaves for you.',
    icon: SlidersHorizontalIcon,
    footerLeft: 'Preferences are stored on this device',
    footerRight: 'Applies to your account only'
  },
  billing: {
    title: 'Billing & Plan',
    subtitle: 'Your plan, usage against its limits, and invoices.',
    icon: CreditCardIcon,
    footerLeft: 'Prices shown in RWF, excluding VAT',
    footerRight: 'Next renewal 1 Oct 2026'
  },
  'admin-models': {
    title: 'Model Registry',
    subtitle: 'Compare models and control what ships.',
    icon: BrainCircuitIcon,
    footerLeft: 'Chronological validation · test window held out',
    footerRight: 'MAE · RMSE · RMSLE'
  },
  'admin-datasets': {
    title: 'Datasets',
    subtitle: 'Provenance, access and evidence boundaries.',
    icon: FolderSearchIcon,
    footerLeft: 'Demand and waste evidence stay separate',
    footerRight: 'Audited Jun 3, 2025'
  },
  'admin-policies': {
    title: 'Policy Lab',
    subtitle: 'Turn forecasts into preparation decisions.',
    icon: FlaskConicalIcon,
    footerLeft: 'Surplus is simulated exposure, not measured waste',
    footerRight: 'Calibrated on validation data'
  },
  'admin-connectors': {
    title: 'Connectors & API',
    subtitle: 'MoMo, POS, files and partner systems.',
    icon: PlugZapIcon,
    footerLeft: 'All channels normalise to one daily-demand schema',
    footerRight: 'Keys scoped per client'
  },
  'admin-accuracy': {
    title: 'Forecast Accuracy',
    subtitle: 'How the published forecast is doing against the demand that landed.',
    icon: TargetIcon,
    footerLeft: 'A day is scored once its forecast is older than the day and its demand is recorded',
    footerRight: 'Rolling-mean fallback days are counted, never scored'
  },
  'admin-experiments': {
    title: 'Experiments',
    subtitle: 'Backtest a change before it reaches production.',
    icon: FlaskConicalIcon,
    footerLeft: 'Rolling-origin folds · train past, test forward',
    footerRight: 'Promotion requires approval'
  },
  'admin-features': {
    title: 'Feature Store',
    subtitle: 'Every predictor, its coverage and whether it ships.',
    icon: LayersIcon,
    footerLeft: 'Leakage checks run on every feature',
    footerRight: '10 features registered'
  },
  'admin-explainer': {
    title: 'Forecast Explainer',
    subtitle: 'Why a past forecast said what it said.',
    icon: SearchCheckIcon,
    footerLeft: 'SHAP values show predictive weight, not causation',
    footerRight: 'Last 7 days retained'
  },
  'admin-locations': {
    title: 'Locations & Menu',
    subtitle: 'Which sites can be forecast, and what they serve.',
    icon: MapPinIcon,
    footerLeft: '7 days of history required before first forecast',
    footerRight: 'Ingest can start before the site is scorable'
  },
  /* A sub-route of `admin-locations`: addressable at `/admin-locations-new` so
     the add form survives a reload and the browser's back button leaves it,
     but never listed in the sidebar. `pageMeta` is still required — the shell
     resolves its header with `pageMeta[activeNavId] ?? pageMeta.overview`, so a
     missing entry would title this page "Overview". */
  'admin-locations-new': {
    title: 'Add location',
    subtitle: 'A new site starts ingesting immediately.',
    icon: MapPinIcon,
    footerLeft: 'Forecasts begin once 7 days of history land',
    footerRight: 'Archiving keeps the history'
  },
  /* `device-fleet` and `mirror-approvals` were missing here, so `AppShell`'s
     `?? pageMeta.overview` fallback showed an "Overview" page header and set the
     document title to Overview while the body rendered the fleet / the approval
     queue. Every nav id needs an entry; `pageTitles.test.tsx` now enforces that. */
  'device-fleet': {
    title: 'Device Fleet',
    subtitle: 'The tills and screens reporting into DemandIQ.',
    icon: CpuIcon,
    footerLeft: 'Machines in a restaurant, not people signed into an account',
    footerRight: 'A device quiet for 24 hours is on the attention list'
  },
  'mirror-approvals': {
    title: 'Remote Support',
    subtitle: 'Consent-gated screen views, approved by a second person.',
    icon: MonitorIcon,
    footerLeft: 'Whoever asks can never be the person who agrees',
    footerRight: 'An approval ends by itself, capped at thirty minutes'
  },
  'admin-approvals': {
    title: 'Approvals',
    subtitle: 'Second sign-off on promotions, policies and access.',
    icon: GitPullRequestIcon,
    footerLeft: 'Every decision is written to the audit log',
    footerRight: 'Two-person rule enforced'
  },
  'admin-webhooks': {
    title: 'Webhooks',
    subtitle: 'Outbound events to your POS and ordering systems.',
    icon: WebhookIcon,
    footerLeft: 'Payloads signed with HMAC-SHA256',
    footerRight: '5 retries with backoff'
  },
  'admin-pipeline': {
    title: 'Pipeline Health',
    subtitle: 'Job runs, failures and drift against training data.',
    icon: ActivityIcon,
    footerLeft: 'Forecasts publish nightly at 04:00',
    footerRight: 'Retrains weekly'
  },
  'admin-ai': {
    title: 'AI Assistant',
    subtitle: 'Quotas, live sessions and the guardrails on every answer.',
    icon: SparklesIcon,
    footerLeft: 'Quota period resets on the 1st',
    footerRight: 'Assistant reads only, never writes'
  },
  'admin-site': {
    title: 'Site Admin',
    subtitle: 'Manage the platform: users, workspaces, site settings.',
    icon: ShieldAlertIcon,
    footerLeft: 'Every admin action is audited',
    footerRight: 'Changes apply immediately'
  },
  'admin-access': {
    title: 'Access Control',
    subtitle: 'Members, roles and the audit trail.',
    icon: ShieldCheckIcon,
    footerLeft: 'Role changes take effect on next sign-in',
    footerRight: 'Audit retention: 12 months'
  }
};