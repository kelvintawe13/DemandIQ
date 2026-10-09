import { HOME_BY_ROLE, type AppRole } from './navItems';

/**
 * Sign-in seats, one per role. The credentials match the accounts created by
 * `backend/scripts/seed_demo_roles.py`, so every role in the capability model
 * can actually be signed into — and the sign-in screen can offer them.
 */
export interface DemoAccount {
  id: 'owner' | 'admin' | 'clerk' | 'analyst' | 'viewer';
  email: string;
  password: string;
  name: string;
  role: AppRole;
  initials: string;
  label: string;
  blurb: string;
  /** First screen after sign-in for this role. */
  home: string;
}

/**
 * The owner seat comes first: it is the placeholder seat the app renders while
 * /auth/me hydrates (see App.tsx), and the workspace owner is the account the
 * demo is built around.
 */
export const demoAccounts: DemoAccount[] = [
{
  id: 'owner',
  email: 'demo@demandiq.app',
  password: 'DemandIQ2026!',
  name: 'Demo Owner',
  role: 'Owner',
  initials: 'DO',
  label: 'Owner',
  blurb: 'Everything the business produces: forecasts, analytics, policies, reports, plus members, approvals and billing.',
  home: HOME_BY_ROLE.Owner
},
{
  id: 'admin',
  email: 'admin@demandiq.app',
  password: 'Admin@2026!',
  name: 'Site Admin',
  role: 'Administrator',
  initials: 'SA',
  label: 'Administrator',
  blurb: 'The modelling and platform console: train and promote models, datasets, features, experiments, connectors and access.',
  home: HOME_BY_ROLE.Administrator
},
{
  id: 'clerk',
  email: 'clerk@demandiq.app',
  password: 'Clerk@2026!',
  name: 'Aline Umutoni',
  role: 'Data Clerk',
  initials: 'AU',
  label: 'Data Clerk',
  blurb: 'Gets demand data in and clean: ingestion, sources, quality fixes, alerts and reports.',
  home: HOME_BY_ROLE['Data Clerk']
},
{
  id: 'analyst',
  email: 'analyst@demandiq.app',
  password: 'Analyst@2026!',
  name: 'Yves Habimana',
  role: 'Analyst',
  initials: 'YH',
  label: 'Analyst',
  blurb: 'Analysis, policy simulation, forecast explanations and report generation.',
  home: HOME_BY_ROLE.Analyst
},
{
  id: 'viewer',
  email: 'viewer@demandiq.app',
  password: 'Viewer@2026!',
  name: 'Grace Uwase',
  role: 'Viewer',
  initials: 'GU',
  label: 'Viewer',
  blurb: 'Read-only: forecasts, analytics and the numbers behind them. Changes nothing.',
  home: HOME_BY_ROLE.Viewer
}];


export const demoAccount = demoAccounts[0];

export function findDemoAccount(email: string, password: string) {
  return demoAccounts.find(
    (account) =>
    account.email === email.trim().toLowerCase() && account.password === password
  );
}
