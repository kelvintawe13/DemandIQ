import { ApiException } from '../../api/client';
import type { EmptyStateVariant } from '../ui/EmptyState';

export interface LoadError {
  variant: Extract<EmptyStateVariant, 'error' | 'offline'>;
  title: string;
  desc: string;
}

/**
 * No network vs refused — the split every page's `errMeta` makes.
 *
 * One implementation for the fleet console and its panels: "you are offline"
 * and "the API said no" call for different advice, and a screen that collapses
 * them into one message leaves an operator reloading a request the server will
 * refuse again. The reference travels with the message because a refusal the
 * operator cannot quote is a refusal they cannot escalate.
 */
export function loadErrorFrom(e: unknown, title: string): LoadError {
  if (e instanceof ApiException) {
    const offline = !navigator.onLine || e.status === 0;
    return {
      variant: offline ? 'offline' : 'error',
      title: offline ? 'You are offline' : title,
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  // A rejected fetch surfaces as a TypeError when the request never left.
  const offline = !navigator.onLine || e instanceof TypeError;
  return {
    variant: offline ? 'offline' : 'error',
    title: offline ? 'You are offline' : title,
    desc: offline ? 'Check your connection.' : e instanceof Error ? e.message : 'Failed to load',
  };
}

/** The message from a write that failed, for a toast. */
export function actionErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiException) return e.error.message;
  if (e instanceof Error) return e.message;
  return fallback;
}
