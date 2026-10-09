/**
 * Session-expiry event bridge.
 *
 * Decouples the framework-free API client from React: on an unrecoverable
 * 401 (silent refresh failed) the client dispatches a window event, and any
 * subscribed UI (session context → toast, router → redirect) reacts. A guard
 * keeps repeat events within one tick from spamming the UI.
 */

const EVENT = 'demandiq:session-expired';

/**
 * Guards repeat events within one tick. A module-level flag rather than a
 * property hung off the function: the property form was invisible to the type
 * checker (`Property 'fired' does not exist`).
 */
let firedThisTick = false;

/** Fire when the session can no longer be refreshed. Idempotent per tick. */
export function notifySessionExpired(): void {
  if (firedThisTick) return;
  firedThisTick = true;
  window.setTimeout(() => {
    firedThisTick = false;
  }, 0);
  window.dispatchEvent(new CustomEvent(EVENT));
}

/** Subscribe to expiry events; returns an unsubscribe function. */
export function onSessionExpired(handler: () => void): () => void {
  const listener = () => handler();
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}

/** Shown on the sign-in screen after an expiry redirect. */
export const SESSION_EXPIRED_MESSAGE = 'Your session has expired. Please sign in again.';
