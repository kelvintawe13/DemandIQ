import { useMemo, useState } from 'react';
import { CheckIcon, EyeIcon, HandHeartIcon, SearchIcon, XIcon } from 'lucide-react';
import { Panel } from '../ui/Panel';
import { StatusPill } from '../ui/StatusPill';
import { EmptyState } from '../ui/EmptyState';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PermissionNote } from '../ui/PermissionNote';
import { absoluteTime, type FleetDevice } from '../../lib/deviceFleet';
import type { MirrorEligibility } from '../../lib/mirrorApprovals';

/**
 * The customer's answer, per device — and the gates that stand between it and a
 * view.
 *
 * Consent is the customer's grant, not a workspace setting, so it is written
 * here as *the customer agreed on <date>* and it is withdrawable by the same
 * hand that recorded it. Withdrawing is the load-bearing half: the server ends
 * every live view and every queued request in the same call, because a customer
 * who says no is not satisfied by a flag flipping while somebody's screen stays
 * on a monitor. The confirmation says so in those words rather than burying it.
 *
 * The conditions below are the eligibility endpoint's own report, so the button
 * that is not offered always has the reason beside it. A control that silently
 * does nothing is indistinguishable from a broken deployment, and that is how a
 * consent problem becomes a support ticket.
 *
 * The name filter narrows the list already in memory rather than going back to
 * the API, which is the opposite of the fleet table's filters: this is one
 * workspace's handful of devices, all of them needed to answer "which till have
 * we not asked about?", and a server round trip per keystroke would only make
 * the list flash.
 */
export function MirrorConsentPanel({
  devices,
  eligibility,
  canRecordConsent,
  busyId,
  onRecordConsent,
  onWithdrawConsent,
  onRequest
}: {
  devices: FleetDevice[];
  eligibility: Record<string, MirrorEligibility | undefined>;
  canRecordConsent: boolean;
  busyId: string | null;
  onRecordConsent: (device: FleetDevice) => void;
  onWithdrawConsent: (device: FleetDevice) => void;
  onRequest: (device: FleetDevice) => void;
}) {
  const [withdrawing, setWithdrawing] = useState<FleetDevice | null>(null);
  const [query, setQuery] = useState('');

  const needle = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      needle === ''
        ? devices
        : devices.filter(
            (device) =>
              device.label.toLowerCase().includes(needle) ||
              device.type.toLowerCase().includes(needle),
          ),
    [devices, needle],
  );

  return (
    <Panel
      title="Customer consent"
      icon={HandHeartIcon}
      description="Nobody's screen can be watched until the customer has agreed, for that device, in writing."
    >
      {devices.length === 0 ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="No devices to ask about yet"
          description="Consent is recorded against a device, so there is nothing to do here until a till or screen is enrolled in the fleet console."
        />
      ) : (
        <div className="flex flex-col gap-2.5">
          <label className="flex max-w-xs flex-col gap-1">
            <span className="text-micro font-semibold text-slate-600">Find a device</span>
            <span className="relative">
              <SearchIcon
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500"
                strokeWidth={1.9}
                aria-hidden="true"
              />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Till 2, kitchen display…"
                aria-label="Find a device by name"
                className="w-full rounded-lg border border-slate-300 bg-white py-1.5 pl-8 pr-2.5 text-meta text-slate-800 outline-none transition-colors duration-150 ease-out focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
              />
            </span>
          </label>

          {shown.length === 0 ? (
            <EmptyState
              variant="search"
              size="sm"
              title="No device matches that"
              description={`Nothing in this workspace is called “${query.trim()}”. Check the spelling, or clear the box to see all ${devices.length} ${
                devices.length === 1 ? 'device' : 'devices'
              }.`}
              action={{ label: 'Clear the search', onClick: () => setQuery('') }}
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {shown.map((device) => {
                const state = eligibility[device.id];
                const granted = Boolean(device.consentAt);
                return (
                  <li
                    key={device.id}
                    className="flex flex-col gap-2 rounded-xl border border-slate-200 px-3.5 py-3"
                  >
                    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body-xs font-semibold text-slate-900">
                          {device.label}
                        </span>
                        <span className="block text-micro text-slate-500">
                          {granted
                            ? `The customer agreed on ${absoluteTime(device.consentAt)}.`
                            : 'No consent on file for this device.'}
                        </span>
                      </span>
                      <StatusPill tone={granted ? 'success' : 'neutral'} dot={granted}>
                        {granted ? 'Consent recorded' : 'No consent'}
                      </StatusPill>
                    </div>

                    {state && state.conditions.length > 0 && (
                      <ul className="flex flex-col gap-1">
                        {state.conditions.map((condition) => (
                          <li
                            key={condition.name}
                            className="flex items-start gap-1.5 text-micro leading-relaxed text-slate-500"
                          >
                            {condition.ok ? (
                              <CheckIcon
                                className="mt-0.5 h-3 w-3 shrink-0 text-brand-600"
                                strokeWidth={2.4}
                                aria-hidden="true"
                              />
                            ) : (
                              <XIcon
                                className="mt-0.5 h-3 w-3 shrink-0 text-amber-600"
                                strokeWidth={2.4}
                                aria-hidden="true"
                              />
                            )}
                            <span>
                              <span className="font-semibold text-slate-700">
                                {condition.name}:
                              </span>{' '}
                              {condition.ok ? 'in place.' : 'not in place.'}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}

                    {state && !state.eligible && state.message && (
                      <p className="rounded-lg bg-amber-50 px-2.5 py-2 text-micro leading-relaxed text-amber-900">
                        {state.message}
                      </p>
                    )}

                    <div className="flex flex-wrap gap-2">
                      {canRecordConsent ? (
                        granted ? (
                          <button
                            type="button"
                            onClick={() => setWithdrawing(device)}
                            disabled={busyId === device.id}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-meta font-semibold text-red-700 transition-colors duration-150 ease-out hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
                          >
                            {busyId === device.id ? 'Withdrawing…' : 'Withdraw consent'}
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => onRecordConsent(device)}
                            disabled={busyId === device.id}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
                          >
                            <CheckIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
                            {busyId === device.id ? 'Recording…' : 'Record the customer’s consent'}
                          </button>
                        )
                      ) : null}

                      {state && state.canRequest && state.eligible && (
                        <button
                          type="button"
                          onClick={() => onRequest(device)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                        >
                          <EyeIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                          Ask to watch this screen
                        </button>
                      )}
                    </div>

                    {!canRecordConsent && (
                      <PermissionNote>
                        Recording or withdrawing what a customer agreed to needs devices:manage. The
                        consent already on file is readable by anyone who can read the fleet.
                      </PermissionNote>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      <p className="mt-2.5 text-micro leading-relaxed text-slate-500">
        Withdrawing consent ends every live view of that device and cancels anything waiting for
        approval, in the same action. It is the customer&rsquo;s decision, and it can only be undone by
        the customer agreeing again.
      </p>

      <ConfirmDialog
        open={withdrawing !== null}
        onClose={() => setWithdrawing(null)}
        onConfirm={() => {
          if (withdrawing) onWithdrawConsent(withdrawing);
        }}
        title={`Withdraw consent for ${withdrawing?.label ?? 'this device'}?`}
        body="Any live view of that screen ends immediately, and any request waiting for approval is cancelled. There is no way to bring those views back from here. The customer has to agree again before anybody can watch that device."
        confirmLabel="Withdraw consent"
        tone="danger"
      />
    </Panel>
  );
}
