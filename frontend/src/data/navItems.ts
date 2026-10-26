import {
  HomeIcon,
  BarChart3Icon,
  GaugeIcon,
  DatabaseIcon,
  LayersIcon,
  SearchIcon,
  BellIcon,
  FileTextIcon,
  SettingsIcon,
  BrainCircuitIcon,
  FlaskConicalIcon,
  PlugZapIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  FolderSearchIcon,
  ActivityIcon,
  SearchCheckIcon,
  MapPinIcon,
  GitPullRequestIcon,
  WebhookIcon,
  SlidersHorizontalIcon,
  TargetIcon,
  SparklesIcon,
  CpuIcon } from
'lucide-react';
import type { LucideIcon } from 'lucide-react';

/**
 * Application roles, mirroring the backend capability bundles in
 * `backend/app/security/permissions.py`. `Data Clerk` is the display name for
 * the backend's `operator` role.
 */
export type AppRole =
  'Owner' |
  'Administrator' |
  'Analyst' |
  'Data Clerk' |
  'Viewer';

export const ALL_ROLES: AppRole[] = ['Owner', 'Administrator', 'Analyst', 'Data Clerk', 'Viewer'];

/**
 * Display label -> the backend's role value. Screens that compare roles
 * (member management, which may only act below the actor) rank these keys, and
 * `ROLE_RANK`/`ROLE_LABELS` on the server mirror this table.
 */
export const ROLE_KEY_BY_LABEL: Record<AppRole, string> = {
  Owner: 'owner',
  Administrator: 'administrator',
  Analyst: 'analyst',
  'Data Clerk': 'operator',
  Viewer: 'viewer'
};

/**
 * Role hierarchy, mirroring `ROLE_RANK` in `app/security/permissions.py`:
 * ordered by reach over *people*, not by capability count — an administrator
 * holds more capabilities than an owner, yet governs fewer accounts.
 */
export const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  operator: 1,
  analyst: 2,
  administrator: 3,
  owner: 4
};

/** Highest rank grantable through member management; ownership is not invitable. */
export const MAX_ASSIGNABLE_RANK = ROLE_RANK.administrator;

/** Display label for a raw role value (``operator`` -> ``Data Clerk``). */
export function roleLabel(raw: string): AppRole {
  const found = ALL_ROLES.find((label) => ROLE_KEY_BY_LABEL[label] === raw.toLowerCase());
  return found ?? 'Viewer';
}

/**
 * First screen after sign-in, per role: each role lands on the work it owns.
 * ``overview`` is the safe default for any role without a more specific home.
 */
export const HOME_BY_ROLE: Record<AppRole, string> = {
  Owner: 'overview',
  Administrator: 'admin-models',
  Analyst: 'analytics',
  'Data Clerk': 'ingestion',
  Viewer: 'overview'
};

export interface NavItem {
  id: string;
  label: string;
  labelKey: string;
  icon: LucideIcon;
  /**
   * Roles that see this screen. Derived from the capability each screen needs:
   * a role only appears here when the API would actually serve it, so the
   * sidebar cannot offer a screen that answers 403.
   */
  roles: AppRole[];
}

export interface NavSection {
  id: string;
  titleKey: string;
  items: NavItem[];
}

const WORKSPACE_ROLES: AppRole[] = ['Owner', 'Administrator', 'Analyst', 'Data Clerk'];

export function sectionsForRole(role: string, isSiteAdmin = false): NavSection[] {
  return navSections.
  map((section) => ({
    ...section,
    // The Site Admin console is flag-gated, not role-gated: only platform
    // administrators (users.is_site_admin) see it in the sidebar.
    items: section.items.filter(
      (item) => item.roles.includes(role as AppRole) && (item.id !== 'admin-site' || isSiteAdmin)
    )
  })).
  filter((section) => section.items.length > 0);
}

export function itemsForRole(role: string, isSiteAdmin = false): NavItem[] {
  return sectionsForRole(role, isSiteAdmin).flatMap((section) => section.items);
}

/**
 * Screens that are addressable but deliberately absent from the sidebar.
 *
 * They are steps inside a nav entry rather than destinations of their own — you
 * reach them from the screen they belong to, never from the menu. A route id
 * that is not in `navSections` is *dropped* by the shell (a deep link falls back
 * to the role's home and a `navigate()` call renders the home page), so a
 * sub-route has to be declared here or it does not exist. Permissions come from
 * the parent, so a sub-route cannot outlive the access its parent has.
 */
const SUB_ROUTE_PARENT: Record<string, string> = {
  'admin-locations-new': 'admin-locations',
};

/** The nav entry a sub-route belongs to, for highlighting and for access. */
export function navRootFor(navId: string): string {
  return SUB_ROUTE_PARENT[navId] ?? navId;
}

export function canAccess(role: string, navId: string, isSiteAdmin = false): boolean {
  // The platform flag opens the console, but the role still has to be one that
  // holds it: a site-admin flag must not put an administrator's screen in front
  // of an owner whose sidebar is the business workspace.
  if (navId === 'admin-site') {
    return isSiteAdmin && itemsForRole(role, true).some((item) => item.id === navId);
  }
  const items = itemsForRole(role, isSiteAdmin);
  if (items.some((item) => item.id === navId)) return true;
  const parent = SUB_ROUTE_PARENT[navId];
  return parent ? items.some((item) => item.id === parent) : false;
}

