import { useState } from 'react';
import { AlertTriangleIcon, CheckIcon, CopyIcon, KeyRoundIcon } from 'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { Field } from '../ui/Field';
import type { DeviceSecret } from '../../lib/deviceFleet';

/**
 * The only place an enrollment secret is ever rendered.
 *
 * The backend stores a bcrypt hash and a non-secret prefix, so there is no
 * second read that could return the secret — closing this dialog loses it for
 * good, and the only way to get another one is to rotate, which immediately
 * stops the old credential working on a till that is mid-shift. The copy here
 * is written for someone who has just handed a screen to a restaurant owner:
 * it says what the string is, that it is the till's only key, and what to do
 * with it, rather than calling it a "token".
 */
export function SecretModal({
  secret,
  onClose,
  onRotated
}: {
  secret: DeviceSecret | null;
  onClose: () => void;
  /** Called after a rotation, so the caller can reload the fleet behind it. */
  onRotated?: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!secret) return null;

  const copy = () => {
    // `navigator.clipboard` is absent in insecure contexts and in tests; the
    // dialog still shows the secret either way, so a failed copy is a nudge
    // rather than a dead end.
    void navigator.clipboard?.writeText(secret.secret);
    setCopied(true);
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Copy this now. It is not shown again."
      description={`${secret.deviceLabel} cannot sync until it has this key.`}
      size="md"
      footer={
        <>
          <ModalButton
            tone="ghost"
            onClick={() => {
              if (onRotated) onRotated();
              onClose();
            }}
          >
            I have written it down
          </ModalButton>
          <ModalButton
            onClick={() => {
              copy();
            }}
          >
            <CopyIcon className="h-3.5 w-3.5" strokeWidth={2} />
            {copied ? 'Copied' : 'Copy the key'}
          </ModalButton>
        </>
      }
    >
      <div
        role="alert"
        className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5"
      >
        <AlertTriangleIcon
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-700"
          strokeWidth={2}
          aria-hidden="true"
        />
        <p className="text-meta leading-relaxed text-amber-900">
          This is the till&rsquo;s only key. It is shown once, at the moment it is created, and there is
          no way to read it back from DemandIQ. If it is lost, the only fix is to rotate the key,
          which stops the old one working straight away.
        </p>
      </div>

      <div className="mt-3.5">
        <Field
          id="device-secret"
          label="Enrollment key"
          hint={`Starts with ${secret.prefix} · ${secret.warning}`}
        >
          <div className="flex items-stretch gap-2">
            <input
              id="device-secret"
              readOnly
              value={secret.secret}
              onFocus={(event) => event.currentTarget.select()}
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-body-xs text-slate-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
            />
            <button
              type="button"
              onClick={copy}
              aria-label="Copy the enrollment key"
              className="shrink-0 rounded-lg border border-slate-300 px-2.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              {copied ? (
                <CheckIcon className="h-4 w-4 text-brand-600" strokeWidth={2.2} />
              ) : (
                <CopyIcon className="h-4 w-4" strokeWidth={1.9} />
              )}
            </button>
          </div>
        </Field>
      </div>

      <ul className="mt-3.5 flex flex-col gap-1.5">
        {[
          'Paste it into the device setup screen on the till, on the counter, in front of the customer.',
          'The key is the device. Anyone holding it can report as that till until you rotate it.',
          'If a device is lost or sold, rotate its key from the fleet list rather than reading this one again.',
        ].map((hint) => (
          <li
            key={hint}
            className="flex items-start gap-2 text-caption leading-relaxed text-slate-500"
          >
            <KeyRoundIcon
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-600"
              strokeWidth={1.9}
              aria-hidden="true"
            />
            {hint}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
