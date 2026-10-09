import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState } from
'react';
import { CheckCircle2Icon, InfoIcon, AlertTriangleIcon, XIcon } from 'lucide-react';

type ToastTone = 'success' | 'info' | 'error';

interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastContextValue {
  toast: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const toneStyles: Record<ToastTone, {icon: typeof InfoIcon;className: string;}> = {
  success: { icon: CheckCircle2Icon, className: 'text-brand-600' },
  info: { icon: InfoIcon, className: 'text-slate-500' },
  error: { icon: AlertTriangleIcon, className: 'text-red-500' }
};

export function ToastProvider({ children }: {children: React.ReactNode;}) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, tone: ToastTone = 'success') => {
      const id = nextId.current++;
      setToasts((current) => [...current.slice(-2), { id, message, tone }]);
      window.setTimeout(() => dismiss(id), 3600);
    },
    [dismiss]
  );

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 top-4 z-[60] flex flex-col items-center gap-2 px-4 md:items-end md:pr-6"
        role="status"
        aria-live="polite">
        
        {toasts.map((item) => {
          const { icon: Icon, className } = toneStyles[item.tone];
          return (
            <div
              key={item.id}
              role="alert"
              aria-live="assertive"
              aria-atomic="true"
              className="pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-lg dark:border-slate-700 dark:bg-slate-900">
              
              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${className}`} strokeWidth={2} />
              <p className="min-w-0 flex-1 text-meta leading-relaxed text-slate-700 dark:text-slate-200">
                {item.message}
              </p>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                aria-label="Dismiss notification"
                className="-mr-1 rounded p-0.5 text-slate-500 transition-colors duration-150 ease-out hover:text-slate-600">
                
                <XIcon className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
            </div>);

        })}
      </div>
    </ToastContext.Provider>);

}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used within ToastProvider');
  return context;
}