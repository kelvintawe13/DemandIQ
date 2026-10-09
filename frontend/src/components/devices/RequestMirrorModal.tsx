import { useEffect, useState } from 'react';
import { EyeIcon, UsersIcon } from 'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { Field, inputClass } from '../ui/Field';
import { EmptyState } from '../ui/EmptyState';
import { api, ApiException } from '../../api/client';
import type { FleetDevice } from '../../lib/deviceFleet';

/**
 * Ask to watch a device's screen — the first of two people, not a permission.
 *
 * The window is deliberately absent from the payload. The router refuses
 * `duration_minutes` with a 400, and that refusal is the right shape for a
 * field the *requester* must not set: the requester is the less trusted of the
 * two, and how long somebody's shift is watched for is the approver's call.
 * Omitting it here is not an oversight, so it is commented rather than left to
 * look like one.
 *
 * On success the dialog does not close. A 201 here means "your request is in
 * somebody else's queue", and a requester who reads 201 as a green light has
 * been told a falsehood by an HTTP code — so the server's own notice is shown
 * before anything else.
 */
export function RequestMirrorModal({
  open,
  device,
  onClose,
  onRequested
}: {
  open: boolean;
  device: FleetDevice | null;
  onClose: () => void;
  onRequested: () => void;
}) {
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setReason('');
      setError(null);
      setNotice(null);
      setSaving(false);
    }
  }, [open]);

  if (!device) return null;

  const submit = async () => {
    if (!reason.trim()) {
      setError('Say why the screen is being viewed. Whoever approves reads this first.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = await api.post<{ data?: { notice?: unknown } }>(
        `/api/v1/device-mirror/devices/${device.id}/mirror-requests`,
        { reason: reason.trim() },
      );
      const serverNotice = payload?.data?.notice;
      setNotice(
        typeof serverNotice === 'string' && serverNotice.trim() !== ''
          ? serverNotice
          : 'This request views nothing yet. A second workspace member has to approve it first.',
      );
      onRequested();
    } catch (e) {
      setError(
        e instanceof ApiException
          ? e.error.message
          : e instanceof Error
            ? e.message
            : 'Could not raise that request.',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={notice ? 'Your request is waiting for somebody else' : `Ask to watch ${device.label}`}
      description={
        notice
          ? undefined
          : 'Mirroring shows a staff member’s live screen during their shift. A second person has to agree before anyone can see it.'
      }
      size="md"
      footer={
        <>
          <ModalButton tone="ghost" onClick={onClose}>
            Close
          </ModalButton>
          {!notice && (
            <ModalButton onClick={() => void submit()} disabled={saving}>
              <EyeIcon className="h-3.5 w-3.5" strokeWidth={2} />
              {saving ? 'Sending…' : 'Send the request'}
            </ModalButton>
          )}
        </>
      }
    >
      {notice ? (
        <div className="flex flex-col gap-3">
          <EmptyState
            variant="empty"
            size="sm"
            title="Nothing is being viewed yet"
            description={notice}
            hints={[
              'You cannot approve your own request, however senior you are.',
              'The approver chooses how long the view may last, and can end it early.',
              'The customer’s recorded consent is what makes any view possible at all.',
            ]}
          />
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {error && (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-meta leading-relaxed text-red-800"
            >
              {error}
            </p>
          )}

          <Field
            id="mirror-reason"
            label="Why does the screen need to be seen?"
            hint="Recorded on the request and read by whoever approves it. “Till 2 is showing an error on the payment screen” is enough."
          >
            <textarea
              id="mirror-reason"
              value={reason}
              rows={3}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Till 2 is showing an error on the payment screen and the shift is stuck"
              className={inputClass}
            />
          </Field>

          <p className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2.5 text-caption leading-relaxed text-slate-600">
            <UsersIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-600" strokeWidth={1.9} aria-hidden="true" />
            You are asking, not deciding. A different member of this workspace has to approve, and the
            time limit is theirs to set, not yours.
          </p>
        </div>
      )}
    </Modal>
  );
}
