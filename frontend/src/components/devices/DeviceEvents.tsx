import { useCallback, useEffect, useState } from 'react';
import { ActivityIcon } from 'lucide-react';
import { Panel } from '../ui/Panel';
import { EmptyState } from '../ui/EmptyState';
import { LoadingState } from '../ui/LoadingState';
import { api } from '../../api/client';
import { eventTypeLabel, mapDeviceEvents, type DeviceEventRow } from '../../lib/deviceFleet';
import { loadErrorFrom, type LoadError } from './errors';

/**
 * The device's own record of what it has been doing.
 *
 * A heartbeat is a liveness signal, not an event log, and the list reads that
 * way: the most recent rows first, the telemetry as one readable line, and an
 * empty device called out as *never having checked in* rather than left blank —
 * which is the difference between a brand-new till on a bench and one that has
 * been dropped off the network for a week.
 */
export function DeviceEvents({ deviceId }: { deviceId: string }) {
  const [events, setEvents] = useState<DeviceEventRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await api.get(`/api/v1/devices/${deviceId}/events`, { limit: 50 });
      setEvents(mapDeviceEvents(payload));
    } catch (e) {
      setEvents(null);
      setError(loadErrorFrom(e, 'Could not read this device’s activity'));
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Panel
      title="Recent activity"
      icon={ActivityIcon}
      description="The last 50 things this device reported, newest first."
    >
      {loading ? (
        <LoadingState label="Loading recent activity" size="sm" />
      ) : error ? (
        <EmptyState
          variant={error.variant}
          size="sm"
          title={error.title}
          description={error.desc}
          action={{ label: 'Retry', onClick: () => void load() }}
        />
      ) : !events || events.length === 0 ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="This device has never checked in"
          description="Nothing has been reported from it yet. A device that has just been enrolled stays like this until it is set up on the counter and sends its first heartbeat."
        />
      ) : (
        <ul aria-label="Device activity" className="flex flex-col gap-1.5">
          {events.map((event) => (
            <li
              key={event.id}
              className="flex flex-wrap items-baseline gap-x-2 rounded-lg border border-slate-100 px-3 py-1.5"
            >
              <span className="text-meta font-semibold text-slate-800">
                {eventTypeLabel(event.eventType)}
              </span>
              <span className="text-micro text-slate-500">{event.recordedAt}</span>
              <span className="min-w-0 flex-1 basis-full text-micro leading-relaxed text-slate-500">
                {event.summary}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
