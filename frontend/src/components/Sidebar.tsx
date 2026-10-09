import { useMemo, useRef, useState } from 'react';
import {
  SearchIcon,
  XIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon } from
'lucide-react';
import { sectionsForRole } from '../data/navItems';
import { useLocale } from '../contexts/LocaleContext';
import { useSession } from '../contexts/SessionContext';
import { UserMenu } from './chrome/UserMenu';

interface SidebarProps {
  activeItemId: string;
  onSelect: (id: string) => void;
  collapsed: boolean;
  /** Live counts by nav item id (`useNavBadges`); absent means no badge. */
  badges?: Record<string, number>;
  onToggleCollapsed: () => void;
}

export function Sidebar({
  activeItemId,
  onSelect,
  collapsed,
  badges = {},
  onToggleCollapsed
}: SidebarProps) {
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement | null>(null);
  const { t } = useLocale();
  const { user } = useSession();

  const sections = useMemo(() => {
    const allowed = sectionsForRole(user.role, user.isSiteAdmin === true);
    const needle = query.trim().toLowerCase();
    if (!needle) return allowed;
    return allowed.
    map((section) => ({
      ...section,
      items: section.items.filter(
        (item) =>
        t(item.labelKey).toLowerCase().includes(needle) ||
        item.label.toLowerCase().includes(needle)
      )
    })).
    filter((section) => section.items.length > 0);
  }, [query, t, user.role, user.isSiteAdmin]);

  const hasResults = sections.length > 0;

  return (
    <aside
      className={[
      'hidden shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white pb-3 pt-4 transition-[width] duration-200 ease-out md:flex',
      collapsed ? 'w-[72px] px-2' : 'w-[236px] px-3'].
      join(' ')}>
      
      {/* Brand + collapse */}
      <div
        className={[
        'flex shrink-0 items-center',
        collapsed ? 'flex-col gap-2' : 'justify-between gap-2 px-1'].
        join(' ')}>
        
        <span className="flex items-center gap-2">
          <svg
            viewBox="0 0 24 24"
            className="h-6 w-6 shrink-0 text-brand-600"
            aria-hidden="true"
            fill="currentColor">
            
            <rect x="2" y="9" width="3.6" height="12" rx="1.2" />
            <rect x="7.6" y="4" width="3.6" height="17" rx="1.2" />
            <rect x="13.2" y="12" width="3.6" height="9" rx="1.2" opacity="0.65" />
          </svg>
          {!collapsed &&
          <span className="text-h4 font-bold tracking-tight text-brand-800">
              Demand<span className="text-brand-500">IQ</span>
            </span>
          }
        </span>

        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? t('sidebar.expand') : t('sidebar.collapse')}
          title={collapsed ? t('sidebar.expand') : t('sidebar.collapse')}
          className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          {collapsed ?
          <PanelLeftOpenIcon className="h-[18px] w-[18px]" strokeWidth={1.75} /> :

          <PanelLeftCloseIcon className="h-[18px] w-[18px]" strokeWidth={1.75} />
          }
        </button>
      </div>

      {/* Search */}
      <div className="mt-4 shrink-0">
        {collapsed ?
        <button
          type="button"
          aria-label={t('sidebar.search')}
          title={t('sidebar.search')}
          onClick={() => {
            onToggleCollapsed();
            window.setTimeout(() => searchRef.current?.focus(), 220);
          }}
          className="flex w-full items-center justify-center rounded-lg border border-slate-200 py-2 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
            <SearchIcon className="h-4 w-4" strokeWidth={1.9} />
          </button> :

        <div className="flex items-center rounded-lg border border-slate-200 bg-white focus-within:border-brand-500 focus-within:ring-1 focus-within:ring-brand-500">
            <SearchIcon className="ml-2.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.9} />
            <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('sidebar.search')}
            aria-label={t('sidebar.search')}
            className="w-full min-w-0 bg-transparent px-2 py-2 text-meta text-slate-800 placeholder:text-slate-500 focus:outline-none [&::-webkit-search-cancel-button]:hidden" />
          
            {query &&
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => setQuery('')}
            className="mr-1.5 rounded p-1 text-slate-500 transition-colors duration-150 ease-out hover:text-slate-700">
            
                <XIcon className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
          }
          </div>
        }
      </div>

      {/* Nav */}
      <nav
        aria-label="Main"
        className="mt-3 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pb-2 pr-0.5">
        
        {sections.map((section) =>
        <div key={section.id}>
            {!collapsed &&
          <p className="px-2 pb-1 text-fine font-semibold uppercase tracking-wide text-slate-500">
                {t(section.titleKey)}
              </p>
          }
            <ul className="flex flex-col gap-0.5">
              {section.items.map((item) => {
              const Icon = item.icon;
              const isActive = item.id === activeItemId;
              const label = t(item.labelKey);
              return (
                <li key={item.id}>
                    <button
                    type="button"
                    aria-current={isActive ? 'page' : undefined}
                    title={collapsed ? label : undefined}
                    onClick={() => onSelect(item.id)}
                    className={[
                    'flex w-full items-center rounded-lg text-left text-body-sm transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    collapsed ? 'justify-center px-2 py-2.5' : 'gap-2.5 px-2.5 py-2',
                    isActive ?
                    'bg-brand-700 font-semibold text-white' :
                    'text-slate-700 hover:bg-slate-100'].
                    join(' ')}>
                    
                      <Icon className="h-[17px] w-[17px] shrink-0" strokeWidth={1.8} />
                      {!collapsed &&
                    <>
                          <span className="min-w-0 flex-1 truncate">{label}</span>
                          {badges[item.id] ?
                      <span
                        className={[
                        'flex h-4 min-w-[16px] items-center justify-center rounded-full px-1 text-fine font-semibold',
                        isActive ?
                        'bg-white/20 text-white' :
                        'bg-brand-100 text-brand-800'].
                        join(' ')}>
                        
                              {badges[item.id]}
                            </span>
                      : null}
                        </>
                    }
                    </button>
                  </li>);

            })}
            </ul>
          </div>
        )}

        {!hasResults && !collapsed &&
        <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-caption text-slate-500">
            {t('sidebar.noResults')} “{query}”
          </p>
        }
      </nav>

      {/* Footer */}
      <div className="mt-2 flex shrink-0 flex-col gap-2 border-t border-slate-200 pt-3">
        <UserMenu collapsed={collapsed} />
      </div>
    </aside>);

}