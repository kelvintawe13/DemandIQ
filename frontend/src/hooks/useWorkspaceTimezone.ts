import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { getWorkspaceTimezone, setWorkspaceTimezone } from '../lib/workspaceTime';

/**
 * The workspace's timezone, resolved once per authenticated page load.
 * Pass `enabled = false` on the public shell (no session yet) so the read is
 * deferred until sign-in rather than issued — and rejected — unauthenticated.
 *
 * Every screen that decides a calendar day reads `lib/workspaceTime` rather than
 * hardcoding `Africa/Kigali`. This hook is what makes that zone the *workspace's*
 * configured one (`workspaces.timezone`) instead of only its default: it reads
 * `GET /workspace` once, adopts whatever it answers, and re-renders its caller so
 * the change reaches the pages in the same session. A failed or blank read keeps
 * the documented default — the app must never be left with no zone.
 */
const WORKSPACE_TIMEZONE_CHANGED = 'demandiq:workspace-timezone-changed';

/** One read per page load, however many components mount the hook. */
let requested = false;

/**
 * Adopt a timezone and tell every mounted reader, so a change made in Settings
 * (or by a save that already returns the new value) takes effect without a
 * reload. Blank values are ignored by `setWorkspaceTimezone`.
 */
export function applyWorkspaceTimezone(timezone?: string | null): void {
  setWorkspaceTimezone(timezone);
  window.dispatchEvent(new Event(WORKSPACE_TIMEZONE_CHANGED));
}

/** Read the workspace profile and adopt its timezone. Best-effort by design. */
export async function loadWorkspaceTimezone(): Promise<string | undefined> {
  try {
    const workspace = await api.get<{ timezone?: string | null }>('/api/v1/workspace');
    if (workspace?.timezone) {
      applyWorkspaceTimezone(workspace.timezone);
      return workspace.timezone;
    }
  } catch {
    // Offline, or a backend without `/workspace`: the default stands.
  }
  return undefined;
}

export function useWorkspaceTimezone(enabled = true): string {
  const [timezone, setTimezone] = useState(getWorkspaceTimezone);

  useEffect(() => {
    const sync = () => setTimezone(getWorkspaceTimezone());
    window.addEventListener(WORKSPACE_TIMEZONE_CHANGED, sync);
    // Only read `/workspace` once an authenticated seat exists. The public
    // landing / sign-in / sign-up shell mounts this hook too, and a request
    // without a Bearer token is a guaranteed 401 — noise on the console and a
    // wasted round-trip before the user has even signed in. `requested` is left
    // unset while disabled so the read still happens as soon as a session
    // appears (the effect re-runs when `enabled` flips).
    if (enabled && !requested) {
      requested = true;
      void loadWorkspaceTimezone().then((resolved) => {
        if (resolved) setTimezone(resolved);
      });
    }
    return () => window.removeEventListener(WORKSPACE_TIMEZONE_CHANGED, sync);
  }, [enabled]);

  return timezone;
}
