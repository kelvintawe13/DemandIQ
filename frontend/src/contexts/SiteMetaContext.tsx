import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';

export interface SiteMeta {
  registration_open: boolean;
  announcement: string;
  support_email: string;
  maintenance_mode: boolean;
}

interface SiteMetaContextValue {
  meta: SiteMeta | null;
  /** Re-fetch from the server (used after admin edits). */
  refresh: () => Promise<void>;
  /** Optimistically update the banner for every mounted surface. */
  applyLocal: (patch: Partial<SiteMeta>) => void;
}

const SiteMetaContext = createContext<SiteMetaContextValue>({
  meta: null,
  refresh: async () => undefined,
  applyLocal: () => undefined,
});

/**
 * Shared, app-wide view of the public site meta (announcement, maintenance,
 * registration). The banner renders from it; the Site Admin console writes
 * through it so a saved setting is visible without a page reload.
 */
export function SiteMetaProvider({ children }: { children: React.ReactNode }) {
  const [meta, setMeta] = useState<SiteMeta | null>(null);

  const refresh = useCallback(async () => {
    try {
      const m = await api.get<SiteMeta>('/api/v1/admin/site/meta');
      setMeta(m);
    } catch {
      // banner is cosmetic — never block the app
    }
  }, []);

  const applyLocal = useCallback((patch: Partial<SiteMeta>) => {
    setMeta((current) => (current ? { ...current, ...patch } : current));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(() => ({ meta, refresh, applyLocal }), [meta, refresh, applyLocal]);
  return <SiteMetaContext.Provider value={value}>{children}</SiteMetaContext.Provider>;
}

export function useSiteMeta() {
  return useContext(SiteMetaContext);
}
