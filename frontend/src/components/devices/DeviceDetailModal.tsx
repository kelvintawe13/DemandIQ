import { useState } from 'react';
import {
  CircleOffIcon,
  KeyRoundIcon,
  PowerIcon,
  RotateCwIcon,
  ShieldAlertIcon } from
'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { StatusPill } from '../ui/StatusPill';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { TabBar, type TabItem } from '../ui/TabBar';
import { PermissionNote } from '../ui/PermissionNote';
import { useToast } from '../../contexts/ToastContext';
import { api } from '../../api/client';
import {
  absoluteTime,
  deviceStatusLabel,
  mapSecret,
  type DeviceSecret,
  type FleetDevice,
} from '../../lib/deviceFleet';
import { DevicePermissions } from './DevicePermissions';
import { DeviceEvents } from './DeviceEvents';
import { actionErrorMessage } from './errors';

type TabId = 'permissions' | 'events';
type Action = 'rotate' | 'quarantine' | 'restore' | 'revoke' | null;

const ACTION_COPY: Record<
  Exclude<Action, null>,
  { title: string; confirm: string; body: string; label: string; tone: 'primary' | 'danger' }
> = {
  rotate: {
    title: 'Replace this device’s key?',
    confirm: 'Yes, rotate the key',
    label: 'Rotate the key',
    body:
      'The key you have on the counter stops working the moment this finishes, and the new one is shown once and never again. The till will stop reporting until somebody is standing at it with the new key.',
    tone: 'danger',
  },
  quarantine: {
    title: 'Stop this device without losing it?',
    confirm: 'Quarantine it',
    label: 'Quarantine',
    body:
      'The device stops being accepted straight away and can be brought back later. Use this for a till that is misbehaving or has left the restaurant, not for one you have lost, which is a revoke.',
    tone: 'primary',
  },
  restore: {
    title: 'Put this device back in service?',
    confirm: 'Restore it',
    label: 'Restore',
    body: 'The device will be accepted again and will start reporting from its next check-in.',
    tone: 'primary',
  },
  revoke: {
    title: 'Revoke this device for good?',
    confirm: 'Revoke it',
    label: 'Revoke',
    body:
      'This is terminal. The device can never be accepted again and cannot be re-enrolled. A device that is lost, sold or stolen has to be replaced, not revived. Its history stays on file.',
    tone: 'danger',
  },
};

/**
 * One device, in full: what it is, what it may do, and the four things an
 * operator can do to it.
 *
 * Quarantine and revocation are kept apart on the surface as well as on the
 * server, because they mean different things to the person holding the till.
 * Quarantine is a switch that comes back; revoke is a decision about a device
 * that is gone. Both are confirmed, and both say plainly what happens to the
 * key and the record.
 */
