import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { MoreHorizontalIcon, XIcon, LogOutIcon } from 'lucide-react';
import { itemsForRole } from '../data/navItems';
import { useLocale } from '../contexts/LocaleContext';
import { useSession } from '../contexts/SessionContext';

// Each role leads with the work it owns; ids absent from a role's own nav are
// filtered out below, so a shorter list is harmless.
const PRIMARY_BY_ROLE: Record<string, string[]> = {
  Administrator: ['admin-models', 'admin-datasets', 'admin-policies', 'admin-connectors'],
  Owner: ['overview', 'analytics', 'forecast', 'reports'],
  Analyst: ['analytics', 'forecast', 'quality', 'reports'],
  'Data Clerk': ['ingestion', 'quality', 'alerts', 'overview'],
  Viewer: ['overview', 'analytics', 'forecast'],
  default: ['overview', 'analytics', 'ingestion', 'alerts']
};

interface MobileNavProps {
  activeItemId: string;
  onSelect: (id: string) => void;
  /** Live counts by nav item id (`useNavBadges`); absent means no dot. */
  badges?: Record<string, number>;
}

export function MobileNav({ activeItemId, onSelect, badges = {} }: MobileNavProps) {
  const [isSheetOpen, setIsSheetOpen] = useState(false);
  const { t } = useLocale();
  const { user, signOut } = useSession();

  const navItems = itemsForRole(user.role, user.isSiteAdmin === true);
  const primaryIds = PRIMARY_BY_ROLE[user.role] ?? PRIMARY_BY_ROLE.default;

  const primaryItems = primaryIds.
  map((id) => navItems.find((item) => item.id === id)).
  filter((item): item is (typeof navItems)[number] => Boolean(item));
  const overflowItems = navItems.filter((item) => !primaryIds.includes(item.id));
  const isOverflowActive = overflowItems.some((item) => item.id === activeItemId);

  const handleSelect = (id: string) => {
    onSelect(id);
    setIsSheetOpen(false);
  };

  return (
    <>
      <AnimatePresence>
        {isSheetOpen &&
        <>
            <motion.div
            key="scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
            className="fixed inset-0 z-40 bg-slate-900/40 md:hidden"
            onClick={() => setIsSheetOpen(false)} />
          
            <motion.div
            key="sheet"
            initial={{ y: 24, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 24, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            role="dialog"
            aria-label={t('nav.more')}
            className="safe-bottom-offset fixed inset-x-0 z-40 max-h-[70vh] overflow-y-auto rounded-t-2xl border-t border-slate-200 bg-white p-3 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] md:hidden">
            
              <div className="mb-2 flex items-center justify-between gap-3 px-1">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-700 text-fine font-semibold text-white">
                    {user.initials}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-meta font-semibold text-slate-900">
                      {user.name}
                    </span>
                    <span className="block truncate text-fine text-slate-500">
                      {user.role}
                    </span>
                  </span>
                </span>
                <button
                type="button"
                aria-label="Close"
                onClick={() => setIsSheetOpen(false)}
                className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100">
                
                  <XIcon className="h-4 w-4" />
                </button>
              </div>

              <ul className="grid grid-cols-2 gap-1.5">
                {overflowItems.map((item) => {
                const Icon = item.icon;
                const isActive = item.id === activeItemId;
                return (
                  <li key={item.id}>
                      <button
                      type="button"
                      onClick={() => handleSelect(item.id)}
                      className={[
                      'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-meta transition-colors duration-150 ease-out',
                      isActive ?
                      'bg-brand-700 font-semibold text-white' :
                      'text-slate-700 hover:bg-slate-100'].
                      join(' ')}>
                      
                        <Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} />
                        <span className="truncate">{t(item.labelKey)}</span>
                      </button>
                    </li>);

              })}
              </ul>

              <button
              type="button"
              onClick={signOut}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-red-200 px-3 py-2 text-meta font-semibold text-red-600 transition-colors duration-150 ease-out hover:bg-red-50">
              
                <LogOutIcon className="h-4 w-4" strokeWidth={1.9} />
                {t('menu.signout')}
              </button>
            </motion.div>
          </>
        }
      </AnimatePresence>

      <nav
        aria-label="Main"
        className="safe-bottom z-50 flex h-[60px] shrink-0 items-stretch border-t border-slate-200 bg-white px-1 md:hidden">
        
        {primaryItems.map((item) => {
          const Icon = item.icon;
          const isActive = item.id === activeItemId && !isSheetOpen;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={isActive ? 'page' : undefined}
              onClick={() => handleSelect(item.id)}
              className="relative flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-lg px-1 py-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              
              <Icon
                className={[
                'h-[18px] w-[18px] shrink-0',
                isActive ? 'text-brand-700' : 'text-slate-500'].
                join(' ')}
                strokeWidth={isActive ? 2.25 : 1.75} />
              
              {badges[item.id] ?
              <span className="absolute right-[18%] top-1.5 h-1.5 w-1.5 rounded-full bg-brand-500" />
              : null}
              <span
                className={[
                'w-full truncate text-center text-fine leading-none',
                isActive ? 'font-semibold text-brand-700' : 'text-slate-500'].
                join(' ')}>
                
                {t(item.labelKey)}
              </span>
            </button>);

        })}

        <button
          type="button"
          aria-expanded={isSheetOpen}
          onClick={() => setIsSheetOpen((open) => !open)}
          className="flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-lg px-1 py-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          <MoreHorizontalIcon
            className={[
            'h-[18px] w-[18px]',
            isSheetOpen || isOverflowActive ? 'text-brand-700' : 'text-slate-500'].
            join(' ')}
            strokeWidth={isSheetOpen || isOverflowActive ? 2.25 : 1.75} />
          
          <span
            className={[
            'text-fine leading-none',
            isSheetOpen || isOverflowActive ?
            'font-semibold text-brand-700' :
            'text-slate-500'].
            join(' ')}>
            
            {t('nav.more')}
          </span>
        </button>
      </nav>
    </>);

}