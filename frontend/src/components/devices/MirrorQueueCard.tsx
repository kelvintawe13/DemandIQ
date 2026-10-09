import { useState } from 'react';
import { CheckIcon, EyeIcon, ShieldCheckIcon, UsersIcon, XIcon } from 'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Field, inputClass } from '../ui/Field';
import { absoluteTime, type FleetDevice } from '../../lib/deviceFleet';
import { mirrorStatusLabel, type MirrorRequest } from '../../lib/mirrorApprovals';

/**
 * The windows the approver may choose.
 *
 * `mirroring.MAX_SESSION_DURATION` is thirty minutes and is the same constant on
 * every device, so the options are a fixed list rather than something read per
 * request. The server caps whatever is sent, which is why the longest option
 * says so in its own label.
 */
const WINDOWS = [
  { value: '5', label: '5 minutes' },
  { value: '10', label: '10 minutes (the usual support call)' },
  { value: '20', label: '20 minutes' },
  { value: '30', label: '30 minutes (the longest allowed)' },
];

/**
 * One request waiting for a second person.
 *
 * The two buttons are gated on the server's `approvable` flag and not on a
 * local re-derivation of the rule. That flag is computed for *this* viewer from
 * two questions — do you hold `device_mirror:approve`, and is this your own ask
 * — so when it is false there is a reason, and this card says which. A button
 * that cannot be pressed is worse than no button: it turns a rule that should
 * never have been reachable into a support ticket.
 *
 * The window is the approver's, and it is chosen here rather than at request
 * time. The router refuses `duration_minutes` on the request for exactly that
 * reason: the requester is the less trusted of the two, and how long somebody's
 * shift is watched for is not their decision to make.
 */
export function MirrorQueueCard({
  request,
  device,
  busy,
  canApprove,
  onApprove,
  onDeny
}: {
  request: MirrorRequest;
  device: FleetDevice | null;
  busy: boolean;
  /** What this seat holds, used only to word the note on a row it cannot decide. */
  canApprove: boolean;
  onApprove: (request: MirrorRequest, minutes: number) => void;
  onDeny: (request: MirrorRequest) => void;
}) {
  const [approving, setApproving] = useState(false);
  const [denying, setDenying] = useState(false);
  const [minutes, setMinutes] = useState(WINDOWS[1].value);

  return (
    <>
      <li className="flex flex-col gap-2.5 rounded-xl border border-slate-200 px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <EyeIcon className="h-4 w-4 shrink-0 text-brand-600" strokeWidth={1.8} aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body-xs font-semibold text-slate-900">
              {request.deviceLabel}
            </span>
            <span className="block text-micro text-slate-500">
              Asked for by {request.requestedByLabel} · {request.waiting}
              {request.decidedByLabel ? ` · decided by ${request.decidedByLabel}` : ''}
            </span>
          </span>
          <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-micro font-semibold text-amber-800">
            {mirrorStatusLabel(request.status, request.live)}
          </span>
        </div>

        <p className="text-meta leading-relaxed text-slate-700">“{request.reason}”</p>

        <p className="text-micro leading-relaxed text-slate-500">
          {request.consentAt
            ? `The customer agreed to mirroring on ${absoluteTime(request.consentAt)}.`
            : 'No customer consent is on file for this device, so it cannot be viewed even if this is approved.'}
        </p>

        {request.approvable ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setApproving(true)}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-1.5 text-meta font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:opacity-60"
            >
              <CheckIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
              {busy ? 'Deciding…' : 'Agree to let them watch'}
            </button>
            <button
              type="button"
              onClick={() => setDenying(true)}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
            >
              <XIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
              Refuse
            </button>
          </div>
        ) : (
          <p className="flex items-start gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-500">
            <UsersIcon
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500"
              strokeWidth={1.9}
              aria-hidden="true"
            />
            {request.mine
              ? 'This is your own request. A second person has to decide it. The person who asked can never be the person who agrees, however senior they are.'
              : canApprove
                ? 'Somebody else has already taken this one, or it is no longer waiting.'
                : 'Deciding needs device_mirror:approve. That permission is deliberately not given to the administrator role, so this seat can ask for a view but cannot agree to one.'}
          </p>
        )}

        {device && device.status !== 'active' && (
          <p className="text-micro leading-relaxed text-amber-800">
            This device is {device.status === 'revoked' ? 'revoked' : 'quarantined'}, so approving
            would be refused. Bring it back into service first.
          </p>
        )}
      </li>

      <Modal
        open={approving}
        onClose={() => setApproving(false)}
        title={`Let ${request.requestedByLabel} watch ${request.deviceLabel}?`}
        description="This opens a live view of a staff member’s screen, on a device in service right now."
        size="sm"
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setApproving(false)}>
              Cancel
            </ModalButton>
            <ModalButton
              onClick={() => {
                onApprove(request, Number(minutes));
                setApproving(false);
              }}
              disabled={busy}
            >
              <ShieldCheckIcon className="h-3.5 w-3.5" strokeWidth={2} />
              Approve the view
            </ModalButton>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <p className="text-meta leading-relaxed text-slate-600">
            The view stops by itself when the time below runs out, so nobody has to remember to end it,
            and either of you can end it earlier.
          </p>
          <Field id="mirror-window" label="How long may the view last?">
            <select
              id="mirror-window"
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
              className={inputClass}
            >
              {WINDOWS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={denying}
        onClose={() => setDenying(false)}
        onConfirm={() => onDeny(request)}
        title="Refuse this request?"
        body={`${request.requestedByLabel} asked to watch ${request.deviceLabel}. They will see that it was refused and why you said no. Nothing is recorded beyond that.`}
        confirmLabel="Refuse it"
        tone="danger"
      />
    </>
  );
}
