import React from 'react';
import { LockIcon, ExternalLinkIcon } from 'lucide-react';

interface PageFooterProps {
  children?: React.ReactNode;
}

export function PageFooter({ children }: PageFooterProps) {
  return (
    <footer className="hidden shrink-0 items-center justify-between gap-4 border-t border-slate-200 bg-slate-50 px-5 py-3 text-meta text-slate-500 md:flex lg:px-6">
      {children ??
      <>
          <p className="flex items-center gap-2">
            <LockIcon className="h-4 w-4 text-slate-500" strokeWidth={1.75} />
            <span className="font-medium text-slate-700">Secure upload</span>
            <span aria-hidden="true">•</span>
            <span>Your data is encrypted and protected.</span>
          </p>
          <a
          href="#guide"
          className="flex items-center gap-2 rounded transition-colors duration-150 ease-out hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
            Need help? Visit our guide
            <ExternalLinkIcon className="h-4 w-4" strokeWidth={1.75} />
          </a>
        </>
      }
    </footer>);

}