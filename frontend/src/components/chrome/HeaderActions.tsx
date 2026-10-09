import { useCallback, useEffect, useState, useMemo } from 'react';
import type { ChangeEvent } from 'react';
import {
  BellIcon,
  HelpCircleIcon,
  SunIcon,
  MoonIcon,
  GlobeIcon,
  BookOpenIcon,
  FileTextIcon,
  KeyboardIcon,
  LifeBuoyIcon,
  CheckIcon,
  TriangleAlertIcon,
  CircleCheckIcon,
  InfoIcon,
  PaletteIcon } from
'lucide-react';
import { useDismissable } from '../../hooks/useDismissable';
import { notifyNavBadgesChanged } from '../../hooks/useNavBadges';
import { useTheme } from '../../contexts/ThemeContext';
import { useSession } from '../../contexts/SessionContext';
import { itemsForRole } from '../../data/navItems';
import { useLocale, languages } from '../../contexts/LocaleContext';
import { api, ApiException } from '../../api/client';
import { EmptyState } from '../ui/EmptyState';
import { useToast } from '../../contexts/ToastContext';

type OpenMenu = 'none' | 'notifications' | 'help' | 'language' | 'color';

const toneIcon = {
  alert: TriangleAlertIcon,
  success: CircleCheckIcon,
  info: InfoIcon
};

interface AlertItem {
  id: string;
  title: string;
  detail: string;
  time: string;
  unread: boolean;
  tone: 'alert' | 'success' | 'info';
}

function mapAlert(raw: any): AlertItem {
  const id = String(raw.id ?? raw.alert_id ?? Math.random().toString(36).slice(2));
  const title = raw.title ?? raw.message ?? raw.summary ?? 'Notification';
  const detail = raw.detail ?? raw.description ?? raw.body ?? '';
  const timeRaw = raw.time ?? raw.created_at ?? raw.timestamp ?? '';
  let time = '';
  if (timeRaw) {
    try {
      const d = new Date(timeRaw);
      if (!isNaN(d.getTime())) time = d.toLocaleString();
      else time = String(timeRaw);
    } catch { time = String(timeRaw); }
  }
  // `status === 'unread'` is always a boolean, so the old `?? true` tail was
  // unreachable. With no unread signal at all the alert reads as already seen.
  const unread = raw.unread ?? raw.is_unread ?? raw.status === 'unread';
  let tone: AlertItem['tone'] = 'info';
  const s = String(raw.severity ?? raw.tone ?? raw.level ?? '').toLowerCase();
  if (s.includes('alert') || s.includes('critical') || s.includes('warn')) tone = 'alert';
  else if (s.includes('success') || s.includes('resolved')) tone = 'success';
  return { id, title, detail, time, unread, tone };
}

