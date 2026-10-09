import { Modal, ModalButton } from './Modal';

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: string;
  confirmLabel: string;
  tone?: 'primary' | 'danger';
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel,
  tone = 'primary'
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
      <>
          <ModalButton tone="ghost" onClick={onClose}>
            Cancel
          </ModalButton>
          <ModalButton
          tone={tone}
          onClick={() => {
            onConfirm();
            onClose();
          }}>
          
            {confirmLabel}
          </ModalButton>
        </>
      }>
      
      <p className="text-body-xs leading-relaxed text-slate-600 dark:text-slate-300">
        {body}
      </p>
    </Modal>);

}