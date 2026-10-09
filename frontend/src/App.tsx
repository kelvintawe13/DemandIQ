import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ThemeProvider } from './contexts/ThemeContext';
import { LocaleProvider } from './contexts/LocaleContext';
import { SessionProvider, useSession } from './contexts/SessionContext';
import type { SessionUser } from './contexts/SessionContext';
import { NavigationProvider } from './contexts/NavigationContext';
import { ToastProvider } from './contexts/ToastContext';
import { AppShell } from './components/AppShell';
import { ErrorPage } from './pages/ErrorPage';
import { Landing } from './pages/Landing';
import { SignIn } from './pages/auth/SignIn';
import { LegalProvider } from './contexts/LegalContext';
import { WelcomeModal } from './components/WelcomeModal';
import { AgentProvider } from './contexts/AgentContext';
import { SiteMetaProvider } from './contexts/SiteMetaContext';
import { LoadingState } from './components/ui/LoadingState';

/**
 * Pages load on demand.
 *
 * Every screen used to be a static import, so the entry bundle carried the whole
 * console — all seventeen admin pages, the analytics and forecast charting, the
 * auth flow — before the landing page could paint (1.49 MB, 390 KB gzip, with
 * Vite warning about it). Only the shell, the landing page, sign-in and the
 * error screen stay eager: they are what an unauthenticated first paint needs.
 */
