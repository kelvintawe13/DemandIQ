import { useEffect, useState } from 'react';
import { TabletIcon } from 'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { Field, inputClass } from '../ui/Field';
import { api, ApiException } from '../../api/client';
import { mapSecret, type DeviceSecret } from '../../lib/deviceFleet';

export interface LocationOption {
  id: string;
  name: string;
}

/** Device types the router's default vocabulary covers, in restaurant words. */
const TYPES = [
  { value: 'pos', label: 'Till (POS)' },
  { value: 'kds', label: 'Kitchen display' },
  { value: 'tablet', label: 'Tablet' },
  { value: 'display', label: 'Customer-facing display' },
  { value: 'handheld', label: 'Handheld' },
];

/**
 * Enrol a device, and hand its one-time key straight to `SecretModal`.
 *
 * The label is the only field the server insists on, and it is the one an
 * operator will read under pressure at 6pm — "Till 2" beats a UUID, so it is
 * required here and defaulted to the type ("Till — Kigali") rather than left
 * blank. Location is optional: a device can be enrolled before it is installed
 * and bound to a restaurant later.
 */
export function EnrollDeviceModal({
  open,
  locations,
  onClose,
  onEnrolled
}: {
  open: boolean;
  locations: LocationOption[];
  onClose: () => void;
  onEnrolled: (secret: DeviceSecret) => void;
}) {
  const [label, setLabel] = useState('');
  const [type, setType] = useState('pos');
  const [platform, setPlatform] = useState('');
  const [locationId, setLocationId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The form belongs to the dialog, so it is emptied when the dialog closes
  // rather than when the next enrolment starts.
  useEffect(() => {
    if (!open) {
      setLabel('');
      setType('pos');
      setPlatform('');
      setLocationId('');
      setError(null);
      setSaving(false);
    }
  }, [open]);

  const submit = async () => {
    if (!label.trim()) {
      setError('Give the device a name you will recognise on the counter, for example "Till 2".');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, string> = {
        label: label.trim(),
        device_type: type,
      };
      if (platform.trim()) payload.platform = platform.trim();
      // Omitted rather than sent as null: an explicit null clears the binding on
      // PATCH, and enrolment is not a clearing.
      if (locationId) payload.location_id = locationId;
      const res = await api.post('/api/v1/devices', payload);
      const secret = mapSecret(res);
      if (!secret) {
        setError('The device was created but no key came back. Rotate its key from the list to get one.');
        setSaving(false);
        return;
      }
      onEnrolled(secret);
      onClose();
    } catch (e) {
      setError(
        e instanceof ApiException
          ? e.error.message
          : e instanceof Error
            ? e.message
            : 'Could not enrol that device.',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Enrol a device"
      description="A restaurant device running DemandIQ on a till, a kitchen screen or a tablet."
      size="md"
      footer={
        <>
          <ModalButton tone="ghost" onClick={onClose}>
            Cancel
          </ModalButton>
          <ModalButton onClick={() => void submit()} disabled={saving}>
            <TabletIcon className="h-3.5 w-3.5" strokeWidth={2} />
            {saving ? 'Enrolling…' : 'Enrol and show the key'}
          </ModalButton>
        </>
      }
    >
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
          id="enrol-label"
          label="What the staff will know it as"
          hint="Shown on the fleet list. Something like “Till 2” or “KDS pass”."
        >
          <input
            id="enrol-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Till 2"
            className={inputClass}
          />
        </Field>

        <Field id="enrol-type" label="Kind of device">
          <select
            id="enrol-type"
            value={type}
            onChange={(event) => setType(event.target.value)}
            className={inputClass}
          >
            {TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>

        <Field
          id="enrol-location"
          label="Restaurant"
          hint="Optional. A device reports against the restaurant it is bound to; you can bind it later."
        >
          <select
            id="enrol-location"
            value={locationId}
            onChange={(event) => setLocationId(event.target.value)}
            className={inputClass}
          >
            <option value="">Not bound to a restaurant yet</option>
            {locations.map((location) => (
              <option key={location.id} value={location.id}>
                {location.name}
              </option>
            ))}
          </select>
        </Field>

        <Field id="enrol-platform" label="Operating system" hint="Optional. Sent with the key for support.">
          <input
            id="enrol-platform"
            value={platform}
            onChange={(event) => setPlatform(event.target.value)}
            placeholder="Android 13"
            className={inputClass}
          />
        </Field>
      </div>
    </Modal>
  );
}
