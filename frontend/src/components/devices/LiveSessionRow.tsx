import { useState } from 'react';
import { EyeIcon, StopCircleIcon } from 'lucide-react';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { absoluteTime } from '../../lib/deviceFleet';
import { countdownLabel, mirrorStatusLabel, type MirrorRequest } from '../../lib/mirrorApprovals';

/**
 * A view of somebody's screen, and the clock that ends it.
 *
 * The countdown is shown against the session's own `ends_at` — the hard expiry
 * the server sets at approval and caps at thirty minutes — rather than against
 * anything the browser believes. It is the only thing that stops the view, so
 * an operator watching it is watching the thing that matters, and a view whose
 * window has closed says "Window closed" rather than continuing to claim it is
 * live.
 *
 * `now` is passed in rather than read from the clock here, so one tick on the
 * page moves every countdown together and a paused tab cannot leave three
 * sessions claiming different amounts of time.
 */
export function LiveSessionRow({
  request,
  now,
  busy,
  onEnd
}: {
  request: MirrorRequest;
  now: number;
  busy: boolean;
  onEnd: (request: MirrorRequest) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const endsAtMs = request.endsAt ? new Date(request.endsAt).getTime() : Number.NaN;
  const msLeft = Number.isNaN(endsAtMs) ? 0 : endsAtMs - now;
  const live = request.live && msLeft > 0;
  const urgent = live && msLeft <= 5 * 60_000;

  return (
    <>
      <li className="flex flex-col gap-2 rounded-xl border border-red-200 bg-red-50/40 px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <EyeIcon className="h-4 w-4 shrink-0 text-red-600" strokeWidth={1.8} aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body-xs font-semibold text-slate-900">
              {request.deviceLabel}
            </span>
            <span className="block text-micro text-slate-500">
              Opened by {request.requestedByLabel}
              {request.decidedByLabel ? ` · agreed by ${request.decidedByLabel}` : ''}
            </span>
          </span>
          <span
            className={[
              'shrink-0 rounded-full px-2.5 py-1 text-micro font-semibold',
              live ? (urgent ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-700') : 'bg-slate-100 text-slate-600',
            ].join(' ')}
          >
            {mirrorStatusLabel(request.status, live)}
          </span>
        </div>

        <p className="text-meta leading-relaxed text-slate-700">“{request.reason}”</p>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="text-caption font-semibold text-slate-800">
            {live ? countdownLabel(request.endsAt, now) : 'The viewing window has closed'}
          </span>
          <span className="text-micro text-slate-500">
            {request.endsAt ? `Ends at ${absoluteTime(request.endsAt)}` : 'No end time was set'}
          </span>
          {live && (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={busy}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
            >
              <StopCircleIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
              {busy ? 'Ending…' : 'End this view'}
            </button>
          )}
        </div>
      </li>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => onEnd(request)}
        title="End this view now?"
        body={`Nobody will be able to see ${request.deviceLabel}’s screen after this. The session stays on file as a record that it happened, and the requester can raise a new one if the problem is still there.`}
        confirmLabel="End it"
      />
    </>
  );
}
