import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ClockIcon,
  MonitorIcon,
  PencilIcon,
  PlusIcon,
  ShieldCheckIcon,
  UtensilsCrossedIcon,
} from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { StatCard, type StatTone } from '../components/ui/StatCard';
import { StatusPill } from '../components/ui/StatusPill';
import { EmptyState } from '../components/ui/EmptyState';
import { LoadingState } from '../components/ui/LoadingState';
import { type Column } from '../components/ui/Table';
import { RecordList } from '../components/ui/RecordList';
import { FormModal } from '../components/ui/FormModal';
import { Field, inputClass } from '../components/ui/Field';
import { CountChart } from '../components/ui/CountChart';
import { MixDonut } from '../components/ui/MixDonut';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { api } from '../api/client';
import {
  STALE_AFTER_HOURS,
  appVersionCounts,
  deviceStatusLabel,
  devicesByLocation,
  fleetAttention,
  lastSeenBuckets,
  mapDevices,
  statusMixOf,
  type DeviceSecret,
  type FleetDevice,
} from '../lib/deviceFleet';
import { DeviceDetailModal } from '../components/devices/DeviceDetailModal';
import { EnrollDeviceModal, type LocationOption } from '../components/devices/EnrollDeviceModal';
import { SecretModal } from '../components/devices/SecretModal';
import { loadErrorFrom, type LoadError } from '../components/devices/errors';

/**
 * The restaurant's own devices: the tills, kitchen screens and tablets running
 * DemandIQ on a counter, during a service.
 *
 * This is not the same screen as **Devices & Sessions** (`pages/Devices.tsx`),
 * which lists everywhere *this person* is signed in and how to sign out of it.
 * Nothing here is about a person: these are machines in a restaurant, they
 * report on their own schedule, and a fleet of them is what an owner is
 * actually responsible for.
 *
 * The page answers one question first — **is the fleet reporting?** — because
 * `status` does not: a device is `active` from the moment it is enrolled, long
 * before it sends anything. So the reading order is the state of the fleet
 * (a KPI strip), then the devices that want a person (the attention list, beside
 * the table), then the shape of the whole fleet (the charts). A device that has
 * never checked in is the one thing that stops a till feeding a forecast, and
 * the previous screen said nothing about it.
 *
 * Every write here is a decision about somebody's hardware mid-shift, so the
 * page is deliberately plain about consequences: a key is shown once and the
 * dialog says so before the secret appears, quarantine says it is reversible
 * while revoke says it is not, and a device is never deleted; a lost till is
 * revoked and stays on file under its name.
 */
