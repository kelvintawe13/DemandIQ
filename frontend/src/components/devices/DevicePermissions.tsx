import { useCallback, useEffect, useState } from 'react';
import { KeyRoundIcon, ShieldCheckIcon, ShieldXIcon, UndoIcon } from 'lucide-react';
import { Panel } from '../ui/Panel';
import { StatusPill } from '../ui/StatusPill';
import { EmptyState } from '../ui/EmptyState';
import { LoadingState } from '../ui/LoadingState';
import { Field, inputClass } from '../ui/Field';
import { PermissionNote } from '../ui/PermissionNote';
import { api } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import {
  grantableCapabilities,
  grantStateLabel,
  mapEffectiveScopes,
  mapGrants,
  type DeviceGrant,
  type EffectiveScopes,
  type FleetDevice,
} from '../../lib/deviceFleet';
import { actionErrorMessage, loadErrorFrom, type LoadError } from './errors';

/** Expiry presets. The wire value is an ISO timestamp with a UTC offset. */
const EXPIRY_OPTIONS = [
  { value: '', label: 'No end date' },
  { value: '1', label: 'Ends in 1 hour' },
  { value: '8', label: 'Ends in 8 hours (a shift)' },
  { value: '24', label: 'Ends in 1 day' },
  { value: '168', label: 'Ends in 7 days' },
] as const;

function expiryIso(hours: string): string | undefined {
  if (!hours) return undefined;
  return new Date(Date.now() + Number(hours) * 3_600_000).toISOString();
}

/**
 * What this device may do, and what it may not do and why.
 *
 * The panel is two lists over one report. `effective` says what the till is
 * allowed to do today, attributed to its base scopes or to a named grant, and
 * `withheld` says what it is not, each with the nearest reason it is missing.
 * That second list is the point: "the till cannot send forecasts" is a support
 * ticket until somebody can read *why*; never granted, or granted last Tuesday
 * and never taken back, or taken back on the 3rd.
 *
 * Granting widens; it never narrows. The server refuses a grant for a
 * capability the device already holds in its base scopes, because revoking it
 * later would appear to remove the permission and would not. So the form offers
 * only the server's own `withheld` list, which is by construction the set of
 * capabilities that can actually be granted here.
 */