// Written out per page — rather than through a name-indexing helper — so each
// lazy component keeps the exact prop type of the module it loads, and a wrong
// `onNavigate`/`account` prop is still a type error.
const Overview = lazy(() => import('./pages/Overview').then((m) => ({ default: m.Overview })));
const ImportDemandData = lazy(() =>
  import('./pages/ImportDemandData').then((m) => ({ default: m.ImportDemandData }))
);
const DemandForecast = lazy(() =>
  import('./pages/DemandForecast').then((m) => ({ default: m.DemandForecast }))
);
const Analytics = lazy(() => import('./pages/Analytics').then((m) => ({ default: m.Analytics })));
const Sources = lazy(() => import('./pages/Sources').then((m) => ({ default: m.Sources })));
const QualityMonitor = lazy(() =>
  import('./pages/QualityMonitor').then((m) => ({ default: m.QualityMonitor }))
);
const Alerts = lazy(() => import('./pages/Alerts').then((m) => ({ default: m.Alerts })));
const Reports = lazy(() => import('./pages/Reports').then((m) => ({ default: m.Reports })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const Profile = lazy(() => import('./pages/Profile').then((m) => ({ default: m.Profile })));
const Devices = lazy(() => import('./pages/Devices').then((m) => ({ default: m.Devices })));
const DeviceFleet = lazy(() =>
  import('./pages/DeviceFleet').then((m) => ({ default: m.DeviceFleet }))
);
const MirrorApprovals = lazy(() =>
  import('./pages/MirrorApprovals').then((m) => ({ default: m.MirrorApprovals }))
);
const Billing = lazy(() => import('./pages/Billing').then((m) => ({ default: m.Billing })));
const AiGovernance = lazy(() =>
  import('./pages/admin/AiGovernance').then((m) => ({ default: m.AiGovernance }))
);
const ModelRegistry = lazy(() =>
  import('./pages/admin/ModelRegistry').then((m) => ({ default: m.ModelRegistry }))
);
const DatasetsAdmin = lazy(() =>
  import('./pages/admin/DatasetsAdmin').then((m) => ({ default: m.DatasetsAdmin }))
);
const PolicyLab = lazy(() => import('./pages/admin/PolicyLab').then((m) => ({ default: m.PolicyLab })));
const Connectors = lazy(() =>
  import('./pages/admin/Connectors').then((m) => ({ default: m.Connectors }))
);
const AccessControl = lazy(() =>
  import('./pages/admin/AccessControl').then((m) => ({ default: m.AccessControl }))
);
const SiteAdmin = lazy(() => import('./pages/admin/SiteAdmin').then((m) => ({ default: m.SiteAdmin })));
const PipelineHealth = lazy(() =>
  import('./pages/admin/PipelineHealth').then((m) => ({ default: m.PipelineHealth }))
);
const ForecastAccuracy = lazy(() =>
  import('./pages/admin/ForecastAccuracy').then((m) => ({ default: m.ForecastAccuracy }))
);
const Experiments = lazy(() =>
  import('./pages/admin/Experiments').then((m) => ({ default: m.Experiments }))
);
const FeatureStore = lazy(() =>
  import('./pages/admin/FeatureStore').then((m) => ({ default: m.FeatureStore }))
);
const ForecastExplainer = lazy(() =>
  import('./pages/admin/ForecastExplainer').then((m) => ({ default: m.ForecastExplainer }))
);
const LocationRegistry = lazy(() =>
  import('./pages/admin/LocationRegistry').then((m) => ({ default: m.LocationRegistry }))
);
// A sub-route of `admin-locations`, reachable by URL but not from the sidebar.
const AddLocation = lazy(() =>
  import('./pages/admin/AddLocation').then((m) => ({ default: m.AddLocation }))
);
const Approvals = lazy(() => import('./pages/admin/Approvals').then((m) => ({ default: m.Approvals })));
const Webhooks = lazy(() => import('./pages/admin/Webhooks').then((m) => ({ default: m.Webhooks })));
const SignUp = lazy(() => import('./pages/auth/SignUp').then((m) => ({ default: m.SignUp })));
const ResetPassword = lazy(() =>
  import('./pages/auth/ResetPassword').then((m) => ({ default: m.ResetPassword }))
);
const CheckEmail = lazy(() => import('./pages/auth/CheckEmail').then((m) => ({ default: m.CheckEmail })));
const VerifyEmail = lazy(() =>
  import('./pages/auth/VerifyEmail').then((m) => ({ default: m.VerifyEmail }))
);
const AllSet = lazy(() => import('./pages/auth/AllSet').then((m) => ({ default: m.AllSet })));
const CompleteProfile = lazy(() =>
  import('./pages/auth/CompleteProfile').then((m) => ({ default: m.CompleteProfile }))
);
const SigningIn = lazy(() => import('./pages/auth/SigningIn').then((m) => ({ default: m.SigningIn })));
const TwoFactor = lazy(() => import('./pages/auth/TwoFactor').then((m) => ({ default: m.TwoFactor })));
import { demoAccounts } from './data/demoAccount';
import type { DemoAccount } from './data/demoAccount';
import { canAccess, HOME_BY_ROLE, ROLE_KEY_BY_LABEL, type AppRole } from './data/navItems';
import { setToken } from './api/client';
import { SESSION_EXPIRED_MESSAGE, notifySessionExpired, onSessionExpired } from './lib/sessionExpiry';
import { pageMeta } from './data/pages';
import { syncDocumentMeta } from './hooks/useDocumentTitle';
import { useWorkspaceTimezone } from './hooks/useWorkspaceTimezone';
import { authRouteFromPath, initialScreenFromPath, pathForRouteId, withBase } from './lib/route';

/**
 * Screens that sit outside the role navigation but every signed-in seat owns.
 *
 * These are reached from the account menu rather than the sidebar, so they stay
 * out of `navItems` — and out of the per-role sidebar counts a role re-scope is
 * written against. Each is still a shareable URL, which is why the deep-link
 * check consults this set alongside the role check.
 */
const PERSONAL_ROUTES = new Set(['profile', 'devices', 'billing', 'settings-preferences']);

type Route =
'landing' |
'signin' |
'signup' |
'reset' |
'check-email' |
'verify' |
'two-factor' |
'all-set' |
'complete-profile' |
'callback' |
'app';

interface AppProps {
  startAuthenticated?: boolean;
  defaultTheme?: 'light' | 'dark';
  defaultColorScheme?: 'ocean' | 'ash' | 'green' | 'blue' | 'yellow' | 'purple' | 'orange' | 'red' | 'pink' | 'custom';
  defaultCustomColor?: string;
}

interface AppBootedProps {
  account: DemoAccount;
  setAccount: (seat: DemoAccount) => void;
  user: SessionUser;
  startAuthenticated: boolean;
  onRegisterSignOut: (fn: () => void) => void;
}

export function App({
  startAuthenticated = false,
  // Dark is the platform default; a stored user preference overrides it in
  // ThemeContext as soon as `/me/preferences` answers.
  defaultTheme = 'dark',
  defaultColorScheme = 'ocean',
  defaultCustomColor = '#64748b'
}: AppProps) {
  // The demo seat lives ABOVE SessionProvider because it feeds its `user`
  // prop (and so stays in sync with a restored real session). Everything that
  // actually reads the session lives below in AppBooted — a component cannot
  // consume a context its own render provides (see SessionContext).
  const [account, setAccount] = useState<DemoAccount>(demoAccounts[0]);
  const user: SessionUser = useMemo(
    () => ({
      name: account.name,
      role: account.role,
      roleKey: ROLE_KEY_BY_LABEL[account.role],
      email: account.email,
      initials: account.initials
    }),
    [account]
  );
  // The session context owns sign-out (token purge, /auth/logout); the screen
  // reset it needs to trigger is registered by AppBooted on mount.
  const resetLocalSession = useRef<() => void>(() => undefined);
  const onRegisterSignOut = useCallback((fn: () => void) => {
    resetLocalSession.current = fn;
  }, []);
  const signOut = useCallback(() => {
    setAccount(demoAccounts[0]);
    resetLocalSession.current();
  }, []);

  return (
    <ThemeProvider initialTheme={defaultTheme} initialColorScheme={defaultColorScheme} initialCustomColor={defaultCustomColor}>
      <LocaleProvider>
        {/* ToastProvider sits above the session layer so SessionContext can
            raise expiry toasts from the API client's session-expiry event. */}
        <ToastProvider>
          <SiteMetaProvider>
            <SessionProvider user={user} signOut={signOut}>
              <AppBooted
                account={account}
                setAccount={setAccount}
                user={user}
                startAuthenticated={startAuthenticated}
                onRegisterSignOut={onRegisterSignOut} />
            </SessionProvider>
          </SiteMetaProvider>
        </ToastProvider>
      </LocaleProvider>
    </ThemeProvider>);
}

function AppBooted({
  account,
  setAccount,
  user,
  startAuthenticated,
  onRegisterSignOut
}: AppBootedProps) {
  const session = useSession();
  // Resolve the workspace's configured timezone before the pages decide any
  // calendar day, so "today" is the workspace's day and not a hardcoded one.
  // Called here, above every screen, because its resolution re-renders this
  // component and so the page below it — the shell's own re-render would not.
  // Gated on the session so the public landing/sign-in shell does not fire an
  // unauthenticated `GET /workspace` (a guaranteed 401) before anyone logs in.
  useWorkspaceTimezone(session.isAuthenticated);

  // Restore-on-reload: if a persisted session exists, open straight into the
  // app. SessionContext rehydrates the user from /auth/me; the account seat is
  // re-derived from the restored role below. Without this, a plain reload
  // bounced the user back to the marketing page (looked like being logged
  // out even though tokens were intact and /auth/me returned 200).
  const [hasPersistedSession] = useState(() => {
    try {
      return Boolean(localStorage.getItem('demandiq_token'));
    } catch {
      return false;
    }
  });
  // A direct hit on `/login` or `/signup` names an auth screen, so it wins over
  // the seat guess: without this the landing page rendered and its URL sync
  // rewrote `/login` back to `/`. Falling through to `app`/`landing` when the
  // path is not an auth path keeps a plain reload restoring the session.
  const [route, setRoute] = useState<Route>(() => {
    const authRoute = authRouteFromPath(window.location.pathname);
    if (authRoute) return authRoute as Route;
    return startAuthenticated || hasPersistedSession ? 'app' : 'landing';
  });
  const [resetEmail, setResetEmail] = useState('');
  // Welcome is shown once per successful entry, and is framed differently for
  // a brand-new workspace than for a returning sign-in.
  const [welcome, setWelcome] = useState<'signin' | 'signup'>();
  // Set when the API layer reports an unrecoverable session expiry; shown as
  // a notice on the sign-in screen until the next successful sign-in.
  const [sessionNotice, setSessionNotice] = useState<string>();
  // Fresh Google sign-up awaiting profile completion.
  const [profilePending, setProfilePending] = useState<DemoAccount | null>(null);
  const [history, setHistory] = useState<string[]>([demoAccounts[0].home]);
  // The URL is the only record of where the user was, and it has to be read
  // before any effect rewrites it (`Replace the URL with the active nav id`
  // below does that on mount). A phone reloads far more often than a desktop
  // tab does — iOS evicts background tabs — so without this, refreshing or
  // reopening the app dropped the user back on their home screen.
  const pendingDeepLink = useRef<string | null>(initialScreenFromPath(window.location.pathname));

  // Align the demo seat + landing page with the RESTORED session's role so a
  // reload lands the user on the same screen they left (and the sidebar shows
  // their real name/role, not the demo placeholder).
  const seatRestored = useRef(false);
  useEffect(() => {
    if (seatRestored.current) return;
    if (route !== 'app' || !session.isAuthenticated) return;
    // Wait for /auth/me: only a hydrated session carries an id. Latching before
    // it arrives restored the PLACEHOLDER demo seat instead — a reload of an
    // owner/admin account came back as the clerk seat with every admin screen
    // hidden (the placeholder seat shares demo@demandiq.app's email, so a
    // name/email check cannot tell the two apart).
    if (!session.user.id) return;
    seatRestored.current = true;
    const role = session.user.role as AppRole;
    const seat: DemoAccount = {
      ...demoAccounts[0],
      email: session.user.email,
      name: session.user.name || session.user.email,
      initials: session.user.initials || (session.user.name || 'U').slice(0, 2).toUpperCase(),
      role,
      home: HOME_BY_ROLE[role] ?? 'overview',
    };
    setAccount(seat);
    // Land on the screen the URL asked for when this seat may open it; a deep
    // link to a screen the role cannot see falls back to its home. Either way
    // the pending link is consumed, so it is only honoured once.
    const deepLink = pendingDeepLink.current;
    pendingDeepLink.current = null;
    const canOpenDeepLink =
      deepLink !== null &&
      (canAccess(role, deepLink, session.user.isSiteAdmin === true) || PERSONAL_ROUTES.has(deepLink));
    setHistory([canOpenDeepLink ? deepLink : seat.home]);
  }, [route, session.isAuthenticated, session.user, setAccount]);

  const requested = history[history.length - 1];
  // Account-level screens sit outside the role navigation but are open to
  // every signed-in seat.
  const isAllowed = canAccess(account.role, requested, session.user.isSiteAdmin === true) || PERSONAL_ROUTES.has(requested);
  const activeNavId = isAllowed ? requested : account.home;

  // Dynamic per-route page titles (index.html base is overridden here).
  // Outer routes (landing/signin/...) use routeTitles map; inner app routes
  // use pageMeta[activeNavId]. See hooks/useDocumentTitle.ts for contract.
  useEffect(() => {
    if (route === 'app') {
      const meta = pageMeta[activeNavId] ?? pageMeta.overview;
      syncDocumentMeta({ route, pageMeta: meta });
    } else {
      syncDocumentMeta({ route });
    }
  }, [route, activeNavId]);

  const navigate = useCallback((id: string) => {
    setHistory((current) =>
    current[current.length - 1] === id ? current : [...current, id]
    );
    // Update URL to reflect navigation
    const path = pathForRouteId(id);
    window.history.pushState({ navId: id }, '', path);
  }, []);

  const back = useCallback(() => {
    setHistory((current) => current.length > 1 ? current.slice(0, -1) : current);
    // Update URL for back navigation
    if (history.length > 1) {
      const previousId = history[history.length - 2];
      const path = pathForRouteId(previousId);
      window.history.pushState({ navId: previousId }, '', path);
    }
  }, [history]);

  // Sign-out is driven from the session context (token purge → externalSignOut
  // in App); the screen reset that belongs with it is registered here.
  useEffect(() => {
    onRegisterSignOut(() => {
      setHistory([demoAccounts[0].home]);
      // Drop any unconsumed deep link so the next sign-in does not resurrect it.
      pendingDeepLink.current = null;
      setRoute('landing');
      window.history.pushState(null, '', withBase('/'));
    });
  }, [onRegisterSignOut]);

  const signIn = useCallback(
    (next: DemoAccount, kind: 'signin' | 'signup' = 'signin') => {
      setAccount(next);
      setHistory([next.home]);
      setWelcome(kind);
      setSessionNotice(undefined);
      setRoute('callback');
    },
    [setAccount]
  );

  // Stable identity — SigningIn used to depend on an inline `() => setRoute('app')`
  // that changed every render. Session hydrate re-rendered App mid-boot, cancelled
  // the effect, and restarted forever on the loading screen.
  const finishSignIn = useCallback(() => setRoute('app'), []);

  // Unrecoverable 401 → session-expiry UX: purge the refresh credential,
  // raise the toast (via the SessionContext subscriber), and route the user
  // to sign-in with an explanatory notice instead of failing silently.
  useEffect(
    () =>
      onSessionExpired(() => {
        try {
          localStorage.removeItem('demandiq_refresh_token');
        } catch {}
        notifySessionExpired();
        setSessionNotice(SESSION_EXPIRED_MESSAGE);
        setRoute('signin');
      }),
    [],
  );

  // Handle browser back/forward navigation
  useEffect(() => {
    const handlePopState = (event: PopStateEvent) => {
      if (event.state?.navId) {
        setHistory((current) => {
          const newHistory = [...current];
          // Find and navigate to the state's navId
          const index = newHistory.indexOf(event.state.navId);
          if (index !== -1) {
            return newHistory.slice(0, index + 1);
          }
          return [...newHistory, event.state.navId];
        });
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Set initial URL based on current route
  useEffect(() => {
    if (route === 'app' && activeNavId) {
      // A deep link is still waiting for the seat to hydrate — rewriting the
      // URL now is what used to erase it before it could be honoured.
      if (pendingDeepLink.current) return;
      const path = pathForRouteId(activeNavId);
      if (window.location.pathname !== path) {
        window.history.replaceState({ navId: activeNavId }, '', path);
      } else if (window.history.state?.navId !== activeNavId) {
        // A deep link already points at this screen, so only the history state
        // is missing. Fill it in, or back/forward has nothing to restore.
        window.history.replaceState({ navId: activeNavId }, '', path);
      }
    } else if (route === 'landing' && window.location.pathname !== withBase('/') && !authRouteFromPath(window.location.pathname)) {
      window.history.replaceState(null, '', withBase('/'));
    } else if (route === 'signin' && window.location.pathname !== withBase('/login')) {
      window.history.replaceState(null, '', withBase('/login'));
    } else if (route === 'signup' && window.location.pathname !== withBase('/signup')) {
      window.history.replaceState(null, '', withBase('/signup'));
    }
  }, [route, activeNavId]);

  // Admin seats and Google sign-in pass through a second factor first.
  const challenge = useCallback(
    (next: DemoAccount) => {
      setAccount(next);
      setRoute('two-factor');
    },
    [setAccount],
  );

  const navigationValue = useMemo(
    () => ({
      activeId: activeNavId,
      navigate,
      back,
      canGoBack: history.length > 1
    }),
    [activeNavId, navigate, back, history.length]
  );

  const renderPage = () => {
    // Flag-gated page guard: deep links to the Site Admin console 403 for
    // anyone without users.is_site_admin, even if the route is typed by hand.
    if (activeNavId === 'admin-site' && session.user.isSiteAdmin !== true) {
      return (
        <ErrorPage
          kind="not-found"
          detail="This console is restricted to platform administrators."
          onHome={() => navigate(account.home)} />);
    }
    switch (activeNavId) {
      case 'analytics':
        return <Analytics onNavigate={navigate} />;
      case 'forecast':
        return <DemandForecast onNavigate={navigate} />;
      case 'ingestion':
        return <ImportDemandData />;
      case 'sources':
        return <Sources onNavigate={navigate} />;
      case 'quality':
        return <QualityMonitor />;
      case 'alerts':
        return <Alerts onNavigate={navigate} />;
      case 'reports':
        return <Reports />;
      case 'settings':
        return <Settings onNavigate={navigate} />;
      case 'settings-preferences':
        return <Settings onNavigate={navigate} initialTab="preferences" />;
      case 'profile':
        return <Profile onNavigate={navigate} />;
      case 'devices':
        return <Devices onNavigate={navigate} />;
      case 'device-fleet':
        return <DeviceFleet />;
      case 'mirror-approvals':
        return <MirrorApprovals />;
      case 'billing':
        return <Billing onNavigate={navigate} />;
      case 'admin-models':
        return <ModelRegistry />;
      case 'admin-datasets':
        return <DatasetsAdmin />;
      case 'admin-policies':
        return <PolicyLab />;
      case 'admin-connectors':
        return <Connectors />;
      case 'admin-accuracy':
        return <ForecastAccuracy />;
      case 'admin-experiments':
        return <Experiments />;
      case 'admin-features':
        return <FeatureStore />;
      case 'admin-explainer':
        return <ForecastExplainer />;
      case 'admin-locations':
        return <LocationRegistry onNavigate={navigate} />;
      case 'admin-locations-new':
        return <AddLocation onNavigate={navigate} />;
      case 'admin-approvals':
        return <Approvals />;
      case 'admin-webhooks':
        return <Webhooks />;
      case 'admin-pipeline':
        return <PipelineHealth />;
      case 'admin-ai':
        return <AiGovernance />;
      case 'admin-access':
        return <AccessControl />;
      case 'admin-site':
        return <SiteAdmin />;
      case 'overview':
        return <Overview onNavigate={navigate} />;
      default:
        return (
          <ErrorPage
            kind="not-found"
            detail={`No screen registered for "${activeNavId}"`}
            onHome={() => navigate(account.home)}
            onBack={history.length > 1 ? back : undefined} />);


    }
  };

  const renderRoute = () => {
    switch (route) {
      case 'landing':
        return (
          <Landing
            onSignIn={() => {
              setRoute('signin');
              window.history.pushState(null, '', withBase('/login'));
            }}
            onGetStarted={() => {
              setRoute('signup');
              window.history.pushState(null, '', withBase('/signup'));
            }} />);


      case 'signup':
        return (
          <SignUp
            onNavigateAuth={(view) => setRoute(view as Route)}
            onSignIn={signIn}
            onCompleteProfile={(acct) => {
              setProfilePending(acct);
              setRoute('complete-profile');
            }} />);
      case 'reset':
        return (
          <ResetPassword
            onNavigateAuth={(view) => setRoute(view as Route)}
            onEmailSubmit={setResetEmail} />);


      case 'check-email':
        return (
          <CheckEmail
            email={resetEmail}
            onNavigateAuth={(view) => setRoute(view as Route)} />);


      case 'verify':
        return <VerifyEmail onNavigateAuth={(view) => setRoute(view as Route)} />;
      case 'complete-profile':
        return (
          <CompleteProfile
            account={profilePending}
            onDone={(finished) => {
              setProfilePending(null);
              signIn(finished);
            }}
            onCancel={() => {
              setProfilePending(null);
              setToken(null);
              setRoute('signin');
            }}
          />
        );
      case 'two-factor':
        return (
          <TwoFactor
            email={account.email}
            onVerified={() => signIn(account)}
            onNavigateAuth={(view) => setRoute(view as Route)} />);


      case 'all-set':
        return <AllSet onEnterApp={() => signIn(demoAccounts[0], 'signup')} />;
      case 'callback':
        return <SigningIn userName={user.name} onComplete={finishSignIn} />;
      case 'app':
        return (
          <>
            <AppShell activeNavId={activeNavId} onNavigate={navigate}>
              {/* Inside the shell, so a page chunk loads behind the nav rather
                  than replacing it with a bare fallback. */}
              <Suspense fallback={<LoadingState label="Loading this page" />}>
                {renderPage()}
              </Suspense>
            </AppShell>
            <WelcomeModal
              open={Boolean(welcome)}
              kind={welcome ?? 'signin'}
              name={user.name}
              role={user.role}
              onClose={() => setWelcome(undefined)}
              onNavigate={navigate} />
            
          </>);

      default:
        return (
          <SignIn
            onSignIn={signIn}
            onChallenge={challenge}
            onNavigateAuth={(view) => setRoute(view as Route)}
            sessionNotice={sessionNotice}
            onCompleteProfile={(acct) => {
              setProfilePending(acct);
              setRoute('complete-profile');
            }} />);


    }
  };

  return (
    <NavigationProvider value={navigationValue}>
      <AgentProvider>
        <LegalProvider>
          {/* Catches the auth-flow screens; app pages have their own boundary
              inside AppShell. */}
          <Suspense fallback={<LoadingState label="Loading" />}>{renderRoute()}</Suspense>
        </LegalProvider>
      </AgentProvider>
    </NavigationProvider>);

}