import { useState } from 'react';
import { Modal, ModalButton } from './Modal';

interface DeleteAccountDialogProps {
  open: boolean;
  onClose: () => void;
  /** False for OAuth-only accounts, which have no password to confirm. */
  hasPassword: boolean;
  onConfirm: (currentPassword: string | null) => Promise<void>;
}

/**
 * Account erasure confirmation.
 *
 * Deliberately heavier than ConfirmDialog: erasure is irreversible, so the
 * user must type DELETE and re-enter their password. The password box is
 * hidden for OAuth-only accounts, which have no password to confirm.
 */
export function DeleteAccountDialog({
  open,
  onClose,
  hasPassword,
  onConfirm
}: DeleteAccountDialogProps) {
  const [typed, setTyped] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    if (busy) return;
    setTyped('');
    setPassword('');
    setError(null);
    onClose();
  };

  const canSubmit = typed === 'DELETE' && (!hasPassword || password.length > 0) && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(hasPassword ? password : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not delete the account. Try again.');
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Delete your account?"
      description="This permanently erases your account."
      size="sm"
      footer={
        <>
          <ModalButton tone="ghost" onClick={close} disabled={busy}>
            Cancel
          </ModalButton>
          <ModalButton tone="danger" onClick={() => void submit()} disabled={!canSubmit}>
            {busy ? 'Deleting…' : 'Delete account'}
          </ModalButton>
        </>
      }>
      <div className="flex flex-col gap-3">
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5">
          <p className="text-body-xs leading-relaxed text-red-800">
            This cannot be undone. Your profile, sessions and linked sign-in methods are
            erased immediately.
          </p>
          <p className="mt-1.5 text-body-xs leading-relaxed text-red-800">
            If you are the last member of your workspace, that workspace and its data are
            deleted too.
          </p>
        </div>

        {hasPassword && (
          <label className="flex flex-col gap-1.5">
            <span className="text-caption font-semibold text-slate-600">Your password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-body-sm text-slate-800 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
            />
          </label>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-caption font-semibold text-slate-600">
            Type <span className="font-mono text-slate-900">DELETE</span> to confirm
          </span>
          <input
            type="text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            placeholder="DELETE"
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 font-mono text-body-sm text-slate-800 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
          />
        </label>

        {error && (
          <p role="alert" className="text-meta text-red-600">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}