import React from 'react';

interface FieldProps {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}

export function Field({ id, label, hint, error, children, className }: FieldProps) {
  return (
    <div className={['min-w-0', className].filter(Boolean).join(' ')}>
      <label
        htmlFor={id}
        className="block text-body-xs font-semibold text-slate-800 dark:text-slate-200">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {error ? (
        <p className="mt-1.5 text-caption text-red-600 dark:text-red-400" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1.5 text-caption text-slate-500 dark:text-slate-400">{hint}</p>
      ) : null}
    </div>
  );
}

/** Shared control chrome — keep in sync across Settings, modals, and admin forms. */
export const inputClass =
  'w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-control font-normal leading-snug tracking-normal text-slate-900 outline-none transition-[border-color,box-shadow,background-color] duration-150 ease-out placeholder:text-slate-500 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 read-only:cursor-default read-only:border-slate-200 read-only:bg-slate-50 read-only:text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500';

export const inputErrorClass = 'border-red-400 focus:border-red-500 focus:ring-red-500/20';
