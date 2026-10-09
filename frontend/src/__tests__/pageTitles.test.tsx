import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { syncDocumentMeta, routeTitles } from '../hooks/useDocumentTitle';
import { pageMeta } from '../data/pages';
import { navSections } from '../data/navItems';
import { App } from '../App';

describe('syncDocumentMeta (unit)', () => {
  beforeEach(() => {
    document.head.innerHTML = `
      <title>Fallback</title>
      <meta name="description" content="" />
      <meta name="title" content="" />
      <meta property="og:title" content="" />
      <meta property="og:description" content="" />
      <meta name="twitter:title" content="" />
      <meta name="twitter:description" content="" />
    `;
  });

  it('maps outer routes via routeTitles', () => {
    syncDocumentMeta({ route: 'signin' });
    expect(document.title).toBe('Sign In · DemandIQ');
    expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
      routeTitles.signin.description
    );
  });

  it('maps landing route', () => {
    syncDocumentMeta({ route: 'landing' });
    expect(document.title).toBe('Explainable Demand Forecasting for Food-Waste Prevention · DemandIQ');
  });

  it('maps inner app routes via pageMeta', () => {
    syncDocumentMeta({ route: 'app', pageMeta: pageMeta.overview });
    expect(document.title).toBe('Overview · DemandIQ');
    expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
      pageMeta.overview.subtitle
    );

    syncDocumentMeta({ route: 'app', pageMeta: pageMeta.analytics });
    expect(document.title).toBe('Analytics · DemandIQ');

    syncDocumentMeta({ route: 'app', pageMeta: pageMeta['admin-models'] });
    expect(document.title).toBe('Model Registry · DemandIQ');
  });

  it('covers all outer routeTitles', () => {
    for (const [route, { title }] of Object.entries(routeTitles)) {
      syncDocumentMeta({ route });
      expect(document.title).toBe(`${title} · DemandIQ`);
    }
  });

  /**
   * `AppShell` resolves its header with `pageMeta[activeNavId] ??
   * pageMeta.overview`. A nav id with no entry therefore does not render a blank
   * — it renders the *Overview* header, so Device Fleet and Remote Support both
   * announced "Overview" and titled the document Overview while the body showed
   * the fleet and the approval queue. A missing entry is silent, so it is
   * asserted here rather than left to review.
   */
  it('has a pageMeta entry for every navigation id', () => {
    const ids = navSections.flatMap((section) => section.items.map((item) => item.id));
    expect(ids.filter((id) => !pageMeta[id])).toEqual([]);
  });

  it('covers all pageMeta entries (inner routes)', () => {
    for (const meta of Object.values(pageMeta)) {
      syncDocumentMeta({ route: 'app', pageMeta: meta });
      expect(document.title).toBe(`${meta.title} · DemandIQ`);
      expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
        meta.subtitle
      );
    }
  });
});

describe('App dynamic titles (integration, jsdom)', () => {
  beforeEach(() => {
    document.head.innerHTML = `
      <title>Fallback</title>
      <meta name="description" content="initial" />
      <meta name="title" content="" />
      <meta property="og:title" content="" />
      <meta property="og:description" content="" />
      <meta name="twitter:title" content="" />
      <meta name="twitter:description" content="" />
    `;
  });

  it('landing route sets title on mount', async () => {
    render(<App startAuthenticated={false} />);
    await waitFor(() => {
      expect(document.title).toBe('Explainable Demand Forecasting for Food-Waste Prevention · DemandIQ');
    });
  });

  it('authenticated app opens on the seat role home (Owner -> Overview)', async () => {
    // The pre-hydration seat is the owner demo seat (demoAccounts[0]), and each
    // role lands on the work it owns (see HOME_BY_ROLE in data/navItems.ts).
    render(<App startAuthenticated={true} />);
    await waitFor(() => {
      expect(document.title).toBe(`${pageMeta.overview.title} · DemandIQ`);
    });
  });

  it('navigating inside app updates title (Overview -> Analytics -> Demand Forecast)', async () => {
    const user = userEvent.setup();
    render(<App startAuthenticated={true} />);

    await waitFor(() => expect(document.title).toBe(`${pageMeta.overview.title} · DemandIQ`));

    // Sidebar + MobileNav both render same items → pick first
    const analyticsBtns = screen.getAllByRole('button', { name: /Analytics/i });
    await user.click(analyticsBtns[0]);
    await waitFor(() => expect(document.title).toBe('Analytics · DemandIQ'));
    expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toBe(
      pageMeta.analytics.subtitle
    );

    const forecastBtns = screen.getAllByRole('button', { name: /Demand Forecast/i });
    await user.click(forecastBtns[0]);
    await waitFor(() => expect(document.title).toBe('Demand Forecast · DemandIQ'));
  });

  it('auth flow: Sign In screen title', async () => {
    const user = userEvent.setup();
    render(<App startAuthenticated={false} />);
    // Landing has header Sign In + footer Sign in; pick the header one
    const signInCtas = screen.getAllByRole('button', { name: /^Sign In$/i });
    await user.click(signInCtas[0]);
    await waitFor(() => expect(document.title).toBe('Sign In · DemandIQ'));
  });
});

describe('auth deep links (integration, jsdom)', () => {
  beforeEach(() => {
    document.head.innerHTML = `
      <title>Fallback</title>
      <meta name="description" content="initial" />
      <meta name="title" content="" />
      <meta property="og:title" content="" />
      <meta property="og:description" content="" />
      <meta name="twitter:title" content="" />
      <meta name="twitter:description" content="" />
    `;
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('opens the sign-in screen from a /login link instead of the landing page', async () => {
    // Opening `/login` directly used to render the landing page and then rewrite
    // the URL back to `/`, so a shared sign-in link silently became marketing.
    window.history.replaceState(null, '', '/login');
    render(<App startAuthenticated={false} />);

    await waitFor(() => expect(document.title).toBe('Sign In · DemandIQ'));
    // The URL still names the screen it was given — it is not rewritten to `/`.
    expect(window.location.pathname).toBe('/login');
  });

  it('opens the sign-up screen from a /signup link', async () => {
    window.history.replaceState(null, '', '/signup');
    render(<App startAuthenticated={false} />);

    await waitFor(() => expect(document.title).toBe('Create Account · DemandIQ'));
    expect(window.location.pathname).toBe('/signup');
  });
});