export function HeaderActions() {
  const [openMenu, setOpenMenu] = useState<OpenMenu>('none');
  const [items, setItems] = useState<AlertItem[]>([]);
  const [notifLoading, setNotifLoading] = useState(false);
  const [notifError, setNotifError] = useState<{ variant: 'error' | 'offline'; message: string } | null>(null);
  const close = useCallback(() => setOpenMenu('none'), []);
  const ref = useDismissable<HTMLDivElement>(openMenu !== 'none', close);
  const { theme, toggleTheme, colorScheme, setColorScheme, customColor, setCustomColor } = useTheme();
  const { locale, setLocale, t } = useLocale();
  const { toast } = useToast();
  const { user, isAuthenticated } = useSession();
  // The bell reads the alerts endpoint, which answers 403 for a seat without
  // ``alerts:read`` (a Viewer) and 401 when no session exists. Gate it on
  // both auth and nav visibility so an unauthenticated landing screen or an
  // expired session never polls — otherwise every 60s interval spams
  // `GET /alerts 401` to the console (see client.ts:117).
  const canSeeAlerts =
    isAuthenticated &&
    Boolean(user.id) &&
    itemsForRole(user.role, user.isSiteAdmin === true).some((item) => item.id === 'alerts');

  const fetchNotifications = useCallback(async () => {
    if (!canSeeAlerts) {
      setItems([]);
      setNotifError(null);
      setNotifLoading(false);
      return;
    }
    setNotifLoading(true);
    setNotifError(null);
    try {
      // Primary: GET /api/v1/alerts?status=unread&limit=5, fallback limit only
      let data: any = null;
      try {
        data = await api.get<any>('/api/v1/alerts', { status: 'unread', limit: 5 });
      } catch (e) {
        if (e instanceof ApiException && (e.status === 400 || e.status === 422)) {
          data = await api.get<any>('/api/v1/alerts', { limit: 5 });
        } else throw e;
      }
      let rawList: any[] = [];
      if (Array.isArray(data)) rawList = data;
      else if (Array.isArray(data?.data)) rawList = data.data;
      else if (Array.isArray(data?.alerts)) rawList = data.alerts;
      else if (Array.isArray(data?.items)) rawList = data.items;
      const mapped = rawList.map(mapAlert);
      setItems(mapped);
    } catch (e) {
      if (e instanceof ApiException) {
        // 401 = not authenticated — don't spam the console or show an error
        // panel for the bell; the session layer will handle expiry toasts.
        if (e.status === 401) {
          setNotifError(null);
          return;
        }
        if (!navigator.onLine || e.status === 0) setNotifError({ variant: 'offline', message: e.error.message });
        else setNotifError({ variant: 'error', message: e.error.message });
      } else if (e instanceof Error && !navigator.onLine) {
        setNotifError({ variant: 'offline', message: 'Offline' });
      } else {
        setNotifError({ variant: 'error', message: e instanceof Error ? e.message : 'Failed to load' });
      }
    } finally {
      setNotifLoading(false);
    }
  }, [canSeeAlerts]);

  useEffect(() => {
    if (!canSeeAlerts) return;
    void fetchNotifications();
    const id = window.setInterval(() => { void fetchNotifications(); }, 60000);
    return () => window.clearInterval(id);
  }, [fetchNotifications, canSeeAlerts]);

  const markRead = useCallback(async (alertId: string) => {
    // optimistic
    setItems((cur) => cur.map((x) => x.id === alertId ? { ...x, unread: false } : x));
    notifyNavBadgesChanged();
    try {
      await api.post(`/api/v1/alerts/${alertId}/read`);
    } catch {
      // keep optimistic; could revert but not critical
    }
  }, []);

  const markAllRead = useCallback(async () => {
    const unreadIds = items.filter((i) => i.unread).map((i) => i.id);
    setItems((cur) => cur.map((x) => ({ ...x, unread: false })));
    for (const id of unreadIds) {
      try { await api.post(`/api/v1/alerts/${id}/read`); } catch {}
    }
    notifyNavBadgesChanged();
  }, [items]);

  const handleLocale = useCallback(async (code: typeof languages[number]['code']) => {
    setLocale(code);
    close();
    try {
      await api.patch('/api/v1/me/preferences', { language: code });
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    }
  }, [setLocale, close, toast]);

  const colorSchemes = useMemo(() => [
    { id: 'ash' as const, label: t('color.ash'), color: '#64748b' },
    { id: 'green' as const, label: t('color.green'), color: '#17a294' },
    { id: 'blue' as const, label: t('color.blue'), color: '#3b82f6' },
    { id: 'yellow' as const, label: t('color.yellow'), color: '#f59e0b' },
    { id: 'purple' as const, label: t('color.purple'), color: '#a855f7' },
    { id: 'orange' as const, label: t('color.orange'), color: '#f97316' },
    { id: 'red' as const, label: t('color.red'), color: '#ef4444' },
    { id: 'ocean' as const, label: t('color.ocean'), color: '#007d8b' },
      { id: 'pink' as const, label: t('color.pink'), color: '#ec4899' },
    { id: 'custom' as const, label: t('color.custom'), color: customColor }
  ], [t, customColor]);

  const handleColorScheme = useCallback(async (scheme: any) => {
    setColorScheme(scheme);
    close();
    try {
      await api.patch('/api/v1/me/preferences', { colorScheme: scheme });
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    }
  }, [setColorScheme, close, toast]);

  const handleCustomColorChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const newColor = e.target.value;
    setCustomColor(newColor);
    setColorScheme('custom');
    close();
    try {
      api.patch('/api/v1/me/preferences', { colorScheme: 'custom', customColor: newColor });
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    }
  }, [setCustomColor, setColorScheme, close, toast]);

  const unreadCount = items.filter((item) => item.unread).length;
  const currentLanguage = languages.find((language) => language.code === locale);

  const toggle = (menu: OpenMenu) =>
  setOpenMenu((current) => current === menu ? 'none' : menu);

  const helpLinks = [
  { id: 'docs', label: t('header.docs'), icon: BookOpenIcon },
  { id: 'guide', label: t('header.guide'), icon: FileTextIcon },
  { id: 'shortcuts', label: t('header.shortcuts'), icon: KeyboardIcon },
  { id: 'support', label: t('header.support'), icon: LifeBuoyIcon }];


  const triggerClass =
  'tap-expand relative rounded-lg p-1.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-200/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:p-2';

  return (
    <div ref={ref} className="relative flex shrink-0 items-center gap-0.5 sm:gap-1">
      {/* Notifications */}
      <div className="relative">
        <button
          type="button"
          aria-label={t('header.notifications')}
          aria-haspopup="menu"
          aria-expanded={openMenu === 'notifications'}
          onClick={() => toggle('notifications')}
          className={triggerClass}>
          
          <BellIcon className="h-[18px] w-[18px] sm:h-5 sm:w-5" strokeWidth={1.75} />
          {unreadCount > 0 &&
          <span className="absolute right-0.5 top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-brand-600 px-1 text-nano font-semibold text-white sm:right-1 sm:top-1">
              {unreadCount}
            </span>
          }
        </button>

        {openMenu === 'notifications' &&
        <div
          role="menu"
          className="fixed left-3 right-3 top-[56px] z-50 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_12px_30px_rgba(15,23,42,0.12)] sm:absolute sm:left-auto sm:right-0 sm:top-[calc(100%+8px)] sm:w-[340px]">
          
            <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5">
              <p className="text-body-xs font-semibold text-slate-900">
                {t('header.notifications')}
              </p>
              {canSeeAlerts &&
              <button
              type="button"
              onClick={() => void markAllRead()}
              className="flex items-center gap-1 rounded text-micro font-medium text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800">
              
                <CheckIcon className="h-3.5 w-3.5" strokeWidth={2} />
                {t('header.markAll')}
              </button>}
            </div>

            {!canSeeAlerts ? (
              <div className="p-2">
                <EmptyState
                  variant="empty"
                  size="sm"
                  title="Alerts are not part of your seat"
                  description="An administrator can grant a role that includes alerts."
                />
              </div>
            ) : notifLoading && items.length === 0 ? (
              <div className="px-3 py-6 text-center text-caption text-slate-500">Loading…</div>
            ) : notifError && items.length === 0 ? (
              <div className="p-2">
                <EmptyState variant={notifError.variant} size="sm" title={notifError.variant === 'offline' ? 'Offline' : 'Failed to load'} description={notifError.message} />
              </div>
            ) : items.length === 0 ? (
              <div className="p-2">
                <EmptyState variant="empty" size="sm" title="All caught up" description="No unread alerts." />
              </div>
            ) : (
            <ul className="max-h-[300px] overflow-y-auto">
              {items.map((item) => {
              const Icon = toneIcon[item.tone];
              return (
                <li key={item.id}>
                    <button
                    type="button"
                    onClick={() => void markRead(item.id)}
                    className="flex w-full items-start gap-2.5 border-b border-slate-100 px-3 py-2.5 text-left transition-colors duration-150 ease-out hover:bg-slate-50">
                    
                      <Icon
                      className={[
                      'mt-0.5 h-4 w-4 shrink-0',
                      item.tone === 'alert' ?
                      'text-amber-500' :
                      item.tone === 'success' ?
                      'text-brand-600' :
                      'text-slate-500'].
                      join(' ')}
                      strokeWidth={1.9} />
                    
                      <span className="min-w-0 flex-1">
                        <span className="block text-meta font-semibold text-slate-900">
                          {item.title}
                        </span>
                        {item.detail ? (
                        <span className="mt-0.5 block text-micro leading-relaxed text-slate-600">
                          {item.detail}
                        </span>
                        ) : null}
                        {item.time ? (
                        <span className="mt-1 block text-fine text-slate-500">
                          {item.time}
                        </span>
                        ) : null}
                      </span>
                      {item.unread &&
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-500" />
                    }
                    </button>
                  </li>);

            })}
            </ul>
            )}

            <button
            type="button"
            onClick={close}
            className="w-full px-3 py-2.5 text-center text-caption font-semibold text-brand-700 transition-colors duration-150 ease-out hover:bg-slate-50">
            
              {t('header.viewAll')}
            </button>
          </div>
        }
      </div>

      {/* Help */}
      <div className="relative hidden sm:block">
        <button
          type="button"
          aria-label={t('header.help')}
          aria-haspopup="menu"
          aria-expanded={openMenu === 'help'}
          onClick={() => toggle('help')}
          className={triggerClass}>
          
          <HelpCircleIcon className="h-5 w-5" strokeWidth={1.75} />
        </button>

        {openMenu === 'help' &&
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-[248px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
          
            <p className="px-3 py-2 text-fine font-semibold uppercase tracking-wide text-slate-500">
              {t('header.help')}
            </p>
            {helpLinks.map((link) => {
            const Icon = link.icon;
            return (
              <button
                key={link.id}
                type="button"
                role="menuitem"
                onClick={close}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-meta text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50">
                
                  <Icon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} />
                  {link.label}
                </button>);

          })}
          </div>
        }
      </div>

      {/* Language */}
      <div className="relative">
        <button
          type="button"
          aria-label={t('header.language')}
          aria-haspopup="menu"
          aria-expanded={openMenu === 'language'}
          onClick={() => toggle('language')}
          className={`${triggerClass} flex items-center gap-1`}>
          
          <GlobeIcon className="h-[18px] w-[18px] sm:h-5 sm:w-5" strokeWidth={1.75} />
          <span className="hidden text-micro font-semibold sm:inline">
            {currentLanguage?.short}
          </span>
        </button>

        {openMenu === 'language' &&
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-[188px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
          
            <p className="px-3 py-2 text-fine font-semibold uppercase tracking-wide text-slate-500">
              {t('header.language')}
            </p>
            {languages.map((language) => {
            const isActive = language.code === locale;
            return (
              <button
                key={language.code}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                onClick={() => void handleLocale(language.code)}
                className={[
                'flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-meta transition-colors duration-150 ease-out hover:bg-slate-50',
                isActive ? 'font-semibold text-brand-700' : 'text-slate-700'].
                join(' ')}>
                
                  <span className="flex items-center gap-2">
                    <span className="w-6 shrink-0 text-fine font-semibold text-slate-500">
                      {language.short}
                    </span>
                    {language.label}
                  </span>
                  {isActive && <CheckIcon className="h-4 w-4" strokeWidth={2.25} />}
                </button>);

          })}
          </div>
        }
      </div>

      {/* Color Scheme */}
      <div className="relative hidden sm:block">
        <button
          type="button"
          aria-label={t('header.colorScheme')}
          aria-haspopup="menu"
          aria-expanded={openMenu === 'color'}
          onClick={() => toggle('color')}
          className={triggerClass}>
          
          <PaletteIcon className="h-5 w-5" strokeWidth={1.75} />
        </button>

        {openMenu === 'color' &&
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-[240px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
          
            <p className="px-3 py-2 text-fine font-semibold uppercase tracking-wide text-slate-500">
              {t('header.colorScheme')}
            </p>
            <div className="grid grid-cols-3 gap-1 px-3 py-2">
              {colorSchemes.filter(s => s.id !== 'custom').map((scheme) => {
              const isActive = scheme.id === colorScheme;
              return (
                <button
                  key={scheme.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={isActive}
                  onClick={() => void handleColorScheme(scheme.id)}
                  className={[
                  'flex flex-col items-center gap-1 rounded-lg p-2 text-center transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                  isActive ? 'bg-brand-50 ring-2 ring-brand-500' : ''].
                  join(' ')}>
                  
                    <span 
                      className="h-6 w-6 rounded-full border border-slate-300"
                      style={{ backgroundColor: scheme.color }}
                    />
                    <span className="text-fine font-medium text-slate-600">{scheme.label}</span>
                  </button>);
            
            })}
            </div>
            
            <div className="border-t border-slate-100 px-3 py-2">
              <label className="flex items-center gap-2 text-caption font-medium text-slate-700">
                <input
                  type="color"
                  value={customColor}
                  onChange={handleCustomColorChange}
                  className="h-6 w-6 rounded cursor-pointer border-0 p-0"
                />
                {t('color.pickCustom')}
              </label>
            </div>
          </div>
        }
      </div>

      {/* Theme: single icon on small screens, segmented switch from sm up */}
      <button
        type="button"
        role="switch"
        aria-checked={theme === 'dark'}
        aria-label={`${t('header.theme')}: ${theme === 'dark' ? t('header.dark') : t('header.light')}`}
        onClick={toggleTheme}
        className={`${triggerClass} sm:hidden`}>
        
        {theme === 'dark' ?
        <MoonIcon className="h-[18px] w-[18px]" strokeWidth={1.75} /> :

        <SunIcon className="h-[18px] w-[18px]" strokeWidth={1.75} />
        }
      </button>

      <button
        type="button"
        role="switch"
        aria-checked={theme === 'dark'}
        aria-label={`${t('header.theme')}: ${theme === 'dark' ? t('header.dark') : t('header.light')}`}
        onClick={toggleTheme}
        className="ml-0.5 hidden items-center gap-1 rounded-full border border-slate-200 bg-white p-1 transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:flex">
        
        <span
          className={[
          'flex h-6 w-6 items-center justify-center rounded-full transition-colors duration-150 ease-out',
          theme === 'light' ? 'bg-brand-600 text-white' : 'text-slate-500'].
          join(' ')}>
          
          <SunIcon className="h-3.5 w-3.5" strokeWidth={2} />
        </span>
        <span
          className={[
          'flex h-6 w-6 items-center justify-center rounded-full transition-colors duration-150 ease-out',
          theme === 'dark' ? 'bg-brand-600 text-white' : 'text-slate-500'].
          join(' ')}>
          
          <MoonIcon className="h-3.5 w-3.5" strokeWidth={2} />
        </span>
      </button>
    </div>);

}
