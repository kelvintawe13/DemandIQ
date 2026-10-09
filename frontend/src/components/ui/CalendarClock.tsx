import { useEffect, useMemo, useRef, useState } from "react";
import { ClockIcon, CalendarIcon, InfoIcon, MoonIcon } from "lucide-react";
import { Panel } from "./Panel";
import { addDaysISO, getWorkspaceTimezone } from "../../lib/workspaceTime";
import { useWorkspaceTimezone } from "../../hooks/useWorkspaceTimezone";

interface CalendarClockProps {
  /** When given, shows ML features for this date instead of today. ISO YYYY-MM-DD */
  targetDate?: string;
  /** Compact inside a card, or full */
  size?: "sm" | "md";
  /** Show ML feature strip */
  showML?: boolean;
}

// Rwanda holidays 2026 (subset) + Golden Week (JP anomaly), matching model/features/build.py fs-2026.09.2
const RW_HOLIDAYS_2026 = new Set(["2026-01-01", "2026-04-07", "2026-05-01", "2026-07-04", "2026-08-15", "2026-12-25"]);
function isHoliday(iso: string) {
  return RW_HOLIDAYS_2026.has(iso);
}

/** Kigali-consistent helpers: all calendar features must be derived in the selected timeZone */
function toISOInTZ(date: Date, tz: string): string {
  if (tz === "local") {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }
  // en-CA gives YYYY-MM-DD in the requested timeZone
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function partsForISO(iso: string): { year: number; month: number; day: number; dow: number } {
  const [y, m, d] = iso.split("-").map(Number);
  /*
   * A calendar date's weekday is a property of the date, not of a timezone, so
   * read it from UTC midnight. Deriving it from "UTC noon, then converted to the
   * zone" shifted the day for zones east of +12: at GMT+14 the UTC-noon instant
   * is already 02:00 the *next* day, so 2026-10-02 read as Saturday (it is a
   * Friday) and the long date printed 3 October.
   */
  return { year: y, month: m, day: d, dow: new Date(Date.UTC(y, m - 1, d)).getUTCDay() };
}

function goldenWeekFeaturesFromParts(year: number, month: number, day: number) {
  const md = month * 100 + day;
  const isGW = md >= 429 && md <= 505;
  const utc = Date.UTC(year, month - 1, day);
  const gwStart = Date.UTC(year, 3, 29);
  const gwEnd = Date.UTC(year, 4, 5);
  let dist = 0;
  if (utc < gwStart) dist = Math.round((utc - gwStart) / 86400000);
  else if (utc > gwEnd) dist = Math.round((utc - gwEnd) / 86400000);
  const clipped = Math.max(-14, Math.min(14, dist));
  return { is_golden_week: isGW ? 1 : 0, days_to_golden_week: clipped };
}

// The Date-based wrappers that used to sit here were dead: the TZ-aware
// `…FromParts` / `…FromISO` versions below replaced them and are what the
// component calls.
function nonworkRunFromISO(iso: string): number {
  let run = 0;
  let curISO = iso;
  for (let i = 0; i < 7; i++) {
    const { dow } = partsForISO(curISO);
    const isWk = dow === 0 || dow === 6;
    if (isWk || isHoliday(curISO)) run++;
    else break;
    curISO = addDaysISO(curISO, -1);
  }
  return run;
}

const TZ_OPTIONS = [
  { id: "Africa/Kigali", label: "GMT+2 · Africa/Kigali", offset: "GMT+2" },
  { id: "UTC", label: "UTC · GMT+0", offset: "GMT+0" },
  { id: "local", label: "Local", offset: "Local" },
] as const;

/** `GMT+2` for any IANA zone, including one the workspace names that is not preset. */
function offsetLabel(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" }).formatToParts(new Date());
    return parts.find((part) => part.type === "timeZoneName")?.value ?? tz;
  } catch {
    return tz;
  }
}

