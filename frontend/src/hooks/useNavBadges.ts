import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { itemsForRole } from '../data/navItems';
import { useSession } from '../contexts/SessionContext';

/**
 * Live counts for the navigation badges.
 *
 * These counts used to be hardcoded numbers in navItems, so the sidebar
 * claimed unread alerts and pending approvals that the workspace did not have
 * — and contradicted the notification panel, which reads the same data. Each
 * badge now comes from the endpoint that owns it, and only the badges this
 * role can actually see are fetched. A failed request shows no badge rather
 * than a stale number.
 */
const NAV_BADGES_CHANGED = 'demandiq:nav-badges-changed';

const POLL_MS = 60000;

/** Exact unread total from GET /alerts (unread = not read and not resolved). */
async function fetchUnreadAlerts(): Promise<number | undefined> {
  try {
    const res = await api.get<{ unread_count?: number; data?: unknown[] }>('/api/v1/alerts', {
      status: 'unread',
      limit: 1,
    });
    if (typeof res?.unread_count === 'number') return res.unread_count;
    // Older API shape: fall back to the page we asked for.
    return Array.isArray(res?.data) ? res.data.length : undefined;
  } catch {
    return undefined;
  }
}

/** Pending approvals are change requests still awaiting a decision. */
async function fetchPendingApprovals(): Promise<number | undefined> {
  try {
    const res = await api.get<{ data?: { status?: string }[] }>('/api/v1/change-requests', {
      status: 'pending',
    });
    if (!Array.isArray(res?.data)) return undefined;
    return res.data.filter((row) => (row?.status ?? 'pending') === 'pending').length;
  } catch {
    return undefined;
  }
}

/**
 * Counts keyed by nav item id, for the items this session can see. Rendered by
 * both the sidebar and the mobile nav, which sit under the same shell, so the
 * fetches happen once per shell rather than once per navigation surface.
 */
export function useNavBadges(): Record<string, number> {
  const session = useSession();
  const { user } = session;
  // isAuthenticated may be absent in test mocks — fall back to id presence.
  const isAuthenticated = (session as unknown as { isAuthenticated?: boolean }).isAuthenticated ?? Boolean(user.id);
  const visibleIds = itemsForRole(user.role, user.isSiteAdmin === true).map((item) => item.id);
  // Before /auth/me hydrates, the shell renders the placeholder seat — whose
  // role can be broader than the signed-in user's. Fetching then issued calls
  // the real user is not allowed to make (a data clerk asking for change
  // requests). A placeholder seat carries no id, so that is the gate.
  // Also require a live session — otherwise every 60s poll would spam
  // `GET /alerts 401` (client.ts:117) on the landing screen.
  const hydrated = Boolean(user.id) && isAuthenticated;
  const wantsAlerts = hydrated && visibleIds.includes('alerts');
  const wantsApprovals = hydrated && visibleIds.includes('admin-approvals');
  const [badges, setBadges] = useState<Record<string, number>>({});
  // The role is not stable on first paint: the demo seat placeholder and the
  // hydrated session disagree, so `load` can run twice with different targets.
  // Only the newest run may publish — an older run resolving late used to
  // overwrite the fresh counts with its own partial set and blank the badge.
  const runRef = useRef(0);

  const load = useCallback(async () => {
    const run = ++runRef.current;
    const [alerts, approvals] = await Promise.all([
      wantsAlerts ? fetchUnreadAlerts() : undefined,
      wantsApprovals ? fetchPendingApprovals() : undefined,
    ]);
    if (run !== runRef.current) return;
    const next: Record<string, number> = {};
    if (typeof alerts === 'number') next.alerts = alerts;
    if (typeof approvals === 'number') next['admin-approvals'] = approvals;
    setBadges(next);
  }, [wantsAlerts, wantsApprovals]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => {
      void load();
    }, POLL_MS);
    const onChanged = () => {
      void load();
    };
    window.addEventListener(NAV_BADGES_CHANGED, onChanged);
    return () => {
      window.clearInterval(id);
      window.removeEventListener(NAV_BADGES_CHANGED, onChanged);
    };
  }, [load]);

  return badges;
}

/**
 * Announce that a badge's underlying data changed, so a page that just marked
 * alerts read doesn't leave a stale count in the sidebar for up to a minute.
 */
export function notifyNavBadgesChanged(): void {
  window.dispatchEvent(new Event(NAV_BADGES_CHANGED));
}
