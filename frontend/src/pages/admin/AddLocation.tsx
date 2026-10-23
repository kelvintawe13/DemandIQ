import { useState } from 'react';
import { ArrowLeftIcon, CheckCircle2Icon, ClockIcon, MapPinIcon, UsersIcon } from 'lucide-react';
import { useSession, type SignupProfile } from '../../contexts/SessionContext';
import { Panel } from '../../components/ui/Panel';
import { LocationFields } from '../../components/locations/LocationFields';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import {
  DAY_NAMES,
  NEEDED_HISTORY_DAYS,
  emptyLocationForm,
  locationPayload,
  openDayNames,
  toggleOpenDay,
  type LocationForm,
} from '../../lib/locations';

/**
 * Adding a location is a page, not a dialog.
 *
 * It was the same `FormModal` that editing uses. Two reasons it moved out:
 *
 * * **It is the first thing a new workspace does**, and a task that long sits
 *   better at its own address than behind a modal — the URL survives a reload,
 *   the browser's back button means "leave the form", and the sidebar keeps its
 *   place instead of the app being covered by an overlay.
 * * **It wants room for context.** The form can afford to say what happens when
 *   you save — the 7-day bar, that ingest starts now — and to show the record
 *   being built beside it as you fill it in.
 *
 * Editing a row that already exists stays a dialog: that is a correction, not a
 * destination.
 */
export function AddLocation({ onNavigate }: { onNavigate?: (id: string) => void }) {
  const { user } = useSession();
  const [draft, setDraft] = useState<LocationForm>(() => {
    // Prefill from what the organisation answered at signup (persisted on
    // the workspace since migration 019): the business type hints cuisine
    // and service style, the promised daily covers seed seating capacity.
    const signup = (user as { signupProfile?: SignupProfile })?.signupProfile;
    if (!signup) return emptyLocationForm;
    const bt = signup.businessType;
    return {
      ...emptyLocationForm,
      cuisine:
        bt === 'bakery'
          ? 'Café'
          : bt === 'cafeteria' || bt === 'catering'
            ? 'Canteen'
            : emptyLocationForm.cuisine,
      service_type:
        bt === 'bakery'
          ? 'cafe'
          : bt === 'cafeteria' || bt === 'catering'
            ? 'canteen'
            : emptyLocationForm.service_type,
      seating_capacity: signup.dailyCovers ? String(signup.dailyCovers) : '',
    };
  });
  const [saving, setSaving] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const { toast } = useToast();

  const leave = () => onNavigate?.('admin-locations');
  const nameMissing = attempted && !draft.name.trim();

  const save = async () => {
    setAttempted(true);
    if (!draft.name.trim()) return;
    setSaving(true);
    try {
      await api.post('/api/v1/locations', locationPayload(draft));
      toast(`Location added. Forecasting from ${NEEDED_HISTORY_DAYS} days of history.`, 'success');
      leave();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to create location', 'error');
    } finally {
      setSaving(false);
    }
  };

  const openDays = openDayNames(draft.open_days_mask);
  const summary: Array<[string, string]> = [
    ['District', draft.district || '—'],
    ['Service type', draft.service_type ? draft.service_type.replace(/_/g, ' ') : '—'],
    ['Seating', draft.seating_capacity ? `${draft.seating_capacity} seats` : 'Not set'],
    ['Open days', openDays.length === 7 ? 'Every day' : openDays.join(', ') || 'None'],
    ['Manager', draft.manager_name || 'Not set'],
    ['Phone', draft.phone || 'Not set'],
  ];

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={leave}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-caption font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          <ArrowLeftIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.2} />
          Back to locations
        </button>
        <p className="text-fine text-slate-500">Nothing is saved until you submit this form.</p>
      </div>

      <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel
          title="Site details"
          icon={MapPinIcon}
          description={`A new site ingests immediately and forecasts from ${NEEDED_HISTORY_DAYS} days of history.`}>
          <LocationFields
            draft={draft}
            setDraft={setDraft}
            toggleDay={(index) => setDraft({ ...draft, open_days_mask: toggleOpenDay(draft.open_days_mask, index) })}
            nameError={nameMissing ? 'Give the location a name — it is how every report refers to it.' : undefined}
            autoFocusName
          />

          {/* The action bar lives with the form it submits: one `<form>`, so
              Enter saves from any field and the footer cannot drift from it. */}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3.5">
            <p className="text-fine text-slate-500">
              {draft.name.trim() ? `Adding “${draft.name.trim()}”` : 'Name the site to continue'}
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={leave}
                className="rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-body-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-2">
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors hover:bg-brand-800 disabled:cursor-not-allowed disabled:bg-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-2">
                <CheckCircle2Icon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.2} />
                {saving ? 'Adding…' : 'Add location'}
              </button>
            </div>
          </div>
        </Panel>

        <div className="flex flex-col gap-3">
          {/* What the reader is about to create, as they create it — the one
              thing a modal could not show them without hiding the form. */}
          <Panel title="The site you are adding" icon={UsersIcon} description="Updates as you fill the form in.">
            <p className="truncate text-h5 font-semibold text-slate-900" title={draft.name || undefined}>
              {draft.name.trim() || 'Untitled site'}
            </p>
            <p className="mt-1 text-caption text-slate-500">
              {[draft.district, draft.service_type ? draft.service_type.replace(/_/g, ' ') : '', draft.area]
                .filter(Boolean)
                .join(' · ') || 'No district or service type yet'}
            </p>
            <dl className="mt-3 flex flex-col divide-y divide-slate-100 border-t border-slate-100 pt-1">
              {summary.map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between gap-3 py-1.5">
                  <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="min-w-0 truncate text-caption text-slate-800" title={value}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </Panel>

          <Panel
            title="What happens on save"
            icon={ClockIcon}
            description="The onboarding rule this site will follow.">
            <ol className="flex flex-col gap-2.5">
              {[
                { day: 'Day 0', text: 'The site is on the register and ingest can start straight away.' },
                {
                  day: `Day ${NEEDED_HISTORY_DAYS}`,
                  text: 'Forecasting switches on for its own series, once lag_7 has something to read.',
                },
                {
                  day: 'Any time',
                  text: 'Fill gaps from the Manual Form or a CSV for tighter intervals.',
                },
              ].map((step) => (
                <li key={step.day} className="flex items-baseline gap-2.5 text-caption">
                  <span className="w-14 shrink-0 text-fine font-semibold uppercase tracking-wide text-slate-500">
                    {step.day}
                  </span>
                  <span className="min-w-0 text-slate-600">{step.text}</span>
                </li>
              ))}
            </ol>
            <p className="mt-3 border-t border-slate-100 pt-3 text-fine text-slate-500">
              {openDays.length === 0
                ? 'Every day is currently marked closed — the forecast will treat the site as shut.'
                : openDays.length === 7
                  ? 'Open every day, so no weekday is excluded from the series.'
                  : `Closed ${DAY_NAMES.filter((day) => !openDays.includes(day)).join(', ')} — those days are excluded from the series.`}
            </p>
          </Panel>
        </div>
      </div>
    </form>
  );
}
