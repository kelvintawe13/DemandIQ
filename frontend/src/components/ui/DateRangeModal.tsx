import { useState } from 'react';
import { Modal, ModalButton } from './Modal';
import { Field, inputClass } from './Field';
import { todayISO, addDaysISO } from '../../lib/workspaceTime';

const datePresets = [
'Last 7 days',
'Last 14 days',
'Last 30 days',
'Last 90 days',
'This month',
'Custom range'];


interface DateRangeModalProps {
  open: boolean;
  onClose: () => void;
  value: string;
  onApply: (label: string) => void;
  /**
   * The explicit dates behind the selection, or `null` for a preset.
   *
   * Screens that can query by real bounds (`GET /analytics/*` takes `from` and
   * `to`) should use this rather than parsing them back out of the label — the
   * label is for display, and a screen that only understands presets used to
   * fall back to a 30-day default on a custom range without saying so.
   */
  onApplyRange?: (range: { from: string; to: string } | null) => void;
}

/** An existing `YYYY-MM-DD → YYYY-MM-DD` label, split back into its bounds. */
function parseCustom(value: string): { from: string; to: string } | null {
  const match = /^(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})$/.exec(value.trim());
  return match ? { from: match[1], to: match[2] } : null;
}

export function DateRangeModal({ open, onClose, value, onApply, onApplyRange }: DateRangeModalProps) {
  const [preset, setPreset] = useState(value);
  // Default to the last 30 days rather than a frozen pair of dates that went
  // stale the moment the calendar moved past them.
  const [from, setFrom] = useState(() => {
    const existing = parseCustom(value);
    if (existing) return existing.from;
    // 29 days back from the workspace's today, so the default window covers the
    // same calendar days the rest of the app calls "the last 30".
    return addDaysISO(todayISO(), -29);
  });
  const [to, setTo] = useState(() => parseCustom(value)?.to ?? todayISO());

  const apply = () => {
    const sortable = from > to ? { from: to, to: from } : { from, to };
    onApply(preset === 'Custom range' ? `${sortable.from} → ${sortable.to}` : preset);
    onApplyRange?.(preset === 'Custom range' ? sortable : null);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Date range"
      description="Applies to every panel on this screen."
      footer={
      <>
          <ModalButton tone="ghost" onClick={onClose}>
            Cancel
          </ModalButton>
          <ModalButton onClick={apply}>Apply range</ModalButton>
        </>
      }>
      
      <div className="grid grid-cols-2 gap-1.5">
        {datePresets.map((option) =>
        <button
          key={option}
          type="button"
          aria-pressed={preset === option}
          onClick={() => setPreset(option)}
          className={[
          'rounded-lg border px-3 py-2 text-left text-meta font-medium transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          preset === option ?
          'border-brand-500 bg-brand-50 text-brand-800' :
          'border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300'].
          join(' ')}>
          
            {option}
          </button>
        )}
      </div>

      {preset === 'Custom range' &&
      <div className="mt-3 grid grid-cols-2 gap-2">
          <Field id="range-from" label="From">
            <input
            id="range-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            className={inputClass} />
          
          </Field>
          <Field id="range-to" label="To">
            <input
            id="range-to"
            type="date"
            value={to}
            onChange={(event) => setTo(event.target.value)}
            className={inputClass} />
          
          </Field>
        </div>
      }
    </Modal>);

}