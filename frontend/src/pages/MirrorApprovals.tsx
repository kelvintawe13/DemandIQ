import { useCallback, useEffect, useState } from 'react';
import { EyeIcon, EyeOffIcon, MonitorIcon, ShieldCheckIcon, UsersIcon } from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { EmptyState } from '../components/ui/EmptyState';
import { LoadingState } from '../components/ui/LoadingState';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { api, ApiException } from '../api/client';
import {
  absoluteTime,
  deviceLabels,
  mapDevices,
  type FleetDevice,
} from '../lib/deviceFleet';
import {
  liveSessions,
  mapConsentOutcome,
  mapEligibility,
  mapMirrorRequests,
  refusalMessage,
  type ConsentOutcome,
  type MirrorEligibility,
  type MirrorRequest,
} from '../lib/mirrorApprovals';
import { MirrorQueueCard } from '../components/devices/MirrorQueueCard';
import { LiveSessionRow } from '../components/devices/LiveSessionRow';
import { MirrorConsentPanel } from '../components/devices/MirrorConsentPanel';
import { RequestMirrorModal } from '../components/devices/RequestMirrorModal';
import { actionErrorMessage, loadErrorFrom, type LoadError } from '../components/devices/errors';

/**
 * The two-person queue for watching a staff member's screen.
 *
 * This is not a debug tool with a consent checkbox on it. Mirroring shows
 * whatever is on a till or a kitchen screen while a real person is working
 * during a service, so four things have to be true, and all four are said out
 * loud here rather than left to a help page:
 *
 * 1. the customer agreed, in writing, for that device;
 * 2. two different people are involved: the one who asked can never be the one
 *    who agreed, which is a database constraint rather than a UI convention;
 * 3. deciding needs `device_mirror:approve`, which the administrator role
 *    deliberately does not hold, so the usual support path cannot approve the
 *    request it just made;
 * 4. the view ends by itself, at a time the approver chose, capped at thirty
 *    minutes.
 *
 * Every control here is gated on what the server said this seat may do
 * (`approvable` per request, `can_request` per device), so the queue never
 * offers a button whose only possible answer is a refusal.
 */
