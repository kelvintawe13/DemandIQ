/**
 * Google Identity Services (GIS) loader + One Tap / button helpers.
 *
 * Loads https://accounts.google.com/gsi/client once, then either runs the
 * One Tap prompt or renders the official Google button into a container.
 * The resulting `id_token` credential is posted to the backend's
 * /auth/oauth/google (verified in backend/app/security/human.py).
 *
 * 403 "The given origin is not allowed for the given client ID" on
 * localhost:5173 means the OAuth client has no Authorized JavaScript origin
 * for that origin. Fix in Google Cloud Console → APIs & Services →
 * Credentials → OAuth 2.0 Client IDs → Authorized JavaScript origins:
 *   http://localhost:5173
 *   http://localhost:5174
 * (plus any deployed origin). No code change can bypass it.
 */

const GSI_SRC = 'https://accounts.google.com/gsi/client';
const RAW_CLIENT_ID = (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) ?? '';
const CLIENT_ID = RAW_CLIENT_ID.trim();

export interface GoogleCredential {
  credential: string; // the id_token (JWT)
}

interface GsiButtonOptions {
  width?: number;
  text?: 'signin_with' | 'signup_with' | 'continue_with';
}

type GoogleAccountsApi = {
  accounts: {
    id: {
      initialize: (config: {
        client_id: string;
        callback: (response: GoogleCredential) => void;
        auto_select?: boolean;
        cancel_on_tap_outside?: boolean;
        use_fedcm_for_prompt?: boolean;
      }) => void;
      prompt: (listener?: (notification: { isNotDisplayed?: () => boolean; getSkippedReason?: () => string }) => void) => void;
      renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
    };
  };
};

declare global {
  interface Window {
    google?: GoogleAccountsApi;
    onGoogleGsiLoaded?: () => void;
  }
}

let gsiPromise: Promise<GoogleAccountsApi> | null = null;

/** True when a client ID is configured (non-empty after trim). */
export function isGoogleConfigured(): boolean {
  return CLIENT_ID !== '';
}

/** Raw client ID for diagnostics (never log in production). */
export function getGoogleClientId(): string {
  return CLIENT_ID;
}

/**
 * Detect the GSI "origin not allowed" 403. Covers all variants seen in the
 * wild: the GSI_LOGGER prefix, the plain text message, and the iframe URL
 * `https://accounts.google.com/gsi/button?... -> 403`.
 */
export function isGsiOriginBlockedMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    (m.includes('gsi_logger') && m.includes('origin is not allowed')) ||
    m.includes('the given origin is not allowed for the given client id') ||
    (m.includes('gsi/button') && m.includes('403')) ||
    (m.includes('idpiframe') && m.includes('origin'))
  );
}

function loadGsi(): Promise<GoogleAccountsApi> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  if (!gsiPromise) {
    gsiPromise = new Promise((resolve, reject) => {
      window.onGoogleGsiLoaded = () => {
        if (window.google?.accounts?.id) resolve(window.google);
        else reject(new Error('GSI loaded but API missing'));
      };
      const script = document.createElement('script');
      script.src = GSI_SRC;
      script.async = true;
      script.defer = true;
      script.onload = () => window.onGoogleGsiLoaded?.();
      script.onerror = () => {
        gsiPromise = null;
        reject(new Error('Failed to load Google Identity Services'));
      };
      document.head.appendChild(script);
    });
  }
  return gsiPromise;
}

function onCredential(credential: string) {
  window.dispatchEvent(new CustomEvent('demandiq:google-credential', { detail: credential }));
}

/** Show the One Tap prompt (or Chrome FedCM prompt) for sign-in. */
export async function promptGoogleOneTap(): Promise<void> {
  if (!isGoogleConfigured()) throw new Error('Google Sign-In not configured (VITE_GOOGLE_CLIENT_ID missing)');
  const gsi = await loadGsi();
  gsi.accounts.id.initialize({
    client_id: CLIENT_ID,
    callback: (response) => onCredential(response.credential),
    cancel_on_tap_outside: true,
    use_fedcm_for_prompt: true,
  });
  gsi.accounts.id.prompt();
}

/** Render the official Google button into `parent` (fallback for browsers without One Tap). */
export async function renderGoogleButton(parent: HTMLElement, options: GsiButtonOptions = {}): Promise<void> {
  if (!isGoogleConfigured()) throw new Error('Google Sign-In not configured');
  const gsi = await loadGsi();
  // Both calls can synchronously log GSI_LOGGER errors if the origin is
  // blocked — caller should have installed the GSI_LOGGER suppressor and
  // should .catch() to show the friendly fallback.
  gsi.accounts.id.initialize({
    client_id: CLIENT_ID,
    callback: (response) => onCredential(response.credential),
    cancel_on_tap_outside: true,
  });
  gsi.accounts.id.renderButton(parent, {
    theme: 'outline',
    size: 'large',
    shape: 'pill',
    text: options.text ?? 'continue_with',
    logo_alignment: 'left',
    width: options.width ?? 320,
  });
}

/** Subscribe to credential events; returns an unsubscribe function. */
export function onGoogleCredential(handler: (credential: string) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<string>).detail);
  window.addEventListener('demandiq:google-credential', listener);
  return () => window.removeEventListener('demandiq:google-credential', listener);
}