export const navSections: NavSection[] = [
{
  id: 'insights',
  titleKey: 'nav.group.insights',
  items: [
  {
    id: 'overview',
    label: 'Overview',
    labelKey: 'nav.overview',
    icon: HomeIcon,
    roles: [...ALL_ROLES]
  },
  {
    id: 'analytics',
    label: 'Analytics',
    labelKey: 'nav.analytics',
    icon: BarChart3Icon,
    roles: [...ALL_ROLES]
  },
  {
    id: 'forecast',
    label: 'Demand Forecast',
    labelKey: 'nav.forecast',
    icon: GaugeIcon,
    roles: [...ALL_ROLES]
  }]

},
{
  id: 'data',
  titleKey: 'nav.group.data',
  items: [
  {
    id: 'ingestion',
    label: 'Data Ingestion',
    labelKey: 'nav.ingestion',
    icon: DatabaseIcon,
    roles: [...WORKSPACE_ROLES]
  },
  {
    id: 'sources',
    label: 'Sources',
    labelKey: 'nav.sources',
    icon: LayersIcon,
    roles: [...WORKSPACE_ROLES]
  },
  {
    id: 'quality',
    label: 'Quality Monitor',
    labelKey: 'nav.quality',
    icon: SearchIcon,
    roles: [...WORKSPACE_ROLES]
  }]

},
{
  id: 'system',
  titleKey: 'nav.group.system',
  items: [
  {
    id: 'alerts',
    label: 'Alerts',
    labelKey: 'nav.alerts',
    icon: BellIcon,
    roles: [...WORKSPACE_ROLES]
  },
  {
    id: 'reports',
    label: 'Reports',
    labelKey: 'nav.reports',
    icon: FileTextIcon,
    roles: [...WORKSPACE_ROLES]
  },
  {
    // Workspace settings for the business and hands-on roles; the administrator
    // gets the governance copy below instead, so 'settings' never renders twice.
    id: 'settings',
    label: 'Settings',
    labelKey: 'nav.settings',
    icon: SettingsIcon,
    roles: ['Owner', 'Analyst', 'Data Clerk']
  }]

},
{
  id: 'admin-modelling',
  titleKey: 'nav.group.modelling',
  items: [
  {
    id: 'admin-models',
    label: 'Model Registry',
    labelKey: 'nav.models',
    icon: BrainCircuitIcon,
    // Training, evaluation and promotion live with the administrator.
    roles: ['Administrator']
  },
  {
    // How the served forecast is actually doing in production — the other half
    // of the registry's held-out metrics, and the administrator's morning read.
    id: 'admin-accuracy',
    label: 'Forecast Accuracy',
    labelKey: 'nav.accuracy',
    icon: TargetIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-experiments',
    label: 'Experiments',
    labelKey: 'nav.experiments',
    icon: FlaskConicalIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-features',
    label: 'Feature Store',
    labelKey: 'nav.features',
    icon: LayersIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-policies',
    label: 'Policy Lab',
    labelKey: 'nav.policies',
    icon: SlidersHorizontalIcon,
    // A planning decision the analyst and the administrator simulate here.
    roles: ['Administrator', 'Analyst']
  },
  {
    id: 'admin-explainer',
    label: 'Forecast Explainer',
    labelKey: 'nav.explainer',
    icon: SearchCheckIcon,
    roles: ['Administrator', 'Analyst']
  }]

},
{
  id: 'admin-operations',
  titleKey: 'nav.group.operations',
  items: [
  {
    id: 'admin-locations',
    label: 'Locations & Menu',
    labelKey: 'nav.locations',
    icon: MapPinIcon,
    roles: ['Administrator']
  },
  {
    // Enrolment, credential rotation, quarantine and health for the till/POS
    // hardware itself. Routine administration, so it sits in Operations and both
    // roles that hold `devices:manage` see it.
    id: 'device-fleet',
    label: 'Device Fleet',
    labelKey: 'nav.deviceFleet',
    icon: CpuIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-webhooks',
    label: 'Webhooks',
    labelKey: 'nav.webhooks',
    icon: WebhookIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-connectors',
    label: 'Connectors & API',
    labelKey: 'nav.connectors',
    icon: PlugZapIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-pipeline',
    label: 'Pipeline Health',
    labelKey: 'nav.pipeline',
    icon: ActivityIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-ai',
    label: 'AI Assistant',
    labelKey: 'nav.ai',
    icon: SparklesIcon,
    roles: ['Administrator']
  }]

},
{
  id: 'admin-governance',
  titleKey: 'nav.group.governance',
  items: [
  {
    id: 'admin-site',
    label: 'Site Admin',
    labelKey: 'nav.siteAdmin',
    icon: ShieldAlertIcon,
    // Platform console: an administrator's screen, still flag-gated on top.
    roles: ['Administrator']
  },
  {
    id: 'admin-datasets',
    label: 'Datasets',
    labelKey: 'nav.datasets',
    icon: FolderSearchIcon,
    roles: ['Administrator']
  },
  {
    id: 'admin-approvals',
    label: 'Approvals',
    labelKey: 'nav.approvals',
    icon: GitPullRequestIcon,
    roles: ['Administrator']
  },
  // `mirror-approvals` (Remote Support) is deliberately absent. Approving a
  // consent-gated remote view is an owner-only capability
  // (`device_mirror:approve`), and the owner's sidebar is now the business
  // workspace only — so listing it here would either show an administrator a
  // screen whose action the API refuses, or put a console route back in front
  // of the owner. The page and its API remain; it has no menu entry.
  {
    id: 'admin-access',
    label: 'Access Control',
    labelKey: 'nav.access',
    icon: ShieldCheckIcon,
    roles: ['Administrator']
  },
  {
    id: 'settings',
    label: 'Settings',
    labelKey: 'nav.settings',
    icon: SettingsIcon,
    roles: ['Administrator']
  }]

}];


export const navItems: NavItem[] = navSections.flatMap((section) => section.items);
