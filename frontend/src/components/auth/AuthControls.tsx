import { useCallback, useState } from 'react';
import { SunIcon, MoonIcon, GlobeIcon, CheckIcon } from 'lucide-react';
import { useDismissable } from '../../hooks/useDismissable';
import { useTheme } from '../../contexts/ThemeContext';
import { useLocale, languages } from '../../contexts/LocaleContext';

export function AuthControls() {
  const [isOpen, setIsOpen] = useState(false);
  const close = useCallback(() => setIsOpen(false), []);
  const ref = useDismissable<HTMLDivElement>(isOpen, close);
  const { theme, toggleTheme } = useTheme();
  const { locale, setLocale, t } = useLocale();

  const current = languages.find((language) => language.code === locale);

  return (
    <div ref={ref} className="flex items-center gap-1.5">
      <div className="relative">
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={isOpen}
          aria-label={t('header.language')}
          onClick={() => setIsOpen((open) => !open)}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          <GlobeIcon className="h-4 w-4" strokeWidth={1.75} />
          {current?.short}
        </button>

        {isOpen &&
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-50 w-[180px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-[0_12px_30px_rgba(15,23,42,0.12)]">
          
            {languages.map((language) => {
            const isActive = language.code === locale;
            return (
              <button
                key={language.code}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                onClick={() => {
                  setLocale(language.code);
                  close();
                }}
                className={[
                'flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-meta transition-colors duration-150 ease-out hover:bg-slate-50',
                isActive ? 'font-semibold text-brand-700' : 'text-slate-700'].
                join(' ')}>
                
                  {language.label}
                  {isActive && <CheckIcon className="h-4 w-4" strokeWidth={2.25} />}
                </button>);

          })}
          </div>
        }
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={theme === 'dark'}
        aria-label={t('header.theme')}
        onClick={toggleTheme}
        className="flex items-center gap-1 rounded-full border border-slate-200 p-1 transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        <span
          className={[
          'flex h-5 w-5 items-center justify-center rounded-full transition-colors duration-150 ease-out',
          theme === 'light' ? 'bg-brand-600 text-white' : 'text-slate-500'].
          join(' ')}>
          
          <SunIcon className="h-3 w-3" strokeWidth={2} />
        </span>
        <span
          className={[
          'flex h-5 w-5 items-center justify-center rounded-full transition-colors duration-150 ease-out',
          theme === 'dark' ? 'bg-brand-600 text-white' : 'text-slate-500'].
          join(' ')}>
          
          <MoonIcon className="h-3 w-3" strokeWidth={2} />
        </span>
      </button>
    </div>);

}