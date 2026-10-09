import { useEffect, useState } from 'react';
import { CheckIcon } from 'lucide-react';
import { Modal, ModalButton } from './Modal';

const locationOptions = [
'All Locations',
'Kigali Heights',
'Kimironi Branch',
'Nyarutarama Cafe',
'CBD Canteen',
'Remera Grill',
'Kacyiru Bistro'];


interface LocationPickerModalProps {
  open: boolean;
  onClose: () => void;
  value: string;
  onApply: (label: string) => void;
  options?: string[];
}

export function LocationPickerModal({
  open,
  onClose,
  value,
  onApply,
  options,
}: LocationPickerModalProps) {
  const [selected, setSelected] = useState(value);
  const opts = options && options.length > 0 ? options : locationOptions;

  useEffect(() => {
    setSelected(value);
  }, [value, open]);

  // Ensure selected is valid option; fallback to first
  useEffect(() => {
    if (!opts.includes(selected) && opts.length > 0) {
      // keep as is; will be handled on apply
    }
  }, [opts, selected]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Location"
      description="Scope this screen to one site or view the whole group."
      footer={
      <>
          <ModalButton tone="ghost" onClick={onClose}>
            Cancel
          </ModalButton>
          <ModalButton
          onClick={() => {
            onApply(selected);
            onClose();
          }}>
          
            Apply
          </ModalButton>
        </>
      }>
      
      <ul className="flex flex-col gap-1">
        {opts.map((option) =>
        <li key={option}>
            <button
            type="button"
            aria-pressed={selected === option}
            onClick={() => setSelected(option)}
            className={[
            'flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-body-xs transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            selected === option ?
            'bg-brand-50 font-semibold text-brand-800' :
            'text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800'].
            join(' ')}>
            
              {option}
              {selected === option &&
            <CheckIcon className="h-4 w-4 shrink-0" strokeWidth={2.4} />
            }
            </button>
          </li>
        )}
      </ul>
    </Modal>);

}