export function CalendarClock({ targetDate, size = "md", showML = true }: CalendarClockProps) {
  const [now, setNow] = useState(() => new Date());
  // The clock defaults to the workspace's configured zone, not a literal. The
  // zone is read once at boot and may not have resolved yet when this mounts,
  // so the effect below adopts it — without overriding a zone the operator
  // chose here.
  const workspaceTz = useWorkspaceTimezone();
  const [tz, setTz] = useState<string>(() => getWorkspaceTimezone());
  const tzTouched = useRef(false);
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (!tzTouched.current && workspaceTz) setTz(workspaceTz);
  }, [workspaceTz]);

  // Keep the workspace zone selectable even when it is not one of the presets.
  const tzOptions = useMemo(() => {
    if (TZ_OPTIONS.some((option) => option.id === tz)) return TZ_OPTIONS;
    return [{ id: tz, label: `${offsetLabel(tz)} · ${tz}`, offset: offsetLabel(tz) }, ...TZ_OPTIONS];
  }, [tz]);

  const tzLabel = tzOptions.find((o) => o.id === tz)?.offset ?? offsetLabel(tz);
  const tzDisplay = tz === "local" ? Intl.DateTimeFormat().resolvedOptions().timeZone : tzLabel;

  const display = useMemo(() => {
    const fmtTz = tz === "local" ? undefined : tz;
    const fmtTime = new Intl.DateTimeFormat("en-GB", { timeZone: fmtTz, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    // A target date is already a calendar day, so it is formatted from UTC
    // midnight (a fixed instant on that day); the live clock is formatted in the
    // selected zone. Both used to share one formatter, so the UTC-noon base of a
    // far-east zone printed the following day.
    const dateFmtTz = targetDate ? "UTC" : fmtTz;
    const fmtDate = new Intl.DateTimeFormat("en-GB", { timeZone: dateFmtTz, weekday: "long", year: "numeric", month: "long", day: "numeric" });
    const fmtDow = new Intl.DateTimeFormat("en-GB", { timeZone: dateFmtTz, weekday: "short" });

    // Service day in selected TZ: targetDate is already YYYY-MM-DD in Kigali/tz terms; live uses now converted to tz
    const iso = targetDate ? targetDate : toISOInTZ(now, tz);
    const { year, month, day, dow } = partsForISO(iso);
    const gw = goldenWeekFeaturesFromParts(year, month, day);
    const nwr = nonworkRunFromISO(iso);
    const holiday = isHoliday(iso);
    const before = isHoliday(addDaysISO(iso, 1));
    const after = isHoliday(addDaysISO(iso, -1));

    // Base for Intl formatting: UTC midnight for a date-only target (which the
    // UTC formatter above renders as that day), the live instant otherwise.
    const base: Date = targetDate ? new Date(Date.UTC(year, month - 1, day)) : now;

    const daysInMonth = new Date(year, month, 0).getDate();

    return {
      time: fmtTime.format(now),
      date: fmtDate.format(base),
      dowShort: fmtDow.format(base),
      dowNum: dow,
      iso,
      isWeekend: dow === 0 || dow === 6,
      isHoliday: holiday,
      dayBeforeHoliday: before ? 1 : 0,
      dayAfterHoliday: after ? 1 : 0,
      daysToMonthEnd: daysInMonth - day,
      ...gw,
      nonwork_run: nwr,
    };
  }, [now, targetDate, tz]);

  const isCompact = size === "sm";

  return (
    <Panel
      title={targetDate ? "Forecast date: calendar features" : `Live clock · ${tzDisplay}`}
      description={targetDate ? `${display.iso} · ${display.dowShort} · ML features as-of 23:59` : `${tzDisplay} · real time · updates every second · same features the model sees`}
      icon={targetDate ? CalendarIcon : ClockIcon}
    >
      <div className="flex flex-col gap-3">
        {/* Timezone selector: default GMT+2 */}
        <div className="flex items-center justify-end gap-1.5">
          <span className="text-fine font-semibold uppercase tracking-wide text-slate-500">Time zone</span>
          <select
            value={tz}
            onChange={(e) => { tzTouched.current = true; setTz(e.target.value); }}
            className="rounded-md border border-slate-200 bg-white px-2 py-1 text-fine font-medium text-slate-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            aria-label="Time zone"
          >
            {tzOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {/* Clock strip: real time */}
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 dark:border-slate-700 dark:bg-slate-900">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
            {targetDate ? <CalendarIcon className="h-5 w-5" /> : <ClockIcon className="h-5 w-5" />}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-fine font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {display.dowShort} · {display.iso}
            </p>
            <p className="truncate text-body-sm font-semibold text-slate-900 dark:text-slate-100">{display.date}</p>
            <p className="font-mono text-caption tabular-nums text-slate-600 dark:text-slate-300">
              {/* With a target date the row above is a *future* day, so the live
                  clock underneath it would read as that day's time. Name it for
                  what it is: the instant the feature row was computed. */}
              {targetDate && <span className="font-sans text-slate-500">computed now · </span>}
              {display.time} <span className="text-slate-500">{tzDisplay}</span>
              {display.isHoliday && <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-fine font-bold text-amber-700">HOLIDAY</span>}
              {display.isWeekend && !display.isHoliday && <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-fine font-bold text-slate-600">WEEKEND</span>}
            </p>
          </div>
          {!targetDate && (
            <span className="hidden items-center gap-1.5 text-fine text-slate-500 sm:flex">
              <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
              live
            </span>
          )}
        </div>

        {/* ML feature strip: mirrors model/features/build.py fs-2026.09.2 */}
        {showML && (
          <div className={isCompact ? "grid grid-cols-2 gap-2" : "grid grid-cols-2 gap-2 sm:grid-cols-4"}>
            {[
              { k: "dow", v: String(display.dowNum), hint: "0 Sun → 6 Sat" },
              { k: "is_weekend", v: String(display.isWeekend ? 1 : 0), hint: "Sat/Sun" },
              { k: "is_holiday", v: String(display.isHoliday ? 1 : 0), hint: "RW 2026" },
              { k: "days_to_month_end", v: String(display.daysToMonthEnd), hint: "tail gap" },
              { k: "is_golden_week", v: String(display.is_golden_week), hint: "Apr29-May5" },
              { k: "days_to_golden_week", v: String(display.days_to_golden_week), hint: "clipped -14..14" },
              { k: "day_before_holiday", v: String(display.dayBeforeHoliday), hint: "t+1 holiday" },
              { k: "day_after_holiday", v: String(display.dayAfterHoliday), hint: "t-1 holiday" },
              { k: "nonwork_run", v: String(display.nonwork_run), hint: "Sat/Sun+hol run" },
            ].map((f) => (
              <div key={f.k} className="rounded-lg border border-slate-200 px-2.5 py-2 dark:border-slate-700">
                <p className="truncate font-mono text-fine font-bold text-brand-700 dark:text-brand-300">{f.k}</p>
                <p className="font-mono text-body-xs font-semibold tabular-nums text-slate-900 dark:text-slate-100">{f.v}</p>
                <p className="truncate text-fine leading-tight text-slate-500">{f.hint}</p>
              </div>
            ))}
          </div>
        )}

        {/* Helper */}
        <p className="flex items-start gap-1.5 text-fine leading-relaxed text-slate-500 dark:text-slate-400">
          <InfoIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-500" />
          <span>
            Same calendar features the model sees at <span className="font-mono font-semibold">as_of 23:59</span> the day before
            {targetDate ? ` ${targetDate}` : " the forecast"}, all derived from the calendar alone and never from future demand.
            {display.is_golden_week ? " Golden Week anomaly. Expect high variance." : ""}
          </span>
        </p>

        {/* Tiny live date picker helper when not targetDate */}
        {!targetDate && (
          <p className="flex items-center gap-1.5 text-fine text-slate-500">
            <MoonIcon className="h-3 w-3" />
            <span>Tip: pick a forecast date in Demand Forecast to see its exact feature row.</span>
          </p>
        )}
      </div>
    </Panel>
  );
}
