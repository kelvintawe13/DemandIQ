import { useEffect } from 'react';

/** Suffix applied to every page title. */
const SUFFIX = 'DemandIQ';

/**
 * Canonical title/description map for the outer (unauthenticated) routes.
 * Inner app routes are resolved from `pageMeta` in `data/pages.ts`.
 */
export const routeTitles: Record<string, { title: string; description: string }> = {
  landing: {
    title: 'Explainable Demand Forecasting for Food-Waste Prevention',
    description:
      'DemandIQ pairs LightGBM/CatBoost demand forecasting with adaptive conformal intervals and SR/PS/UD policy simulation (Recruit + URBAN-WASTE).',
  },
  signin: {
    title: 'Sign In',
    description: 'Sign in to DemandIQ, your demand forecasting and preparation-policy workspace.',
  },
  signup: {
    title: 'Create Account',
    description:      'Create your DemandIQ workspace and start forecasting demand and simulating preparation policies.',
  },
  reset: {
    title: 'Reset Password',
    description: 'Reset your DemandIQ password for secure access to your forecasting workspace.',
  },
  'check-email': {
    title: 'Check Your Email',
    description: 'We sent a verification link. Check your inbox to continue to DemandIQ.',
  },
  verify: {
    title: 'Verify Email',
    description: 'Verify your email to activate your DemandIQ workspace.',
  },
  'two-factor': {
    title: 'Two-Factor Authentication',
    description: 'Enter your 6-digit code to complete sign-in to DemandIQ.',
  },
  'all-set': {
    title: 'All Set',
    description: 'Your DemandIQ workspace is ready. Enter the app.',
  },
  callback: {
    title: 'Signing In',
    description: 'Signing you in to DemandIQ…',
  },
};

export interface PageMetaInput {
  title: string;
  subtitle: string;
}

function upsertMeta(selector: string, content: string) {
  const el = document.querySelector(selector) as HTMLMetaElement | null;
  if (el) el.setAttribute('content', content);
}

function formatTitle(pageTitle: string) {
  return `${pageTitle} · ${SUFFIX}`;
}

/**
 * Sync document.title + primary SEO tags.
 * Call from the router whenever `route` or `activeNavId` changes.
 */
export function syncDocumentMeta(opts: {
  route: string;
  pageMeta?: PageMetaInput;
  fallbackTitle?: string;
}) {
  const { route, pageMeta, fallbackTitle } = opts;

  let title: string;
  let description: string;

  if (route !== 'app' && routeTitles[route]) {
    const r = routeTitles[route];
    title = formatTitle(r.title);
    description = r.description;
  } else if (pageMeta) {
    title = formatTitle(pageMeta.title);
    description = pageMeta.subtitle;
  } else {
    title = fallbackTitle ? formatTitle(fallbackTitle) : `DemandIQ · Explainable Demand Forecasting for Food-Waste Prevention`;
    description =
      'Explainable ML decision-support for restaurant demand forecasting (LightGBM/CatBoost, conformal prediction, SHAP) and SR/PS/UD policy simulation.';
  }

  document.title = title;

  // Standard description
  upsertMeta('meta[name="description"]', description);
  upsertMeta('meta[name="title"]', title);
  // Open Graph / Twitter
  upsertMeta('meta[property="og:title"]', title);
  upsertMeta('meta[property="og:description"]', description);
  upsertMeta('meta[name="twitter:title"]', title);
  upsertMeta('meta[name="twitter:description"]', description);
}

/** React hook wrapper: updates on mount + when deps change. */
export function useDocumentTitle(opts: {
  route: string;
  pageMeta?: PageMetaInput;
  fallbackTitle?: string;
}) {
  const { route, pageMeta, fallbackTitle } = opts;
  useEffect(() => {
    syncDocumentMeta({ route, pageMeta, fallbackTitle });
    // `pageMeta` is a fresh object on every render (built from data/pages), so
    // depending on it would write the DOM on every render. Its two consumed
    // fields ARE the dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, pageMeta?.title, pageMeta?.subtitle, fallbackTitle]);
}
