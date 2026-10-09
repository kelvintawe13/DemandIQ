import React from 'react';
import { XIcon } from 'lucide-react';
import { useDialogBehaviour } from './useDialogBehaviour';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  footer?: React.ReactNode;
  children: React.ReactNode;
}

const sizes = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-xl',
  xl: 'max-w-3xl',
  '2xl': 'max-w-5xl'
};

export function Modal({
  open,
  onClose,
  title,
  description,
  size = 'md',
  footer,
  children
}: ModalProps) {
  const panelRef = useDialogBehaviour(open, onClose);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-3 sm:p-4">
      <div
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-[2px] animate-[fadeIn_150ms_ease-out]"
        onClick={onClose}
        aria-hidden="true" />
      
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={[
        'relative flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl outline-none dark:bg-slate-900',
        sizes[size]].
        join(' ')}>
        
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <div className="min-w-0">
            <h2 className="text-control font-semibold text-slate-900 dark:text-slate-100">
              {title}
            </h2>
            {description &&
            <p className="mt-0.5 text-micro leading-relaxed text-slate-500 dark:text-slate-400">
                {description}
              </p>
            }
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="tap-expand -mr-1 rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:hover:bg-slate-800">
            
            <XIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">{children}</div>

        {footer &&
        <footer className="flex flex-wrap justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-700">
            {footer}
          </footer>
        }
      </div>
    </div>);

}

interface ModalButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  tone?: 'primary' | 'ghost' | 'danger';
  disabled?: boolean;
  /** Submits the form with this id from outside it (see `FormModal`). */
  form?: string;
}

export function ModalButton({
  children,
  onClick,
  type = 'button',
  tone = 'primary',
  disabled,
  form
}: ModalButtonProps) {
  const tones = {
    primary:
    'bg-brand-700 text-white hover:bg-brand-800 disabled:bg-slate-300 disabled:text-slate-500',
    ghost:
    'border border-slate-300 text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800',
    danger: 'bg-red-600 text-white hover:bg-red-700'
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      form={form}
      className={[
      'inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-body-xs font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed',
      tones[tone]].
      join(' ')}>
      
      {children}
    </button>);

}