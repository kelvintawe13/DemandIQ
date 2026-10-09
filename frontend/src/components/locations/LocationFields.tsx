import type { ReactNode } from 'react';
import { ClockIcon } from 'lucide-react';
import { Field, inputClass } from '../ui/Field';
import {
  DAYS,
  DAY_NAMES,
  NEEDED_HISTORY_DAYS,
  openDayNames,
  type LocationForm,
} from '../../lib/locations';

/**
 * One section of the location form.
 *
 * Eleven fields in one flat grid gave the eye no landing place and said nothing
 * about which of them belong together. A form this long scans by group: the
 * eyebrow names the group, the rule separates it from the one above.
 */
function FormSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="border-t border-slate-100 pt-3.5 first:border-t-0 first:pt-0">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-overline font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
        {hint && <p className="text-fine text-slate-500">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

/**
 * The five groups of fields, in the order a person fills them in.
 *
 * Shared verbatim by the add route and the edit dialog so the two can never ask
 * for different things — the only difference between them is what happens on
 * submit, which is the page's business, not the fields'.
 */
export function LocationFields({
  draft,
  setDraft,
  toggleDay,
  nameError,
  autoFocusName = false,
}: {
  draft: LocationForm;
  setDraft: (value: LocationForm) => void;
  toggleDay: (index: number) => void;
  /** Shown under the name field once a submit has been attempted. */
  nameError?: string;
  autoFocusName?: boolean;
}) {
  const openDays = openDayNames(draft.open_days_mask);

  return (
    <div className="flex flex-col gap-4">
      <FormSection title="Identity" hint="What this site is called in the app.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field
            id="loc-name"
            label="Location name *"
            hint="Shown in every report and forecast."
            error={nameError}>
            <input
              id="loc-name"
              type="text"
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              placeholder="Remera Grill"
              className={`${inputClass} ${nameError ? 'border-red-400 focus:border-red-500 focus:ring-red-500/20' : ''}`}
              autoFocus={autoFocusName}
            />
          </Field>
          <Field id="loc-district" label="District">
            <select
              id="loc-district"
              value={draft.district}
              onChange={(event) => setDraft({ ...draft, district: event.target.value })}
              className={inputClass}>
              {['Gasabo', 'Kicukiro', 'Nyarugenge', 'Musanze', 'Rubavu'].map((district) => (
                <option key={district} value={district}>
                  {district}
                </option>
              ))}
            </select>
          </Field>
          <Field id="loc-area" label="Area" hint="The neighbourhood or building.">
            <input
              id="loc-area"
              type="text"
              value={draft.area}
              onChange={(event) => setDraft({ ...draft, area: event.target.value })}
              placeholder="Kigali Heights"
              className={inputClass}
            />
          </Field>
          <Field id="loc-capacity" label="Seating capacity" hint="Used to sense-check covers per seat.">
            <input
              id="loc-capacity"
              type="number"
              min={1}
              value={draft.seating_capacity}
              onChange={(event) => setDraft({ ...draft, seating_capacity: event.target.value })}
              placeholder="80"
              className={inputClass}
            />
          </Field>
        </div>
      </FormSection>

      <FormSection title="Trading" hint={`Forecasting needs ${NEEDED_HISTORY_DAYS} days of history.`}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field id="loc-service" label="Service type">
            <select
              id="loc-service"
              value={draft.service_type}
              onChange={(event) => setDraft({ ...draft, service_type: event.target.value })}
              className={inputClass}>
              <option value="buffet">Buffet</option>
              <option value="canteen">Canteen</option>
              <option value="a_la_carte">À la carte</option>
              <option value="cafe">Café</option>
              <option value="bar">Bar</option>
            </select>
          </Field>
          <Field id="loc-cuisine" label="Cuisine">
            <select
              id="loc-cuisine"
              value={draft.cuisine}
              onChange={(event) => setDraft({ ...draft, cuisine: event.target.value })}
              className={inputClass}>
              {['Buffet', 'Canteen', 'À la carte', 'Café', 'Pan-African', 'Continental'].map((cuisine) => (
                <option key={cuisine} value={cuisine}>
                  {cuisine}
                </option>
              ))}
            </select>
          </Field>
          <Field
            id="loc-opened"
            label="Opened on"
            hint={draft.opened_on ? 'The history clock starts from this date.' : undefined}>
            <input
              id="loc-opened"
              type="date"
              value={draft.opened_on}
              onChange={(event) => setDraft({ ...draft, opened_on: event.target.value })}
              className={inputClass}
            />
          </Field>
        </div>
        <div className="mt-3">
          <p className="text-body-xs font-semibold text-slate-800">Open days</p>
          {/* Open/closed was conveyed by colour alone; `aria-pressed` states it. */}
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {DAYS.map((day, index) => {
              const on = draft.open_days_mask.padEnd(7, '0')[index] === '1';
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => toggleDay(index)}
                  aria-pressed={on}
                  aria-label={`${DAY_NAMES[index]}: ${on ? 'open' : 'closed'}`}
                  className={[
                    'rounded-full px-3 py-1.5 text-caption font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    on ? 'bg-brand-700 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200',
                  ].join(' ')}>
                  {day}
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 text-fine text-slate-500">
            The stored mask is{' '}
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-fine text-slate-700">
              {draft.open_days_mask || '1111111'}
            </code>{' '}
            — which weekdays the kitchen trades.
          </p>
        </div>
      </FormSection>

      <FormSection title="Contact" hint="Who to call about this site's data.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field id="loc-manager" label="Manager">
            <input
              id="loc-manager"
              type="text"
              value={draft.manager_name}
              onChange={(event) => setDraft({ ...draft, manager_name: event.target.value })}
              placeholder="Aline Umutoni"
              className={inputClass}
            />
          </Field>
          <Field id="loc-phone" label="Phone">
            <input
              id="loc-phone"
              type="tel"
              value={draft.phone}
              onChange={(event) => setDraft({ ...draft, phone: event.target.value })}
              placeholder="+250 788 000 000"
              className={inputClass}
            />
          </Field>
          <Field id="loc-address" label="Address" className="sm:col-span-2">
            <input
              id="loc-address"
              type="text"
              value={draft.address}
              onChange={(event) => setDraft({ ...draft, address: event.target.value })}
              placeholder="KG 123 St, Kigali"
              className={inputClass}
            />
          </Field>
        </div>
      </FormSection>

      <FormSection title="Notes" hint="Anything the next person to look at this site should know.">
        <Field id="loc-notes" label="Notes">
          <textarea
            id="loc-notes"
            rows={2}
            value={draft.notes}
            onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
            placeholder="Any operational notes…"
            className={inputClass}
          />
        </Field>
      </FormSection>

      {/* The trading pattern in words, where the pills are: the mask above is a
          string of bits, and the reader should not have to decode it. */}
      <p className="flex items-center gap-1.5 border-t border-slate-100 pt-3 text-fine text-slate-500">
        <ClockIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
        {openDays.length === 7
          ? 'Trading every day'
          : openDays.length === 0
            ? 'Closed every day'
            : `Closed ${DAY_NAMES.filter((day) => !openDays.includes(day)).join(', ')}`}
      </p>
    </div>
  );
}
