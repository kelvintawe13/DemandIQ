import { useEffect, useRef, useState } from 'react';
import { CheckIcon, Loader2Icon, PencilIcon, XIcon } from 'lucide-react';

interface InlineEditProps {
  /** Current value; `null`/`''` renders the placeholder in muted text. */
  value: string | null | undefined;
  /**
   * What the field is, for the accessible name — e.g. `Manager for Remera Grill`.
   * A bare pencil announces as "button" and says nothing about what it edits.
   */
  label: string;
  placeholder?: string;
  type?: 'text' | 'tel';
  /** Resolves when the write landed; rejecting puts the old value back. */
  onSave: (next: string) => Promise<void>;
  /** Applied to the value in view mode, for row-level type sizing. */
  textClassName?: string;
}

/**
 * Fix one field without opening a dialog.
 *
 * A correction like a manager's name or a phone number is a two-second edit, and
 * sending the operator into an eleven-field modal for it loses their place in the
 * list. This turns the value itself into the control.
 *
 * Contract, because inline editing is easy to get subtly wrong:
 *
 * * **Enter or the tick saves, Escape or the cross cancels, clicking away
 *   cancels.** Saving on blur and *also* offering a tick is a double-write
 *   waiting to happen — the tick's own blur fires first — so blur discards
 *   instead. `relatedTarget` is what stops the tick from cancelling the edit
 *   before its click is handled.
 * * **Unchanged means no request.** Tabbing through a row must not PATCH it.
 * * **A failed write reverts and says so.** The parent toasts; this control only
 *   guarantees the value it shows is the value the server holds.
 */
export function InlineEdit({
  value,
  label,
  placeholder = 'Not set',
  type = 'text',
  onSave,
  textClassName = '',
}: InlineEditProps) {
  const current = value ?? '';
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current);
  const [saving, setSaving] = useState(false);
  const boxRef = useRef<HTMLSpanElement>(null);

  // A parent that refetches (or rolls a row back) must be able to rewrite the
  // value under an open editor without the editor winning the argument.
  useEffect(() => {
    if (!editing) setDraft(current);
  }, [current, editing]);

  const start = () => {
    setDraft(current);
    setEditing(true);
  };

  const cancel = () => {
    setDraft(current);
    setEditing(false);
  };

  const commit = async () => {
    const next = draft.trim();
    if (next === current.trim()) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(next);
      setEditing(false);
    } catch {
      // The parent owns the message; the control owns the value.
      setDraft(current);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button
        type="button"
        onClick={start}
        aria-label={`Edit ${label}${current ? ` — currently ${current}` : ''}`}
        title={`Edit ${label}`}
        className="group inline-flex max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        <span className={current ? textClassName : `text-fine italic text-slate-500 ${textClassName}`}>
          {current || placeholder}
        </span>
        {/*
          Faintly visible, not hidden until hover. A pencil that only appears on
          hover does not exist on a phone — there is no hover there — so the
          field reads as fixed text and a typed-in correction looks impossible.
        */}
        <PencilIcon
          aria-hidden="true"
          className="h-3 w-3 shrink-0 text-slate-500 opacity-60 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          strokeWidth={2}
        />
      </button>
    );
  }

  return (
    <span
      ref={boxRef}
      className="inline-flex max-w-full items-center gap-1"
      onBlur={(event) => {
        // Moving focus to the tick or the cross is not leaving the editor.
        const next = event.relatedTarget as Node | null;
        if (next && boxRef.current?.contains(next)) return;
        cancel();
      }}>
      <input
        type={type}
        value={draft}
        autoFocus
        disabled={saving}
        aria-label={label}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            void commit();
          }
          if (event.key === 'Escape') {
            event.preventDefault();
            cancel();
          }
        }}
        className="w-full min-w-0 max-w-[190px] rounded-lg border border-slate-300 bg-white px-2 py-1 text-caption text-slate-900 outline-none transition-[border-color,box-shadow] duration-150 ease-out focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:opacity-60"
      />
      <button
        type="button"
        onClick={() => void commit()}
        disabled={saving}
        aria-label={`Save ${label}`}
        title="Save"
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white transition-colors hover:bg-brand-800 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        {saving ? (
          <Loader2Icon aria-hidden="true" className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
        ) : (
          <CheckIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.4} />
        )}
      </button>
      <button
        type="button"
        onClick={cancel}
        disabled={saving}
        aria-label={`Cancel editing ${label}`}
        title="Cancel"
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-300 text-slate-600 transition-colors hover:bg-slate-50 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        <XIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.2} />
      </button>
    </span>
  );
}
