/**
 * URL ↔ screen mapping for the app shell.
 *
 * `navigate()` writes a screen out as `/{navId}`, with the overview at `/`.
 * Reading it back is what lets a reload keep the user's place: the URL is the
 * only record of where they were, and a phone reloads far more often than a
 * desktop tab does (iOS evicts background tabs, and users share links).
 *
 * The mount path is part of the URL. Vite serves the dev app at the root and
 * builds it for `/app/` (the path `backend/app/main.py` mounts `frontend/dist`
 * on), so every helper strips the base before matching and puts it back when
 * writing. `import.meta.env.BASE_URL` is `/` in dev and vitest, `/app/` in the
 * built artifact. The `base` parameter exists so the `/app` behaviour is
 * testable without rebuilding.
 */
const BASE = ((import.meta.env.BASE_URL as string | undefined) ?? '/').replace(/\/+$/, '');

/** `pathname` with the mount prefix removed (`/app/reports` -> `/reports`). */
function withoutBase(pathname: string, base: string): string {
  if (!base) return pathname;
  if (pathname === base) return '/';
  return pathname.startsWith(`${base}/`) ? pathname.slice(base.length) : pathname;
}

export function routeIdFromPath(pathname: string, base: string = BASE): string | null {
  const clean = withoutBase(pathname, base).replace(/^\/+|\/+$/g, '');
  if (!clean) return 'overview';
  // Anything that is not a screen id — `/login`, `/verify`, a stray segment with
  // a slash or dot — must not seed the app navigation.
  return /^[a-z0-9-]+$/.test(clean) ? clean : null;
}

/**
 * The auth screen a path addresses, or `null` when the path is not one.
 *
 * `/login` and `/signup` are the two paths the app writes itself (the landing
 * page pushes them), and they are shareable: an emailed link or a reload has to
 * land on the screen it names. `routeIdFromPath` cannot answer this — `/login`
 * is shaped exactly like a screen id, and the app drops ids the seat cannot
 * open, which is how a direct hit on `/login` became the marketing page.
 */
const AUTH_ROUTE_BY_PATH: Record<string, string> = {
  login: 'signin',
  signup: 'signup',
};

export function authRouteFromPath(pathname: string, base: string = BASE): string | null {
  const clean = withoutBase(pathname, base).replace(/^\/+|\/+$/g, '');
  return AUTH_ROUTE_BY_PATH[clean] ?? null;
}

/** The path a screen is addressed by; the overview owns the mount root. */
export function pathForRouteId(navId: string, base: string = BASE): string {
  return navId === 'overview' ? `${base}/` : `${base}/${navId}`;
}

/**
 * A root-relative path with the mount prefix applied (`/login` -> `/app/login`).
 *
 * The paths the shell writes for the landing page, sign-in and sign-up are not
 * screen ids, so they go through here rather than `pathForRouteId`.
 */
export function withBase(path: string, base: string = BASE): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  return `${base}${clean}`;
}

/**
 * The screen a first load should restore, or `null` to let the seat decide.
 *
 * The mount root is deliberately **not** a deep link. `/` is ambiguous — it is
 * both "the overview" and "the app was opened without naming a screen" — and
 * each role is designed to start on the work it owns (`HOME_BY_ROLE`): an
 * administrator on the model registry, a data clerk on data ingestion. Honouring
 * `/` as a screen sent every one of them to Overview instead.
 *
 * A path that names a real screen is a deliberate link (a reload, a bookmark, a
 * shared URL) and is restored.
 */
export function initialScreenFromPath(pathname: string, base: string = BASE): string | null {
  if (/^\/*$/.test(withoutBase(pathname, base))) return null;
  return routeIdFromPath(pathname, base);
}

/**
 * The first screen a load opens, from the URL plus what the browser holds.
 *
 * Order matters. An auth path names its own screen, so `/login` survives a
 * reload. A console URL with a credential opens the console. A console URL
 * with **no** credential is a sign-in: it used to open the landing page (the
 * link the user followed looked ignored), and if any token-shaped value
 * survived it opened the shell instead, whose every panel then rendered the
 * backend's 401 copy with nothing on screen able to sign out. The mount root
 * stays public — `/` is the landing page, not a screen link.
 */
export function bootRouteFromPath(
  pathname: string,
  options: { hasToken: boolean; startAuthenticated?: boolean; base?: string } = { hasToken: false }
): 'app' | 'signin' | 'signup' | 'landing' {
  const { hasToken, startAuthenticated = false, base = BASE } = options;
  const authRoute = authRouteFromPath(pathname, base);
  if (authRoute === 'signin' || authRoute === 'signup') return authRoute;
  if (startAuthenticated) return 'app';
  if (hasToken) return 'app';
  return initialScreenFromPath(pathname, base) ? 'signin' : 'landing';
}
