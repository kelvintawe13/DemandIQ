import React, { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Sidebar } from './Sidebar';
import { MobileNav } from './MobileNav';
import { PageHeader } from './PageHeader';
import { PageFooter } from './PageFooter';
import { SiteBanner } from './SiteBanner';
import { pageMeta } from '../data/pages';
import { navRootFor } from '../data/navItems';
import { AgentPanel } from './agent/AgentPanel';
import { useNavBadges } from '../hooks/useNavBadges';

interface AppShellProps {
  activeNavId: string;
  onNavigate: (id: string) => void;
  children: React.ReactNode;
}

/**
 * Rendered once for the whole authenticated app: the sidebar, header, footer
 * and bottom nav stay mounted, so navigating only swaps the content region.
 */
export function AppShell({ activeNavId, onNavigate, children }: AppShellProps) {
  // The sidebar opens at full width regardless of the assistant rail: the rail
  // is a default, not a reason to take navigation away. Collapsing stays the
  // user's call.
  const [collapsed, setCollapsed] = useState(false);
  const reduced = useReducedMotion();
  // Real counts (unread alerts / pending approvals), fetched once for both
  // navigation surfaces instead of per-component.
  const badges = useNavBadges();

  const meta = pageMeta[activeNavId] ?? pageMeta.overview;
  // A sub-route keeps its parent lit: on the add-location step, the sidebar
  // should still say where in the app you are rather than nothing at all.
  const navRoot = navRootFor(activeNavId);

  return (
    <div className="app-viewport flex w-full overflow-hidden bg-slate-50 text-slate-900">
      <Sidebar
        activeItemId={navRoot}
        onSelect={onNavigate}
        collapsed={collapsed}
        badges={badges}
        onToggleCollapsed={() => setCollapsed((value) => !value)} />
      

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <SiteBanner />
        <PageHeader title={meta.title} subtitle={meta.subtitle} icon={meta.icon} />

        <main className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-3 pb-6 pt-3 sm:px-4 sm:pb-5 lg:px-5">
          {/* Only the content region animates; the shell around it never moves. */}
          <motion.div
            key={activeNavId}
            initial={reduced ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}>
            
            {children}
          </motion.div>
        </main>

        <PageFooter>
          <p className="truncate">{meta.footerLeft}</p>
          <span className="truncate">{meta.footerRight}</span>
        </PageFooter>

        <MobileNav activeItemId={navRoot} onSelect={onNavigate} badges={badges} />
      </div>

      {/* Assistant: docked third column from xl, overlay drawer below it. */}
      <AgentPanel activeNavId={activeNavId} onNavigate={onNavigate} />
    </div>);

}