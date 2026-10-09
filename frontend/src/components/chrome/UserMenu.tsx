import { useCallback, useState } from 'react';
import {
  ChevronDownIcon,
  UserIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  CreditCardIcon,
  MonitorIcon,
  LogOutIcon } from
'lucide-react';
import { useDismissable } from '../../hooks/useDismissable';
import { useLocale } from '../../contexts/LocaleContext';
import { useSession } from '../../contexts/SessionContext';
import { useNavigation } from '../../contexts/NavigationContext';

interface UserMenuProps {
  collapsed?: boolean;
}

export function UserMenu({ collapsed = false }: UserMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const close = useCallback(() => setIsOpen(false), []);
  const ref = useDismissable<HTMLDivElement>(isOpen, close);
  const { t } = useLocale();
  const { user, signOut } = useSession();
  const { activeId, navigate } = useNavigation();

  // Each entry resolves to a real destination: a dedicated screen, or the
  // matching Settings section.
  const items = [
  { id: 'profile', route: 'profile', label: t('menu.profile'), icon: UserIcon },
  { id: 'devices', route: 'devices', label: t('menu.devices'), icon: MonitorIcon },
  { id: 'account', route: 'settings', label: t('menu.account'), icon: SettingsIcon },
  {
    id: 'preferences',
    route: 'settings-preferences',
    label: t('menu.preferences'),
    icon: SlidersHorizontalIcon
  },
  { id: 'billing', route: 'billing', label: t('menu.billing'), icon: CreditCardIcon }];


  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        title={collapsed ? user.name : undefined}
        className={[
        'flex w-full items-center rounded-xl border border-slate-200 bg-white text-left transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
        collapsed ? 'justify-center p-2' : 'gap-2.5 px-2.5 py-2'].
        join(' ')}>
        
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-700 text-micro font-semibold text-white">
          {user.initials}
        </span>
        {!collapsed &&
        <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body-xs font-semibold text-slate-900">
                {user.name}
              </span>
              <span className="block truncate text-micro text-slate-500">
                {user.role}
              </span>
            </span>
            <ChevronDownIcon
            className={[
            'h-4 w-4 shrink-0 text-slate-500 transition-transform duration-150 ease-out',
            isOpen ? 'rotate-180' : ''].
            join(' ')} />
          
          </>
        }
      </button>

      {isOpen &&
      <div
        role="menu"
        className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-[224px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
        
          <div className="border-b border-slate-100 px-3 py-2">
            <p className="truncate text-meta font-semibold text-slate-900">
              {user.name}
            </p>
            <p className="truncate text-micro text-slate-500">{user.email}</p>
          </div>

          {items.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              aria-current={activeId === item.route ? 'page' : undefined}
              onClick={() => {
                navigate(item.route);
                close();
              }}
              className={[
              'flex w-full items-center gap-2.5 px-3 py-2 text-left text-meta transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:bg-slate-50',
              activeId === item.route ?
              'bg-brand-50 font-semibold text-brand-800' :
              'text-slate-700'].
              join(' ')}>
              
                <Icon
                className={[
                'h-4 w-4 shrink-0',
                activeId === item.route ? 'text-brand-600' : 'text-slate-500'].
                join(' ')}
                strokeWidth={1.75} />
              
                {item.label}
              </button>);

        })}

          <div className="mt-1 border-t border-slate-100 pt-1">
            <button
            type="button"
            role="menuitem"
            onClick={() => {
              close();
              signOut();
            }}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-meta font-medium text-red-600 transition-colors duration-150 ease-out hover:bg-red-50 focus:outline-none focus-visible:bg-red-50">
            
              <LogOutIcon className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              {t('menu.signout')}
            </button>
          </div>
        </div>
      }
    </div>);

}