export function DeviceDetailModal({
  device,
  canManage,
  canManageGrants,
  onClose,
  onChanged,
  onSecret
}: {
  device: FleetDevice;
  canManage: boolean;
  canManageGrants: boolean;
  onClose: () => void;
  onChanged?: () => void;
  onSecret: (secret: DeviceSecret) => void;
}) {
  const { toast } = useToast();
  const [tab, setTab] = useState<TabId>('permissions');
  const [action, setAction] = useState<Action>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!action) return;
    setBusy(true);
    try {
      if (action === 'rotate') {
        const payload = await api.post(`/api/v1/devices/${device.id}/rotate`);
        const secret = mapSecret(payload);
        if (secret) {
          onSecret(secret);
        } else {
          toast('The key was rotated but no new key came back. Rotate again to get one.', 'error');
        }
      } else {
        await api.post(`/api/v1/devices/${device.id}/${action}`);
        toast(
          action === 'revoke'
            ? `${device.label} is revoked. It can never be used again.`
            : action === 'quarantine'
              ? `${device.label} is quarantined and has stopped reporting.`
              : `${device.label} is back in service.`,
          'info',
        );
      }
      onChanged?.();
    } catch (e) {
      toast(actionErrorMessage(e, 'That could not be done.'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const tabs: readonly TabItem<TabId>[] = [
    { id: 'permissions', label: 'What it can do' },
    { id: 'events', label: 'Recent activity' },
  ];

  const record: Array<[string, string]> = [
    ['Kind', device.type],
    ['Platform', device.platform ?? 'Not reported'],
    ['Location', device.locationId ? 'Bound to a restaurant' : 'Not bound to a restaurant'],
    ['App version', device.appVersion],
    ['Last seen', device.lastSeen],
    ['Key on file', `starts with ${device.credentialPrefix}`],
    ['Enrolled', absoluteTime(device.enrolledAt)],
  ];

  return (
    <>
      <Modal
        open
        onClose={onClose}
        title={device.label}
        description="Everything the console knows about this device, read from the server."
        size="xl"
        footer={
          <ModalButton tone="ghost" onClick={onClose}>
            Close
          </ModalButton>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill
              tone={
                device.status === 'active' ? 'success' : device.status === 'quarantined' ? 'warning' : 'critical'
              }
              dot
            >
              {deviceStatusLabel(device.status)}
            </StatusPill>
            <span className="text-micro text-slate-500">{device.lastSeen}</span>
          </div>

          <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
            {record.map(([term, value]) => (
              <div key={term} className="flex items-baseline justify-between gap-3 border-b border-slate-100 py-1">
                <dt className="text-micro text-slate-500">{term}</dt>
                <dd className="min-w-0 text-right text-meta text-slate-800">{value}</dd>
              </div>
            ))}
          </dl>

          {canManage ? (
            <div className="flex flex-wrap gap-2 rounded-xl border border-slate-200 p-2.5">
              <ActionButton
                label={ACTION_COPY.rotate.label}
                icon={RotateCwIcon}
                onClick={() => setAction('rotate')}
                disabled={!device.rotatable}
                title={device.rotatable ? undefined : 'A revoked device is re-enrolled, not revived.'}
              />
              {device.status === 'quarantined' ? (
                <ActionButton
                  label={ACTION_COPY.restore.label}
                  icon={PowerIcon}
                  onClick={() => setAction('restore')}
                />
              ) : (
                <ActionButton
                  label={ACTION_COPY.quarantine.label}
                  icon={CircleOffIcon}
                  onClick={() => setAction('quarantine')}
                  disabled={!device.rotatable}
                />
              )}
              <ActionButton
                label={ACTION_COPY.revoke.label}
                icon={ShieldAlertIcon}
                onClick={() => setAction('revoke')}
                disabled={device.status === 'revoked'}
                tone="danger"
              />
            </div>
          ) : (
            <PermissionNote>
              Changing a device (rotating its key, quarantining it or revoking it) needs
              devices:manage. Everything on this screen is readable either way.
            </PermissionNote>
          )}

          <TabBar tabs={tabs} value={tab} onChange={setTab} ariaLabel="Device detail" />

          {tab === 'permissions' ? (
            <DevicePermissions
              device={device}
              canManageGrants={canManageGrants}
              onChanged={onChanged}
            />
          ) : (
            <DeviceEvents deviceId={device.id} />
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={action !== null}
        onClose={() => setAction(null)}
        onConfirm={() => {
          void run();
        }}
        title={action ? ACTION_COPY[action].title : ''}
        body={busy ? 'Working…' : action ? ACTION_COPY[action].body : ''}
        confirmLabel={action ? ACTION_COPY[action].confirm : ''}
        tone={action ? ACTION_COPY[action].tone : 'primary'}
      />
    </>
  );
}

function ActionButton({
  label,
  icon: Icon,
  onClick,
  disabled,
  title,
  tone = 'primary'
}: {
  label: string;
  icon: typeof KeyRoundIcon;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  tone?: 'primary' | 'danger';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={[
        'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-meta font-semibold transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-50',
        tone === 'danger'
          ? 'border-red-200 text-red-700 hover:bg-red-50'
          : 'border-slate-300 text-slate-700 hover:bg-slate-50',
      ].join(' ')}
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={1.9} />
      {label}
    </button>
  );
}
