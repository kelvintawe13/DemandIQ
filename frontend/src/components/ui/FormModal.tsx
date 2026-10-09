import { useId } from 'react';
import type { ReactNode } from 'react';
import { Modal, ModalButton } from './Modal';

interface FormModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** Called with the already-prevented form submit (Enter key included). */
  onSubmit: () => void;
  submitLabel?: string;
  saving?: boolean;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  children: ReactNode;
  /** Extra buttons between Cancel and Save (e.g. a destructive action). */
  footerExtra?: ReactNode;
}

/**
 * The edit dialog every admin record uses: a real `<form>` (Enter submits),
 * Cancel/Save footer, and a busy state on save.
 *
 * Fields are passed as children so each record keeps its own form; the dialog
 * only owns the chrome, focus handling (via `Modal`) and submit wiring.
 */
export function FormModal({
  open,
  onClose,
  title,
  description,
  onSubmit,
  submitLabel = 'Save changes',
  saving = false,
  size = 'md',
  children,
  footerExtra,
}: FormModalProps) {
  const formId = useId();
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size={size}
      footer={
        <>
          <ModalButton tone="ghost" onClick={onClose}>
            Cancel
          </ModalButton>
          {footerExtra}
          <ModalButton type="submit" form={formId} disabled={saving}>
            {saving ? 'Saving…' : submitLabel}
          </ModalButton>
        </>
      }>
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
        className="flex flex-col gap-3">
        {children}
      </form>
    </Modal>
  );
}
