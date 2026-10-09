/**
 * Everything about a location that more than one screen needs to agree on.
 *
 * The registry list, the add route and the edit dialog all read the same
 * payload, so the rules for reading it live here rather than being re-derived
 * per component — which is how a page ends up saying "forecasting" in one place
 * and "not scored" in another.
 */

/**
 * The history a site needs before its forecast means anything. `lag_7` and
 * `roll_mean_7` are the model's own features, so under seven days the model has
 * nothing to look back at — the number is a modelling fact, not a preference.
 */
export const NEEDED_HISTORY_DAYS = 7;

/** Short names for the weekday pills and the list. */
export const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

/** Long names, for the accessible label and prose. */
export const DAY_NAMES = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const;

export type ForecastState = 'yes' | 'no' | 'unknown';

/**
 * Whether a site can be forecast yet — or whether nobody has said.
 *
 * Three states, not two. `GET /locations` answers `forecastable: null` and
 * `history_days: null` for a site the pipeline has not scored, and a boolean
 * defaulted that to *true*: every such row read "forecasting" and the panel
 * summarised the workspace as "All set." That is a page stating a status it was
 * never given. `unknown` says so instead.
 */
export function forecastState(row: any): ForecastState {
  if (row?.blocker) return 'no';
  if (typeof row?.forecastable === 'boolean') return row.forecastable ? 'yes' : 'no';
  const days = row?.history_days ?? row?.historyDays;
  if (days != null) return Number(days) >= NEEDED_HISTORY_DAYS ? 'yes' : 'no';
  return 'unknown';
}

export function historyDaysOf(row: any): number | null {
  const days = row?.history_days ?? row?.historyDays;
  return days == null ? null : Number(days);
}

export function gapDaysOf(row: any): number | null {
  const gaps = row?.gap_days ?? row?.data_gaps ?? row?.gaps;
  if (gaps == null) return null;
  return Array.isArray(gaps) ? gaps.length : Number(gaps);
}

/**
 * The weekdays a mask says the kitchen trades, as words.
 *
 * A missing mask is *not* seven ones: `open_days_mask` comes back null on a site
 * that predates the field, and defaulting it made the dialog state a trading
 * pattern nobody had entered. Callers ask `maskRecorded` before claiming one.
 */
export function openDayNames(mask?: string | null): string[] {
  const bits = String(mask ?? '1111111').padEnd(7, '0');
  return DAY_NAMES.filter((_, index) => bits[index] === '1');
}

/** Whether the API actually sent a trading pattern. */
export function maskRecorded(row: any): boolean {
  return row?.open_days_mask != null && row?.open_days_mask !== '';
}

export interface LocationForm {
  name: string;
  area: string;
  cuisine: string;
  seating_capacity: string;
  service_type: string;
  district: string;
  open_days_mask: string;
  address: string;
  phone: string;
  manager_name: string;
  notes: string;
  opened_on: string;
}

export const emptyLocationForm: LocationForm = {
  name: '',
  area: 'Gasabo',
  cuisine: 'Buffet',
  seating_capacity: '',
  service_type: 'buffet',
  district: 'Gasabo',
  open_days_mask: '1111111',
  address: '',
  phone: '',
  manager_name: '',
  notes: '',
  opened_on: '',
};

/** The write shape `POST`/`PATCH /locations` accepts — empty means "not set". */
export function locationPayload(form: LocationForm) {
  return {
    name: form.name.trim(),
    area: form.area || undefined,
    cuisine: form.cuisine || undefined,
    opened_on: form.opened_on || undefined,
    seating_capacity: form.seating_capacity ? parseInt(form.seating_capacity, 10) : undefined,
    service_type: form.service_type || undefined,
    district: form.district || undefined,
    open_days_mask: form.open_days_mask || undefined,
    address: form.address || undefined,
    phone: form.phone || undefined,
    manager_name: form.manager_name || undefined,
    notes: form.notes || undefined,
  };
}

/** An existing row, loaded into the form. */
export function formFromRow(row: any): LocationForm {
  return {
    name: row?.name ?? '',
    area: row?.area ?? 'Gasabo',
    cuisine: row?.cuisine ?? 'Buffet',
    seating_capacity: row?.seating_capacity ? String(row.seating_capacity) : '',
    service_type: row?.service_type ?? 'buffet',
    district: row?.district ?? row?.area ?? 'Gasabo',
    open_days_mask: row?.open_days_mask ?? '1111111',
    address: row?.address ?? '',
    phone: row?.phone ?? '',
    manager_name: row?.manager_name ?? '',
    notes: row?.notes ?? '',
    opened_on: row?.opened_on ?? '',
  };
}

/** One weekday pill flips its bit in the mask. */
export function toggleOpenDay(mask: string, index: number): string {
  const bits = mask.padEnd(7, '0').split('');
  bits[index] = bits[index] === '1' ? '0' : '1';
  return bits.join('');
}

/** "Open Monday, Tuesday · closed Wednesday" — or the honest absence of one. */
export function openDaysSentence(row: any): string {
  if (!maskRecorded(row)) return 'Open days not recorded';
  const open = openDayNames(row.open_days_mask);
  if (open.length === 7) return 'Open every day';
  if (open.length === 0) return 'Closed every day';
  const closed = DAY_NAMES.filter((day) => !open.includes(day));
  return `Open ${open.join(', ')} · closed ${closed.join(', ')}`;
}