export function DeviceFleet() {
  const { user } = useSession();
  const { toast } = useToast();
  const canManage = (user.permissions ?? []).includes('devices:manage');
  const canManageGrants = (user.permissions ?? []).includes('device_grants:manage');

  const [devices, setDevices] = useState<FleetDevice[] | null>(null);
  const [locations, setLocations] = useState<LocationOption[]>([]);
  const [status, setStatus] = useState('all');
  const [location, setLocation] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [secret, setSecret] = useState<DeviceSecret | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  // The edit dialog: what `PATCH /devices/{id}` really changes, namely the label the
  // staff know the machine by, and the restaurant it reports into.
  const [editing, setEditing] = useState<FleetDevice | null>(null);
  const [editDraft, setEditDraft] = useState({ label: '', locationId: '' });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // The filters go to the API rather than being applied here: the fleet
      // endpoint is the one place the answer is built, and a client-side copy
      // would be a second answer to "which devices can this seat see".
      const payload = await api.get('/api/v1/devices', {
        status: status === 'all' ? undefined : status,
        location_id: location === 'all' ? undefined : location,
      });
      setDevices(mapDevices(payload));
      try {
        const locationPayload = await api.get<{ data?: Array<{ id?: unknown; name?: unknown }> }>(
          '/api/v1/locations',
          { limit: 100 },
        );
        const rows = Array.isArray(locationPayload.data) ? locationPayload.data : [];
        setLocations(
          rows
            .filter((row): row is { id: string; name: string } => typeof row.id === 'string' && typeof row.name === 'string'),
        );
      } catch {
        // The restaurant names are a convenience. A seat that cannot read them
        // still gets its fleet, described by id rather than by name.
        setLocations([]);
      }
    } catch (e) {
      setDevices([]);
      setError(loadErrorFrom(e, 'Could not load the device fleet'));
    } finally {
      setLoading(false);
    }
  }, [status, location]);

  useEffect(() => {
    void load();
  }, [load]);

  const nameFor = useCallback(
    (locationId: string | null) => {
      if (!locationId) return 'Not bound to a restaurant';
      return locations.find((item) => item.id === locationId)?.name ?? 'Restaurant not listed';
    },
    [locations],
  );

  const rows = devices ?? [];
  const filtered = status !== 'all' || location !== 'all';
  const detail = rows.find((device) => device.id === detailId) ?? null;

  /*
   * Everything below reads `rows` — the same list the table renders, filtered by
   * the API. A chart or a tile that took its numbers from anywhere else could
   * disagree with the table directly under it, so all of it is derived here,
   * once. The reference point is captured per render so a count and the sentence
   * beside it cannot be computed against two different clocks.
   */
  const now = Date.now();
  const overview = useMemo(() => {
    const attention = fleetAttention(rows, now);
    const mix = statusMixOf(rows);
    const reporting = rows.filter(
      (device) => device.lastSeenAt !== null && now - device.lastSeenAt < STALE_AFTER_HOURS * 3_600_000,
    ).length;
    const bound = rows.filter((device) => device.locationId).length;
    const restaurants = new Set(rows.map((device) => device.locationId).filter(Boolean)).size;
    const neverSeen = rows.filter((device) => device.lastSeenAt === null).length;
    const versions = appVersionCounts(rows);
    return {
      attention,
      mix,
      reporting,
      bound,
      restaurants,
      neverSeen,
      versions,
      neverSeenCount: attention.filter((item) => item.reason === 'never-seen').length,
      quiet: attention.filter((item) => item.reason === 'quiet').length,
      quarantined: attention.filter((item) => item.reason === 'quarantined').length,
      buckets: lastSeenBuckets(rows, now),
      byLocation: devicesByLocation(rows, nameFor),
    };
    // `now` is deliberately not a dependency: it would rebuild on every render
    // and the only thing it changes is which side of a threshold a device sits
    // on, which the next load re-reads anyway.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, nameFor]);

  const openEdit = (device: FleetDevice) => {
    setEditing(device);
    setEditDraft({ label: device.label, locationId: device.locationId ?? '' });
  };
  const saveEdit = async () => {
    if (!editing || !editDraft.label.trim()) return;
    setSaving(true);
    try {
      await api.patch(`/api/v1/devices/${editing.id}`, {
        label: editDraft.label.trim(),
        location_id: editDraft.locationId || null,
      });
      toast('Device updated');
      setEditing(null);
      void load();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to update the device', 'error');
    } finally {
      setSaving(false);
    }
  };

  const columns: Column<FleetDevice>[] = [
    {
      key: 'device',
      label: 'Device',
      primary: true,
      sortable: true,
      render: (device) => (
        <span className="flex min-w-0 max-w-[124px] items-center gap-2 lg:max-w-none">
          <MonitorIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} aria-hidden="true" />
          <span className="min-w-0">
            <span className="block truncate font-semibold text-slate-900">{device.label}</span>
            <span className="block text-micro text-slate-500">{device.type}</span>
          </span>
        </span>
      ),
    },
    {
      key: 'location',
      label: 'Restaurant',
      sortable: true,
      render: (device) => nameFor(device.locationId),
    },
    {
      key: 'status',
      label: 'Status',
      sortable: true,
      render: (device) => (
        <StatusPill
          tone={
            device.status === 'active' ? 'success' : device.status === 'quarantined' ? 'warning' : 'critical'
          }
          dot
        >
          {deviceStatusLabel(device.status)}
        </StatusPill>
      ),
    },
    {
      key: 'lastSeen',
      label: 'Last seen',
      sortable: true,
      /*
       * The status says what a person decided; this says whether the machine is
       * actually talking. A till is `active` from the moment it is enrolled, so
       * "In service" beside "Never seen" was two answers that disagreed — the
       * timestamp gets the warning marker when it is the reason this device is
       * on the attention list.
       */
      render: (device) => {
        const item = fleetAttention([device], now)[0];
        const stale = item?.reason === 'quiet' || item?.reason === 'never-seen';
        return (
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
            {stale && (
              <AlertTriangleIcon
                className="h-3 w-3 shrink-0 text-amber-600"
                strokeWidth={2.2}
                aria-hidden="true"
              />
            )}
            <span className={stale ? 'font-medium text-amber-700' : undefined}>{device.lastSeen}</span>
          </span>
        );
      },
    },
    {
      key: 'appVersion',
      label: 'App version',
      sortable: true,
      /* Lowest-value field in the row, and the widest of the trailing columns.
         Dropping it in the tablet band is what lets the other five fit without
         a sideways scroll; the phone card still shows it. */
      headerClassName: 'hide-tablet',
      cellClassName: 'hide-tablet',
      render: (device) => device.appVersion,
    },
    {
      key: 'actions',
      label: 'Actions',
      render: (device) => (
        <span className="flex flex-wrap items-center justify-end gap-1">
          <button
            type="button"
            onClick={() => openEdit(device)}
            aria-label={`Edit ${device.label}`}
            title="Edit device"
            className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-50 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <PencilIcon className="h-3.5 w-3.5" strokeWidth={1.9} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setDetailId(device.id)}
            aria-label={`Manage ${device.label}`}
            className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            Manage
          </button>
        </span>
      ),
    },
  ];

  const scope = filtered ? 'matching this filter' : 'in this workspace';

  return (
    <div className="flex flex-col gap-3">
      {/*
        The state of the fleet, before any list. Four tiles, all computed from the
        rows below them: how many there are, how many are actually reporting, how
        many want a person, and how many have never said anything at all.
      */}
      {rows.length > 0 && (
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {(
            [
              {
                label: 'Devices',
                value: String(rows.length),
                detail:
                  overview.bound === 0
                    ? 'None bound to a restaurant'
                    : overview.bound === rows.length
                      ? `across ${overview.restaurants} ${overview.restaurants === 1 ? 'restaurant' : 'restaurants'}`
                      : `${overview.bound} of ${rows.length} bound to a restaurant`,
                icon: MonitorIcon,
                tone: 'default' as StatTone,
              },
              {
                label: 'Reporting',
                value: String(overview.reporting),
                detail:
                  overview.reporting === rows.length
                    ? `Every device checked in within ${STALE_AFTER_HOURS}h`
                    : `Quiet for over ${STALE_AFTER_HOURS}h: ${rows.length - overview.reporting}`,
                icon: CheckCircle2Icon,
                tone: (overview.reporting === rows.length ? 'success' : 'warning') as StatTone,
              },
              {
                label: 'Needs attention',
                value: String(overview.attention.length),
                /*
                 * "On the list:" is not decoration. The tile beside this one
                 * counts every device that has never checked in; this one
                 * counts the reasons a device is *on the attention list*, and a
                 * revoked till that never reported is in the first set and not
                 * the second. Prefixing the line is what stops the two tiles
                 * looking like they disagree.
                 */
                detail:
                  overview.attention.length === 0
                    ? 'Nothing wants a person right now'
                    : `On the list: ${[
                        overview.quarantined ? `${overview.quarantined} paused` : '',
                        overview.neverSeenCount ? `${overview.neverSeenCount} never checked in` : '',
                        overview.quiet ? `${overview.quiet} gone quiet` : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}`,
                icon: AlertTriangleIcon,
                tone: (overview.attention.length === 0 ? 'success' : 'warning') as StatTone,
              },
              {
                label: 'Never checked in',
                value: String(overview.neverSeen),
                detail:
                  overview.neverSeen === 0
                    ? 'Every device has reported at least once'
                    : 'Enrolled, but no heartbeat yet',
                icon: ClockIcon,
                tone: (overview.neverSeen === 0 ? 'success' : 'warning') as StatTone,
              },
            ] as Array<{
              label: string;
              value: string;
              detail: string;
              icon: typeof MonitorIcon;
              tone: StatTone;
            }>
          ).map((kpi) => (
            <StatCard
              key={kpi.label}
              label={kpi.label}
              value={kpi.value}
              sub={kpi.detail}
              icon={kpi.icon}
              tone={kpi.tone}
            />
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <Panel
          title="Restaurant devices"
          icon={MonitorIcon}
          description={
            rows.length === 0
              ? 'The tills and screens reporting into DemandIQ.'
              : `${rows.length} ${rows.length === 1 ? 'device' : 'devices'}${
                  filtered ? ' matching this filter' : ' enrolled in this workspace'
                }.`
          }
          action={
            canManage ? (
              <button
                type="button"
                onClick={() => setEnrolling(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-1.5"
              >
                <PlusIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
                Enrol a device
              </button>
            ) : null
          }
        >
          {/* The two filters are the API's: the fleet endpoint is the one place
              "which devices can this seat see" is answered. They stay selects
              rather than toolbar chips for that reason — a chip would re-filter
              the page's own copy and drift from the server's answer. */}
          <div className="mb-3 flex flex-wrap items-end gap-2 border-b border-slate-100 pb-3">
            <label className="flex flex-col gap-1">
              <span className="text-micro font-semibold text-slate-600">Status</span>
              <select
                aria-label="Filter by status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                className={inputClass}
              >
                <option value="all">All devices</option>
                <option value="active">In service</option>
                <option value="quarantined">Quarantined</option>
                <option value="revoked">Revoked</option>
              </select>
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-micro font-semibold text-slate-600">Restaurant</span>
              <select
                aria-label="Filter by restaurant"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                className={inputClass}
              >
                <option value="all">All restaurants</option>
                {locations.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>

            {filtered && (
              <button
                type="button"
                onClick={() => {
                  setStatus('all');
                  setLocation('all');
                }}
                className="rounded-lg border border-slate-300 px-3 py-2.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-2"
              >
                Clear filters
              </button>
            )}

            {rows.length > 0 && (
              <span className="ml-auto pb-1 text-micro text-slate-500">
                {overview.reporting} of {rows.length} reporting
              </span>
            )}
          </div>

          {loading ? (
            <LoadingState label="Loading the device fleet" size="sm" />
          ) : error ? (
            <EmptyState
              variant={error.variant}
              size="sm"
              title={error.title}
              description={error.desc}
              action={{ label: 'Retry', onClick: () => void load() }}
            />
          ) : rows.length === 0 && filtered ? (
            <EmptyState
              variant="search"
              size="sm"
              title="No devices match this filter"
              description="Nothing in this workspace has that status at that restaurant. Clear the filter to see the whole fleet."
              action={{
                label: 'Show every device',
                onClick: () => {
                  setStatus('all');
                  setLocation('all');
                },
              }}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="No devices enrolled yet"
              description="Enrol the first till and it will start reporting sales and stock readings straight into DemandIQ. You will get a one-time key when you do. It is shown once and never again."
              action={
                canManage
                  ? { label: 'Enrol a device', onClick: () => setEnrolling(true) }
                  : undefined
              }
            />
          ) : (
            <RecordList<FleetDevice>
              stateKey="data-device-fleet"
              items={rows}
              columns={columns}
              rowKey={(device) => device.id || device.label}
              caption="Devices enrolled in this workspace"
              minWidth="min-w-[440px]"
              countNoun="devices"
              searchFields={(device) =>
                [device.label, device.type, nameFor(device.locationId), deviceStatusLabel(device.status)].join(' ')
              }
              searchPlaceholder="Search devices…"
              sortValue={(device, key) =>
                key === 'location'
                  ? nameFor(device.locationId)
                  : key === 'device'
                    ? device.label
                    : String((device as unknown as Record<string, unknown>)[key] ?? '')
              }
              defaultSort={{ key: 'device', dir: 'asc' }}
            />
          )}

          {!canManage && (
            <p className="mt-2.5 text-micro leading-relaxed text-slate-500">
              You can see every device in this workspace. Enrolling, rotating a key, quarantining and
              revoking are reserved for a seat holding devices:manage.
            </p>
          )}
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          {/* With no devices there is nothing to attend to, and a panel
              reassuring the reader about a fleet that does not exist is noise.
              The reference panel stays: it is what tells somebody about to
              enrol their first till what they are about to do. */}
          {rows.length > 0 && (
          <Panel
            title="Needs attention"
            icon={AlertTriangleIcon}
            description={
              overview.attention.length === 0
                ? `Every device ${scope} is checking in.`
                : `Devices that want a person, of the ${rows.length} ${scope}. A device that has been quiet for ${STALE_AFTER_HOURS} hours belongs here.`
            }
          >
            {overview.attention.length === 0 ? (
              <p className="flex items-start gap-2.5 text-meta leading-relaxed text-slate-600">
                <CheckCircle2Icon
                  className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                  strokeWidth={1.9}
                  aria-hidden="true"
                />
                Nothing is paused, nothing has gone quiet, and every enrolled device has reported in.
              </p>
            ) : (
              <ul className="flex flex-col divide-y divide-slate-100">
                {overview.attention.map((item) => (
                  <li
                    key={item.device.id || item.device.label}
                    className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-meta font-semibold text-slate-900">
                        {item.device.label}
                      </span>
                      <span className="block text-micro text-slate-500">
                        {nameFor(item.device.locationId)}
                      </span>
                      <span className="mt-0.5 block text-micro leading-relaxed text-slate-600">
                        {item.detail}
                      </span>
                    </span>
                    {/*
                      Named `Review …`, not `Manage …`: the table's own row button
                      already carries that name, and two controls in different
                      lists with identical names is ambiguous to a screen reader
                      as well as to a test.
                    */}
                    <button
                      type="button"
                      onClick={() => setDetailId(item.device.id)}
                      aria-label={`Review ${item.device.label}`}
                      className="shrink-0 rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      Review
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          )}

          <Panel
            title="What these devices are"
            icon={ShieldCheckIcon}
            description="Machines in a restaurant, not people signed into an account."
          >
            <ul className="flex flex-col gap-2.5">
              <li className="flex items-start gap-2.5">
                <UtensilsCrossedIcon
                  className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                  strokeWidth={1.8}
                  aria-hidden="true"
                />
                <span className="min-w-0">
                  <span className="block text-meta font-semibold text-slate-900">
                    A key, not a password
                  </span>
                  <span className="block text-micro leading-relaxed text-slate-500">
                    Each device holds a key that lets it report as itself and nothing else. It is shown
                    once when the device is enrolled and once more if you rotate it, and rotating stops
                    the old key working immediately, so the till on the counter needs the new one.
                  </span>
                </span>
              </li>
              <li className="flex items-start gap-2.5">
                <MonitorIcon
                  className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                  strokeWidth={1.8}
                  aria-hidden="true"
                />
                <span className="min-w-0">
                  <span className="block text-meta font-semibold text-slate-900">
                    Quarantine is a pause, revoke is a decision
                  </span>
                  <span className="block text-micro leading-relaxed text-slate-500">
                    Quarantine stops a device that is misbehaving and brings it back later. Revoke is for
                    a till that is lost, sold or stolen: it can never be used again and has to be replaced.
                    Either way the history stays on file.
                  </span>
                </span>
              </li>
            </ul>
          </Panel>
        </div>
      </div>

      {/*
        The shape of the fleet, four ways. Every panel reads the same rows the
        table does — the same objects, the same names — so a chart cannot
        disagree with the list above it.
      */}
      {rows.length > 0 && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Panel title="Fleet status" description="What state the devices on this list are in.">
            <MixDonut
              centreLabel="devices"
              slices={[
                {
                  key: 'active',
                  name: 'In service',
                  value: overview.mix.active,
                  fill: '#0f6d67',
                  dot: 'bg-brand-600',
                },
                {
                  key: 'quarantined',
                  name: 'Quarantined',
                  value: overview.mix.quarantined,
                  fill: '#d97706',
                  dot: 'bg-amber-500',
                },
                {
                  key: 'revoked',
                  name: 'Revoked',
                  value: overview.mix.revoked,
                  fill: '#dc2626',
                  dot: 'bg-red-500',
                },
              ]}
              emptyTitle="No devices to break down"
              emptyDescription="This workspace has no devices yet, so there is no mix to show."
            />
          </Panel>

          <Panel
            title="When devices last checked in"
            description="How long since each device on this list said it was still there."
          >
            {/* Horizontal: the five age labels (`1–24 hours`, `1–7 days`) do
                not fit across a phone's upright x-axis — at 320px the two middle
                ones were measured overlapping by 1px. As rows they also read as
                the ladder they are, and 5 rows stay inside the 210px minimum. */}
            <CountChart
              data={overview.buckets}
              layout="bars"
              noun="devices"
              categoryLabel="Time since last seen"
              emptyTitle="No devices to show"
              emptyDescription="This workspace has no devices yet, so there is nothing to group."
            />
          </Panel>

          <Panel
            title="Devices by restaurant"
            description="Where the hardware is. A device bound to no restaurant feeds no site's forecast."
          >
            <CountChart
              data={overview.byLocation}
              layout="bars"
              noun="devices"
              categoryLabel="Restaurant"
              emptyTitle="No devices to show"
              emptyDescription="This workspace has no devices yet, so no restaurant carries any."
            />
          </Panel>

          <Panel
            title="App versions in the fleet"
            description={
              overview.versions.length === 1
                ? `Every device is on ${overview.versions[0].label}.`
                : `${overview.versions.length} different versions are in service, so some tills are behind.`
            }
          >
            <CountChart
              data={overview.versions}
              noun="devices"
              categoryLabel="Build version"
              emptyTitle="No versions to show"
              emptyDescription="This workspace has no devices yet, so no build is in service."
            />
          </Panel>
        </div>
      )}

      <EnrollDeviceModal
        open={enrolling}
        locations={locations}
        onClose={() => setEnrolling(false)}
        onEnrolled={(issued) => {
          setSecret(issued);
          void load();
          toast(`${issued.deviceLabel} is enrolled. Copy its key now. It is shown once.`, 'info');
        }}
      />

      {detail && (
        <DeviceDetailModal
          device={detail}
          canManage={canManage}
          canManageGrants={canManageGrants}
          onClose={() => setDetailId(null)}
          onChanged={() => {
            void load();
          }}
          onSecret={(issued) => setSecret(issued)}
        />
      )}

      <SecretModal secret={secret} onClose={() => setSecret(null)} onRotated={() => void load()} />

      {/* Edit device: the two facts `PATCH /devices/{id}` actually changes,
          what the staff call the machine and the restaurant it reports into.
          Keys, scopes and status have their own deliberate flows (enrol/rotate,
          the manage dialog, quarantine/revoke) and are not smuggled in here. */}
      <FormModal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`Edit ${editing?.label ?? 'device'}`}
        description="Rename the machine or move it to another restaurant. The key and its permissions do not change."
        onSubmit={() => void saveEdit()}
        saving={saving}
        submitLabel="Save changes"
      >
        <Field id="edit-device-label" label="What the staff will know it as">
          <input
            id="edit-device-label"
            type="text"
            value={editDraft.label}
            onChange={(event) => setEditDraft({ ...editDraft, label: event.target.value })}
            placeholder="Till 2"
            className={inputClass}
          />
        </Field>
        <Field id="edit-device-location" label="Restaurant" hint="Where this machine reports sales and stock readings from.">
          <select
            id="edit-device-location"
            value={editDraft.locationId}
            onChange={(event) => setEditDraft({ ...editDraft, locationId: event.target.value })}
            className={inputClass}
          >
            <option value="">Not bound to a restaurant</option>
            {locations.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
      </FormModal>
    </div>
  );
}
