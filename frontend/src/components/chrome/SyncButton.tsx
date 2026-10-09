import { useEffect, useRef, useState } from 'react';
import { RefreshCwIcon, CheckIcon } from 'lucide-react';

type SyncState = 'idle' | 'syncing' | 'done';

interface SyncButtonProps {
  compact?: boolean;
}

export function SyncButton({ compact = false }: SyncButtonProps) {
  const [state, setState] = useState<SyncState>('idle');
  const [minutesAgo, setMinutesAgo] = useState(12);
  const timers = useRef<number[]>([]);

  useEffect(
    () => () => {
      timers.current.forEach((id) => window.clearTimeout(id));
    },
    []
  );

  const runSync = () => {
    if (state === 'syncing') return;
    setState('syncing');
    timers.current.push(
      window.setTimeout(() => {
        setState('done');
        setMinutesAgo(0);
      }, 1600)
    );
    timers.current.push(window.setTimeout(() => setState('idle'), 3200));
  };

  const label =
  state === 'syncing' ?
  'Syncing…' :
  state === 'done' ?
  'Synced' :
  minutesAgo === 0 ?
  'Synced just now' :
  `Synced ${minutesAgo}m ago`;

  if (compact) {
    return (
      <button
        type="button"
        onClick={runSync}
        aria-label={label}
        title={label}
        className="tap-expand relative rounded-lg p-1.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-200/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        
        {state === 'done' ?
        <CheckIcon className="h-[18px] w-[18px] text-brand-600" strokeWidth={2.25} /> :

        <RefreshCwIcon
          className={[
          'h-[18px] w-[18px]',
          state === 'syncing' ? 'animate-spin text-brand-600' : ''].
          join(' ')}
          strokeWidth={1.9} />

        }
      </button>);

  }

  return (
    <button
      type="button"
      onClick={runSync}
      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:border-slate-300 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
      
      {state === 'done' ?
      <CheckIcon className="h-3.5 w-3.5 text-brand-600" strokeWidth={2.25} /> :

      <RefreshCwIcon
        className={[
        'h-3.5 w-3.5',
        state === 'syncing' ? 'animate-spin text-brand-600' : 'text-slate-500'].
        join(' ')}
        strokeWidth={2} />

      }
      <span className="hidden lg:inline">{label}</span>
      <span className="lg:hidden">Sync</span>
    </button>);

}