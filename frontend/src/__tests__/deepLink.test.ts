import { describe, it, expect } from 'vitest';
import {
  authRouteFromPath,
  bootRouteFromPath,
  initialScreenFromPath,
  pathForRouteId,
  routeIdFromPath,
  withBase,
} from '../lib/route';
import { pageMeta } from '../data/pages';

describe('routeIdFromPath', () => {
  it('treats the root as the overview', () => {
    expect(routeIdFromPath('/')).toBe('overview');
    expect(routeIdFromPath('')).toBe('overview');
  });

  it('reads a screen id off the path', () => {
    expect(routeIdFromPath('/reports')).toBe('reports');
    expect(routeIdFromPath('/admin-models')).toBe('admin-models');
    expect(routeIdFromPath('/admin-access')).toBe('admin-access');
  });

  it('tolerates a trailing slash', () => {
    expect(routeIdFromPath('/reports/')).toBe('reports');
  });

  it('refuses anything that is not shaped like a screen id', () => {
    expect(routeIdFromPath('/a/b')).toBeNull();
    expect(routeIdFromPath('/foo.bar')).toBeNull();
    expect(routeIdFromPath('/Reports')).toBeNull();
    expect(routeIdFromPath('/%20')).toBeNull();
  });

  it('leaves whether a screen exists to the access check', () => {
    // `/login` is shaped like an id and is returned as one on purpose: this
    // function maps paths, it does not own the screen list. App drops an id the
    // seat cannot open, which is what stops an auth path becoming a screen.
    expect(routeIdFromPath('/login')).toBe('login');
    expect(routeIdFromPath('/not-a-screen')).toBe('not-a-screen');
  });

  it('round-trips every screen the app knows about', () => {
    for (const navId of Object.keys(pageMeta)) {
      expect(routeIdFromPath(pathForRouteId(navId))).toBe(navId);
    }
  });
});

describe('authRouteFromPath', () => {
  it('reads the auth screen a shareable path names', () => {
    // A direct hit on these used to render the landing page and then rewrite the
    // URL to `/`, so an emailed sign-in link silently became the marketing page.
    expect(authRouteFromPath('/login')).toBe('signin');
    expect(authRouteFromPath('/signup')).toBe('signup');
    expect(authRouteFromPath('/login/')).toBe('signin');
  });

  it('claims nothing that is not an auth path', () => {
    expect(authRouteFromPath('/')).toBeNull();
    expect(authRouteFromPath('/reports')).toBeNull();
    expect(authRouteFromPath('/signin')).toBeNull();
  });
});

describe('initialScreenFromPath', () => {
  it('restores a screen the path names', () => {
    expect(initialScreenFromPath('/reports')).toBe('reports');
    expect(initialScreenFromPath('/admin-models')).toBe('admin-models');
  });

  it('does not treat the root as a screen', () => {
    // `/` is ambiguous: it is both the overview and "opened without naming a
    // screen". Honouring it sent every role to Overview instead of the screen
    // its role is designed to start on (HOME_BY_ROLE).
    expect(initialScreenFromPath('/')).toBeNull();
    expect(initialScreenFromPath('')).toBeNull();
  });

  it('ignores a path that is not shaped like a screen', () => {
    expect(initialScreenFromPath('/a/b')).toBeNull();
  });
});

describe('the /app mount', () => {
  // `frontend/dist` is served under `/app` (backend/app/main.py), so the built
  // app's paths carry that prefix while the dev server's do not. Every helper
  // takes the base as an argument, which is what makes this testable without
  // rebuilding the bundle with a different `base`.
  const BASE = '/app';

  it('writes and reads screen paths under the mount', () => {
    expect(pathForRouteId('reports', BASE)).toBe('/app/reports');
    expect(pathForRouteId('overview', BASE)).toBe('/app/');
    expect(routeIdFromPath('/app/reports', BASE)).toBe('reports');
    expect(initialScreenFromPath('/app/reports', BASE)).toBe('reports');
  });

  it('leaves the mount root as "no screen named", exactly like /', () => {
    expect(initialScreenFromPath('/app/', BASE)).toBeNull();
    expect(initialScreenFromPath('/app', BASE)).toBeNull();
  });

  it('reads the auth screens a shared /app link names', () => {
    expect(authRouteFromPath('/app/login', BASE)).toBe('signin');
    expect(authRouteFromPath('/app/signup', BASE)).toBe('signup');
    expect(withBase('/login', BASE)).toBe('/app/login');
    expect(withBase('/', BASE)).toBe('/app/');
  });
});

describe('bootRouteFromPath', () => {
  // Opening the console is a decision made once, at load: which screen, and
  // whether there is a session at all.
  it('opens the console for a screen link when a credential is stored', () => {
    expect(bootRouteFromPath('/reports', { hasToken: true })).toBe('app');
  });

  it('opens sign-in for a screen link with no credential', () => {
    // Not the landing page (the link looked ignored) and not the shell, which
    // rendered one 401 panel per call with no way back to sign-in.
    expect(bootRouteFromPath('/reports', { hasToken: false })).toBe('signin');
    expect(bootRouteFromPath('/admin-models', { hasToken: false })).toBe('signin');
  });

  it('keeps the mount root public', () => {
    // `/` is the marketing page, not a screen link: it must not bounce a first
    // visitor to a sign-in form they did not ask for.
    expect(bootRouteFromPath('/', { hasToken: false })).toBe('landing');
    expect(bootRouteFromPath('/app/', { hasToken: false, base: '/app' })).toBe('landing');
  });

  it('lets a named auth screen win over the credential', () => {
    expect(bootRouteFromPath('/login', { hasToken: false })).toBe('signin');
    expect(bootRouteFromPath('/signup', { hasToken: false })).toBe('signup');
    expect(bootRouteFromPath('/login', { hasToken: true })).toBe('signin');
  });

  it('honours the startAuthenticated harness', () => {
    expect(bootRouteFromPath('/', { hasToken: false, startAuthenticated: true })).toBe('app');
  });

  it('reads screen links under the /app mount', () => {
    expect(bootRouteFromPath('/app/reports', { hasToken: false, base: '/app' })).toBe('signin');
    expect(bootRouteFromPath('/app/reports', { hasToken: true, base: '/app' })).toBe('app');
  });
});

describe('pathForRouteId', () => {
  it('puts the overview at the root, matching how navigate() writes URLs', () => {
    expect(pathForRouteId('overview')).toBe('/');
    expect(pathForRouteId('reports')).toBe('/reports');
  });

  it('never produces a double slash', () => {
    for (const navId of Object.keys(pageMeta)) {
      expect(pathForRouteId(navId)).toMatch(/^\/(?:[a-z0-9-]+)?$/);
    }
  });
});