export function DevicePermissions({
  device,
  canManageGrants,
  onChanged
}: {
  device: FleetDevice;
  canManageGrants: boolean;
  onChanged?: () => void;
}) {
  const { toast } = useToast();
  const [report, setReport] = useState<EffectiveScopes | null>(null);
  const [grants, setGrants] = useState<DeviceGrant[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [capability, setCapability] = useState('');
  const [reason, setReason] = useState('');
  const [expiry, setExpiry] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [scopesPayload, grantsPayload] = await Promise.all([
        api.get(`/api/v1/device-grants/devices/${device.id}/effective-scopes`),
        api.get(`/api/v1/device-grants/devices/${device.id}/grants`),
      ]);
      setReport(mapEffectiveScopes(scopesPayload));
      setGrants(mapGrants(grantsPayload));
    } catch (e) {
      setReport(null);
      setGrants(null);
      setError(loadErrorFrom(e, 'Could not read what this device is allowed to do'));
    } finally {
      setLoading(false);
    }
  }, [device.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    if (!capability) {
      setFormError('Choose what this device is being allowed to do.');
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const expiresAt = expiryIso(expiry);
      const response = await api.post(`/api/v1/device-grants/devices/${device.id}/grants`, {
        capability,
        reason: reason.trim() || undefined,
        ...(expiresAt ? { expires_at: expiresAt } : {}),
      });
      setShowForm(false);
      setCapability('');
      setReason('');
      setExpiry('');
      toast(`${capability} is now allowed on ${device.label}.`, 'info');
      await applyMutation(response);
      onChanged?.();
    } catch (e) {
      setFormError(actionErrorMessage(e, 'Could not grant that capability.'));
    } finally {
      setSaving(false);
    }
  };

  /**
   * Fold a grant mutation's `effective_scopes` into the report already on
   * screen, then refetch the grant *list* only.
   *
   * The server answers a create or a revoke with the device's scopes as they
   * stand after the change; computed against its clock, which is the whole
   * point of not re-deriving expiry here. Refetching the scopes to get a value
   * the response already carried would be a second answer to a question that
   * was just answered, and a second round trip besides.
   */
  const applyMutation = useCallback(async (payload: unknown) => {
    const body = payload as { data?: { effective_scopes?: unknown } } | null;
    const effective = body?.data?.effective_scopes;
    if (Array.isArray(effective)) {
      setReport((current) => (current ? { ...current, effective: effective as string[] } : current));
    }
    const grantsPayload = await api.get(`/api/v1/device-grants/devices/${device.id}/grants`);
    setGrants(mapGrants(grantsPayload));
  }, [device.id]);

  const revoke = async (grant: DeviceGrant) => {
    setRevokingId(grant.id);
    try {
      const response = await api.delete(
        `/api/v1/device-grants/devices/${device.id}/grants/${grant.id}`
      );
      toast(`${grant.capability} is no longer allowed on ${device.label}.`, 'info');
      await applyMutation(response);
      onChanged?.();
    } catch (e) {
      toast(actionErrorMessage(e, 'Could not take that permission back.'), 'error');
    } finally {
      setRevokingId(null);
    }
  };

  const grantable = report ? grantableCapabilities(report) : [];

  return (
    <div className="flex flex-col gap-3">
      <Panel
        title="What this device can do"
        icon={ShieldCheckIcon}
        description="Read from the server on every load. The console does not work it out for itself."
      >
        {loading ? (
          <LoadingState label="Reading what this device is allowed to do" size="sm" />
        ) : error ? (
          <EmptyState
            variant={error.variant}
            size="sm"
            title={error.title}
            description={error.desc}
            action={{ label: 'Retry', onClick: () => void load() }}
          />
        ) : !report || report.effective.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="This device can do nothing yet"
            description="It holds no capabilities at all, so it cannot sync or report anything."
          />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {report.effective.map((capabilityName) => {
              const fromBase = report.baseScopes.includes(capabilityName);
              const grant = report.granted.find((item) => item.capability === capabilityName);
              return (
                <li
                  key={capabilityName}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-200 px-3 py-2"
                >
                  <span className="font-mono text-caption text-slate-800">{capabilityName}</span>
                  <StatusPill tone="success" dot>
                    {fromBase ? 'Always on' : 'Allowed by a grant'}
                  </StatusPill>
                  {grant?.expiresAt && (
                    <span className="text-micro text-slate-500">Ends {grant.expiresAt}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        <p className="mt-2.5 text-micro leading-relaxed text-slate-500">
          A device only holds a capability because it was enrolled with it or because somebody granted
          it. Every grant has a name against it and, for anything that changes something, a written
          reason.
        </p>
      </Panel>

      <Panel
        title="Extra permissions"
        icon={KeyRoundIcon}
        description="A grant is a decision with an author and, optionally, an end date."
        action={
          canManageGrants ? (
            <button
              type="button"
              onClick={() => setShowForm((open) => !open)}
              className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              {showForm ? 'Cancel' : 'Grant a capability'}
            </button>
          ) : null
        }
      >
        {showForm && canManageGrants && (
          <div className="mb-3 flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
            {formError && (
              <p
                role="alert"
                className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-meta leading-relaxed text-red-800"
              >
                {formError}
              </p>
            )}
            <Field
              id="grant-capability"
              label="What should this device be allowed to do?"
              hint="Only capabilities it does not already hold are listed; anything else would be refused."
            >
              <select
                id="grant-capability"
                value={capability}
                onChange={(event) => setCapability(event.target.value)}
                className={inputClass}
              >
                <option value="">Choose a capability…</option>
                {grantable.map((item) => (
                  <option key={item.capability} value={item.capability}>
                    {item.capability}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              id="grant-reason"
              label="Why does it need this?"
              hint="Required for anything that changes data. Kept on the record for as long as the device exists."
            >
              <input
                id="grant-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Connecting this till to the kitchen display for the dinner service"
                className={inputClass}
              />
            </Field>
            <Field id="grant-expiry" label="End date">
              <select
                id="grant-expiry"
                value={expiry}
                onChange={(event) => setExpiry(event.target.value)}
                className={inputClass}
              >
                {EXPIRY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => void submit()}
                disabled={saving}
                className="rounded-lg bg-brand-700 px-3.5 py-2 text-meta font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:bg-slate-300"
              >
                {saving ? 'Granting…' : 'Grant it'}
              </button>
            </div>
          </div>
        )}

        {!canManageGrants && (
          <PermissionNote>
            Granting a capability to a customer device needs device_grants:manage. You can read what
            each device holds, but not change it.
          </PermissionNote>
        )}

        {canManageGrants && !showForm && grantable.length === 0 && !loading && (
          <EmptyState
            variant="empty"
            size="sm"
            title="This device holds every capability there is"
            description="There is nothing left to grant it. The list of what it cannot do is empty."
          />
        )}

        {loading ? null : grants && grants.length > 0 ? (
          <ul className="flex flex-col gap-1.5">
            {grants.map((grant) => (
              <li
                key={grant.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-200 px-3 py-2"
              >
                <span className="font-mono text-caption text-slate-800">{grant.capability}</span>
                <StatusPill tone={grant.effective ? 'success' : 'neutral'} dot={grant.effective}>
                  {grantStateLabel(grant.state)}
                </StatusPill>
                <span className="min-w-0 flex-1 basis-full text-micro leading-relaxed text-slate-500">
                  {grant.detail}
                  {grant.reason ? ` (“${grant.reason}”)` : ''}
                  {grant.revokedBy ? ` (taken back by ${grant.revokedBy})` : ''}
                </span>
                {canManageGrants && (
                  <button
                    type="button"
                    onClick={() => void revoke(grant)}
                    disabled={revokingId === grant.id}
                    aria-label={`Take back ${grant.capability}`}
                    className="shrink-0 rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
                  >
                    <UndoIcon className="mr-1 inline h-3.5 w-3.5" strokeWidth={1.9} />
                    {revokingId === grant.id ? 'Taking back…' : 'Take back'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          grants && (
            <EmptyState
              variant="empty"
              size="sm"
              title="No extra permissions on this device"
              description="It can only do what it was enrolled with. Granting a capability gives it something extra, on somebody's authority and for a stated reason."
            />
          )
        )}
      </Panel>

      <Panel
        title="What this device cannot do"
        icon={ShieldXIcon}
        description="Every capability it does not hold, with the nearest reason it is missing."
      >
        {loading ? (
          <LoadingState label="Reading what this device cannot do" size="sm" />
        ) : !report || report.withheld.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="Nothing is missing"
            description="This device holds every capability in the registry, so there is no question of what it is refused."
          />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {report.withheld.map((item) => (
              <li
                key={item.capability}
                className="flex flex-wrap items-baseline gap-x-2 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-1.5"
              >
                <span className="font-mono text-caption text-slate-500">{item.capability}</span>
                <span className="min-w-0 flex-1 text-micro leading-relaxed text-slate-500">
                  {item.detail}
                </span>
              </li>
            ))}
          </ul>
        )}

        {!canManageGrants && (
          <PermissionNote className="mt-2.5">
            This is the answer to “why can this till not do that?”, the same report the API resolves
            server-side, so the console and the device&rsquo;s own call site cannot disagree.
          </PermissionNote>
        )}
      </Panel>
    </div>
  );
}