export function MirrorApprovals() {
  const { user } = useSession();
  const { toast } = useToast();
  const permissions = user.permissions ?? [];
  const canRecordConsent = permissions.includes('devices:manage');
  const canDecide = permissions.includes('device_mirror:approve');

  // `devices === null` *is* the loading state: the first read owns the page, and
  // every later reload re-reads into state the page already has, so approving a
  // request does not blank the queue for a moment.
  const [devices, setDevices] = useState<FleetDevice[] | null>(null);
  const [pending, setPending] = useState<MirrorRequest[]>([]);
  const [live, setLive] = useState<MirrorRequest[]>([]);
  const [eligibility, setEligibility] = useState<Record<string, MirrorEligibility | undefined>>({});
  const [error, setError] = useState<LoadError | null>(null);
  const [queueRefusal, setQueueRefusal] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<LoadError | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [requestDevice, setRequestDevice] = useState<FleetDevice | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    setError(null);
    setQueueError(null);
    setDetailsLoading(true);
    try {
      const devicePayload = await api.get('/api/v1/devices');
      const rows = mapDevices(devicePayload);
      setDevices(rows);

      const labels = deviceLabels(rows);
      const userId = user.id ?? null;
      const moment = Date.now();

      // Names, when this seat may read the roster. `GET /members` needs
      // members:manage, which an administrator does not hold, so the roster is
      // best-effort: a seat that cannot read it gets "another workspace member"
      // rather than a page that refuses to load over a cosmetic field.
      let people: Record<string, string> = {};
      try {
        const members = await api.get<{ data?: Array<{ id?: unknown; name?: unknown }> }>(
          '/api/v1/members',
        );
        for (const member of Array.isArray(members.data) ? members.data : []) {
          if (typeof member.id === 'string' && typeof member.name === 'string') {
            people[member.id] = member.name;
          }
        }
      } catch {
        people = {};
      }

      // Live views are published per device, so a workspace-wide list of them is
      // one read per device. It is the price of the API not offering a "live
      // right now" route, and eligibility is needed on the same devices for the
      // consent panel, so the two are read together rather than twice.
      const details = await Promise.all(
        rows.map(async (device) => {
          const [state, sessions] = await Promise.allSettled([
            api.get(`/api/v1/device-mirror/devices/${device.id}/mirror-eligibility`),
            api.get(`/api/v1/device-mirror/devices/${device.id}/mirror-requests`, { limit: 50 }),
          ]);
          return { device, state, sessions };
        }),
      );

      const nextEligibility: Record<string, MirrorEligibility | undefined> = {};
      const nextLive: MirrorRequest[] = [];
      for (const { device, state, sessions } of details) {
        nextEligibility[device.id] =
          state.status === 'fulfilled' ? mapEligibility(state.value) : undefined;
        if (sessions.status === 'fulfilled') {
          nextLive.push(
            ...liveSessions(
              mapMirrorRequests(sessions.value, { labels, people, userId, now: moment }),
            ),
          );
        }
      }
      setEligibility(nextEligibility);
      setLive(nextLive);

      // The queue is gated on device_mirror:approve. A seat without it gets the
      // explanation instead of a red error, because that refusal *is* the
      // design: asking and agreeing are deliberately different permissions.
      try {
        const queue = await api.get('/api/v1/device-mirror/mirror-requests/pending');
        setPending(mapMirrorRequests(queue, { labels, people, userId, now: moment }));
        setQueueRefusal(null);
      } catch (e) {
        setPending([]);
        if (e instanceof ApiException && e.status === 403) {
          setQueueRefusal(
            'Deciding whether a staff member’s screen can be watched needs device_mirror:approve. That permission is deliberately not given to the administrator role (it belongs to the owner), so this seat can ask for a view but cannot agree to one. The live views and consent below still work.',
          );
        } else {
          setQueueError(loadErrorFrom(e, 'Could not read the approval queue'));
        }
      }
    } catch (e) {
      setDevices([]);
      setError(loadErrorFrom(e, 'Could not load the device fleet'));
    } finally {
      setDetailsLoading(false);
    }
  }, [user.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // One clock for every countdown on the page, and only while something is
  // actually live: a closed window is not a view, and should not keep a timer
  // running behind an empty list.
  useEffect(() => {
    if (live.length === 0) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live.length]);

  const decide = async (id: string, path: 'approve' | 'deny' | 'end', body?: Record<string, number>) => {
    setBusyId(id);
    try {
      const res = await api.post<{ data?: { ends_at?: unknown } }>(
        `/api/v1/device-mirror/mirror-requests/${id}/${path}`,
        body,
      );
      const endsAt = typeof res?.data?.ends_at === 'string' ? res.data.ends_at : null;
      if (path === 'approve') {
        toast(
          endsAt
            ? `The view is open, and ends by itself at ${absoluteTime(endsAt)}. Nobody has to remember to stop it.`
            : 'The view is open, and ends by itself at the time the approver chose.',
          'info',
        );
      } else if (path === 'deny') {
        toast('Refused. The requester has been told why.', 'info');
      } else {
        toast('The view has ended. It stays on file as a record that it happened.', 'info');
      }
      setNow(Date.now());
      await load();
    } catch (e) {
      toast(
        e instanceof ApiException
          ? refusalMessage(e.error.code, e.error.message)
          : actionErrorMessage(e, 'That decision could not be recorded.'),
        'error',
      );
    } finally {
      setBusyId(null);
    }
  };

  const recordConsent = async (device: FleetDevice, granted: boolean) => {
    setBusyId(device.id);
    try {
      const outcome: ConsentOutcome = mapConsentOutcome(
        await api.post(`/api/v1/device-mirror/devices/${device.id}/mirror-consent`, { granted }),
      );
      if (!granted) {
        toast(
          outcome.sessionsEnded > 0
            ? `Consent withdrawn. ${outcome.sessionsEnded} live or queued ${
                outcome.sessionsEnded === 1 ? 'session was' : 'sessions were'
              } ended. Nobody is watching that screen now.`
            : 'Consent withdrawn. Nothing was being watched on that device.',
          'info',
        );
      } else {
        toast(
          `The customer’s consent is recorded. A request can now be raised for ${device.label}.`,
          'info',
        );
      }
      setNow(Date.now());
      await load();
    } catch (e) {
      toast(
        e instanceof ApiException
          ? refusalMessage(e.error.code, e.error.message)
          : actionErrorMessage(e, 'That could not be recorded.'),
        'error',
      );
    } finally {
      setBusyId(null);
    }
  };

  if (devices === null && !error) {
    return <LoadingState label="Loading the screen-mirroring approval queue" />;
  }

  if (error) {
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.desc}
        action={{ label: 'Retry', onClick: () => void load() }}
      />
    );
  }

  const rows = devices ?? [];
  const deviceById = new Map(rows.map((device) => [device.id, device]));

  return (
    <div className="flex flex-col gap-3">
      <Panel
        title="Before anybody can see a staff screen"
        icon={ShieldCheckIcon}
        description="Four gates, every one of them enforced on the server rather than on this page."
      >
        <ul className="flex flex-col gap-2.5">
          {[
            [
              'The customer has agreed',
              'Consent is recorded per device, in writing, and shows the date it was given. Without it no request can even be raised.',
            ],
            [
              'Two different people',
              'Whoever asks can never be the person who agrees. That rule is a database constraint, so it holds however the request arrived.',
            ],
            [
              'Two different permissions',
              'Asking needs device_mirror:request; agreeing needs device_mirror:approve. The administrator role holds the first and not the second, so a support request cannot approve its own.',
            ],
            [
              'It ends by itself',
              'Approving sets a time limit the approver chooses, capped at thirty minutes. Either of the two people can end it earlier.',
            ],
          ].map(([heading, body]) => (
            <li key={heading} className="flex items-start gap-2.5">
              <ShieldCheckIcon
                className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                strokeWidth={1.8}
                aria-hidden="true"
              />
              <span className="min-w-0">
                <span className="block text-meta font-semibold text-slate-900">{heading}</span>
                <span className="block text-micro leading-relaxed text-slate-500">{body}</span>
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Waiting for a second person"
        icon={UsersIcon}
        description="A request views nothing on its own. It waits here until somebody who did not ask for it says yes."
      >
        {queueRefusal ? (
          <EmptyState
            variant="error"
            size="sm"
            title="This seat cannot decide screen-mirroring requests"
            description={queueRefusal}
          />
        ) : detailsLoading ? (
          <LoadingState label="Loading requests waiting for approval" size="sm" />
        ) : queueError ? (
          <EmptyState
            variant={queueError.variant}
            size="sm"
            title={queueError.title}
            description={queueError.desc}
            action={{ label: 'Retry', onClick: () => void load() }}
          />
        ) : pending.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="Nobody is waiting for a second person"
            description="When somebody needs to look at a till’s screen mid-shift, their request waits here until a colleague agrees to it."
          />
        ) : (
          <ul aria-label="Requests waiting for approval" className="flex flex-col gap-2">
            {pending.map((request) => (
              <MirrorQueueCard
                key={request.id}
                request={request}
                device={deviceById.get(request.deviceId) ?? null}
                busy={busyId === request.id}
                canApprove={canDecide}
                onApprove={(row, minutes) =>
                  void decide(row.id, 'approve', { duration_minutes: minutes })
                }
                onDeny={(row) => void decide(row.id, 'deny')}
              />
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Being watched right now"
        icon={live.length > 0 ? EyeIcon : EyeOffIcon}
        description="Live views of a staff member’s screen, each counting down to the moment it ends by itself."
      >
        {detailsLoading ? (
          <LoadingState label="Checking for live views" size="sm" />
        ) : live.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="No staff screen is being viewed"
            description="Nobody in this workspace can see a device screen at the moment. A view appears here the moment it is approved, with the time it will end."
          />
        ) : (
          <ul aria-label="Live views" className="flex flex-col gap-2">
            {live.map((session) => (
              <LiveSessionRow
                key={session.id}
                request={session}
                now={now}
                busy={busyId === session.id}
                onEnd={(row) => void decide(row.id, 'end')}
              />
            ))}
          </ul>
        )}
      </Panel>

      <MirrorConsentPanel
        devices={rows}
        eligibility={eligibility}
        canRecordConsent={canRecordConsent}
        busyId={busyId}
        onRecordConsent={(device) => void recordConsent(device, true)}
        onWithdrawConsent={(device) => void recordConsent(device, false)}
        onRequest={(device) => setRequestDevice(device)}
      />

      <RequestMirrorModal
        open={requestDevice !== null}
        device={requestDevice}
        onClose={() => setRequestDevice(null)}
        onRequested={() => void load()}
      />

      <Panel
        title="Where the record of a view lives"
        icon={MonitorIcon}
        description="Not only on this page. The fleet console keeps every request against the device it was for."
      >
        <p className="text-micro leading-relaxed text-slate-500">
          Every request, decision and withdrawal is written to the audit log with both people named,
          and a device&rsquo;s own mirroring history is on its detail screen in the fleet console,
          readable by anyone who can read the fleet, not only by the person who asked. A view that
          ended by itself stays on file afterwards with the time it stopped, so &ldquo;nobody watched
          that screen&rdquo; is a claim an operator can actually answer.
        </p>
      </Panel>
    </div>
  );
}
