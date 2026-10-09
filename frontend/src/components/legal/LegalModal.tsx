import { useEffect, useRef } from 'react';
import { XIcon, ScaleIcon, ShieldCheckIcon, CookieIcon } from 'lucide-react';
import { legalDocuments } from '../../data/legal';
import type { LegalDocument } from '../../data/legal';
import { useDialogBehaviour } from '../ui/useDialogBehaviour';

type DocId = LegalDocument['id'];

interface LegalModalProps {
  openId?: DocId;
  onClose: () => void;
  onSelect: (id: DocId) => void;
}

const icons: Record<DocId, typeof ScaleIcon> = {
  terms: ScaleIcon,
  privacy: ShieldCheckIcon,
  cookies: CookieIcon
};

/**
 * Full legal reader. The three documents live side by side so someone who
 * opens "Terms" can reach "Privacy" without going back to the footer.
 */
export function LegalModal({ openId, onClose, onSelect }: LegalModalProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  // The same contract as `Modal` — Escape, the Tab trap, focus return and the
  // scroll lock. This reader keeps its own layout because a long legal document
  // needs a fixed height and its own section navigation.
  const panelRef = useDialogBehaviour(Boolean(openId), onClose);

  // Reading a different document should start at the top of that document.
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [openId]);

  if (!openId) return null;

  const doc = legalDocuments.find((item) => item.id === openId)!;

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center sm:items-center sm:p-4">
      <div
        className="absolute inset-0 bg-slate-900/55"
        onClick={onClose}
        aria-hidden="true" />
      
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={doc.title}
        tabIndex={-1}
        className="relative flex h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl outline-none sm:h-[86vh] sm:max-w-3xl sm:rounded-2xl dark:bg-slate-900">
        
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3 sm:px-5 dark:border-slate-700">
          <div className="min-w-0">
            <h2 className="truncate text-h5 font-bold tracking-tight text-slate-900 sm:text-h4 dark:text-slate-50">
              {doc.title}
            </h2>
            <p className="mt-0.5 text-micro text-slate-500 dark:text-slate-400">
              Last updated {doc.updated}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="tap-expand -mr-1 shrink-0 rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:hover:bg-slate-800">
            
            <XIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </header>

        <nav
          aria-label="Legal documents"
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 px-3 py-2 sm:px-4 dark:border-slate-700">
          
          {legalDocuments.map((item) => {
            const Icon = icons[item.id];
            const isActive = item.id === openId;
            return (
              <button
                key={item.id}
                type="button"
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelect(item.id)}
                className={[
                'inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-meta transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                isActive ?
                'bg-brand-600 font-semibold text-white' :
                'font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'].
                join(' ')}>
                
                <Icon className="h-3.5 w-3.5" strokeWidth={1.9} />
                {item.label}
              </button>);

          })}
        </nav>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
          <p className="rounded-xl bg-brand-50 px-3.5 py-3 text-meta leading-relaxed text-brand-900 dark:bg-slate-800 dark:text-slate-200">
            {doc.summary}
          </p>

          <div className="mt-5 flex flex-col gap-5">
            {doc.sections.map((section) =>
            <section key={section.heading}>
                <h3 className="text-body-sm font-semibold text-slate-900 sm:text-body-sm-lg dark:text-slate-100">
                  {section.heading}
                </h3>
                <div className="mt-1.5 flex flex-col gap-2">
                  {section.body.map((paragraph) =>
                <p
                  key={paragraph}
                  className="text-meta leading-relaxed text-slate-600 sm:text-body-xs dark:text-slate-400">
                  
                      {paragraph}
                    </p>
                )}
                </div>
              </section>
            )}
          </div>

          <p className="mt-6 border-t border-slate-200 pt-4 text-micro leading-relaxed text-slate-500 dark:border-slate-700 dark:text-slate-400">
            Questions about this document? Write to legal@demandiq.app. DemandIQ is a
            research-grounded decision-support tool; forecasts and surplus figures are
            estimates, not guarantees.
          </p>
        </div>

        <footer className="flex shrink-0 justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-700">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-brand-700 px-4 py-2 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
            
            Close
          </button>
        </footer>
      </div>
    </div>);

}