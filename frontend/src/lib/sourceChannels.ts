/**
 * One vocabulary for source channels and their state.
 *
 * `Sources.tsx` and the ingestion page's POS / Mobile Money tab render the same
 * rows from `GET /api/v1/sources`. A channel that reads `Mobile Money` on one
 * screen and `momo` on the other is two names for one thing, and a `degraded`
 * source that is amber on one page and grey on the next reads as two different
 * states. Both screens therefore take the naming and the colour from here.
 */
import type { StatusTone } from '../components/ui/StatusPill';

/** The channel kind as the API spells it (`momo`, `pos`, …). */
export const kindOf = (source: unknown): string =>
  String((source as { type?: unknown; kind?: unknown } | null)?.type ??
    (source as { kind?: unknown } | null)?.kind ??
    'API');

/** The channel kind in words (`momo` -> `Mobile Money`). */
export const kindLabel = (raw: string): string =>
  raw === 'momo'
    ? 'Mobile Money'
    : raw === 'pos'
      ? 'POS'
      : raw === 'file'
        ? 'Spreadsheet'
        : raw === 'ocr'
          ? 'Image OCR'
          : raw === 'manual'
            ? 'Manual entry'
            : raw;

/**
 * The import kind behind a channel, when ingestion registered it.
 *
 * `GET /sources` reports `import_kind` for the channels the ingestion service
 * creates itself (uploads, photos, hand entry, till pushes) and `null` for a
 * connector an administrator configured. Those two are not interchangeable: one
 * receives data and has nothing to pull, the other is pulled.
 */
export const importKindOf = (source: unknown): string | null => {
  const kind = (source as { import_kind?: unknown } | null)?.import_kind;
  return typeof kind === 'string' && kind ? kind : null;
};

/** The import kind in words, for the control that cannot sync it. */
export const importKindLabel = (kind: string): string =>
  kind === 'file'
    ? 'spreadsheet uploads'
    : kind === 'image'
      ? 'photo uploads'
      : kind === 'manual'
        ? 'manual entry'
        : kind === 'device'
          ? 'till pushes'
          : kind;

/**
 * Why a channel cannot be pulled, or `undefined` when it can.
 *
 * The reason is the channel's, not the UI's. An import channel is filled by
 * uploads and pushes, so "an administrator activates it on Connectors" — the
 * wording for a connector nobody has configured — would be advice that cannot
 * make the button work.
 */
export const syncBlockedReason = (source: unknown): string | undefined => {
  if (isSyncable(source)) return undefined;
  const kind = importKindOf(source);
  if (kind) return `Filled by ${importKindLabel(kind)} — there is nothing to pull`;
  return stateOf(source).toLowerCase() === 'active'
    ? undefined
    : 'Only an active channel can be pulled — an administrator activates it on Connectors';
};

/**
 * A source's health in one word, for filtering and the status pill.
 *
 * `status` wins over `health` where both exist: `status` is the operator's
 * intent (`paused` is deliberate) while `health` is the platform's judgement
 * about it, so a paused source must not read as `degraded`.
 */
export const stateOf = (source: unknown): string =>
  String(
    (source as { status?: unknown; health?: unknown } | null)?.status ??
      (source as { health?: unknown } | null)?.health ??
      'unknown',
  );

/** Status/health -> pill colour, so `degraded` is amber on every screen. */
export const SOURCE_STATUS_TONE: Record<string, StatusTone> = {
  connected: 'success',
  syncing: 'info',
  error: 'critical',
  inactive: 'neutral',
  healthy: 'success',
  degraded: 'warning',
  unknown: 'neutral',
  available: 'neutral',
  active: 'success',
  paused: 'neutral',
  failing: 'critical',
};

/**
 * The channel kinds that carry POS / mobile-money transactions.
 *
 * Only these belong on the ingestion page's POS / Mobile Money tab. A source of
 * another kind (an OCR connector, say) is real and appears on Sources; claiming
 * it here would be this tab answering for a channel it does not own.
 */
const POS_MOMO_TYPES = ['pos', 'momo'];

/** Whether a source is a POS or mobile-money channel. */
export const isPosOrMomo = (source: unknown): boolean =>
  POS_MOMO_TYPES.includes(kindOf(source));

/**
 * The one status a sync acts on.
 *
 * `_job_connector_sync` pulls connectors with `status == "active"` and skips
 * every other value (including `available`, which is what creating a connector
 * defaults to), and `POST /sources/{id}/sync` refuses a non-active channel with
 * `409 not_active`. Offering Sync for anything else would queue a run that
 * reports success while doing nothing — so both screens tie the control to
 * exactly the set the job processes.
 */
const SYNCABLE_STATUS = 'active';

/** Whether a sync would actually pull from this channel. */
export const isSyncable = (source: unknown): boolean =>
  stateOf(source).toLowerCase() === SYNCABLE_STATUS;
