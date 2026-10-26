# frontend/src/components/ingestion

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Ingestion tabs implementing Ch 3.6 Data Ingestion & Normalisation. The design envelope in `backend/ARCHITECTURE.md` §1 names six inbound channels; **five have a working adapter today**, and each writes the same canonical `demand_observations(location_id, date, demand_qty)` row:

| Channel | Entry point | `source` tag |
| --- | --- | --- |
| Manual entry | `POST /ingest/manual` | `manual` |
| Spreadsheet | `POST /ingest/file` → mapping → commit (CSV/XLSX) | `file` |
| Photo | `POST /ingest/image` → OCR → review → commit | `image` |
| Enrolled till | `POST /devices/{id}/demand` with `X-Device-Key` | `device` |
| Connector | `POST /connectors/{id}/pull`, and the queued sync job | the connector's `type` (`pos`, `momo`, `file`, `ocr`, `api`) |

Mobile money is a connector `type` on that same pull path, not a separate route — **no inbound webhook writes demand** (the `/webhooks` API manages outbound delivery only), so the proposal's MoMo webhook is design only, as is `sms`, a reserved connector type with no gateway parser. Neither is implemented anywhere in this folder.

The channel is **not a column**: `demand_observations` has no `source` field. It is written as a `[source:<tag>]` note plus `upload_id` or `source_id`, and `GET /ingest/demand` reads the tag back as `source`, so a row can be attributed without a JOIN.

Commit rules are enforced in one place, `app/services/ingestion/normaliser.py::commit_demand`: `UNIQUE(location_id,date)`, no future dates, staged `ingest_uploads` preview before commit.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `ExcelCsvTab.tsx` | Upload CSV/XLSX → `POST /ingest/file` → mapping → commit |
| `ManualFormTab.tsx` | Direct `POST /ingest/manual` with date/quantity validation, including the `MAX_DEMAND_QTY` ceiling it mirrors from the server (see `limits.ts`) |

The Manual form's rows put the **label above the control**, not in a label column.
The tabs render in an `xl:grid-cols-3`, so this card is ~260px wide: a label
column left each field 108px and the visitors input measured **26px**, which is
why it read as an empty gap rather than an input. A viewport breakpoint cannot
see the card's width, so the rows stack at every size rather than guessing.

The visitors field also starts **empty**. It used to be pre-filled with `100` — a
number the operator never chose, one stray click (through the confirm dialog)
away from being saved as a real day of demand. Empty and `0` are different
states: `0` is a restaurant that was shut, empty is nothing entered yet, so the
field holds a string and `visitorsValue` is `null` until something is typed.

The form **empties itself after a successful save** (`resetDraft()`): the count
and notes are cleared and the date returns to the workspace's today, while the
location stays chosen. This is a correctness rule, not politeness. Commit is an
upsert on `(location_id, date)`, so a draft that survived its own save sat one
click away from **overwriting** the day just stored — the operator types a new
count, presses Save, and the previous row is silently replaced instead of a
second day being added. Leaving the saved date in the field is what made that
re-write possible, which is why the date is reset too. `Add another entry`
clears the count for the same reason: a retained value queued the same day of
demand twice.

**Past Fills is refreshed explicitly after a write**, not by an effect on
`entries.length`. That length is 0 both before and after a save of the bare form
(the single-entry path), so the effect never fired for it and the row just saved
was the one row missing from the list. The effect is now keyed on the location
alone.

| `PosMomoTab.tsx` | `GET /sources`, `POST /sources/{id}/sync`, `GET /sources/sync-history` |
| `UploadImagePanel.tsx` | `POST /ingest/image` → job id; the preview panel fetches the rows |

The image panel reports **no row count of its own**. `POST /ingest/image`
returns only a job id, so the earlier copy — "2 rows extracted (demo)" — was a
hardcoded number that promised two rows even for a photo the OCR engine read
nothing from. The count is reported by `PreviewPanel`, from the rows that
actually came back; when OCR is not configured the backend returns zero rows and
an explicit note, and the panel says so instead of inventing a preview.

`PosMomoTab` is wired to the **real Sources API**. It used to be a prototype
with no API call at all — "Authorise" set a local flag and announced a 90-day
backfill, "Import 4 days" announced an import, and the preview listed four
hardcoded dates (`2026-09-15` … `2026-09-18`) — so the screen could report demand
as ingested when nothing had been written.

It now reads `GET /sources` for which POS / mobile-money channels exist and how
fresh each is, queues the real pull with `POST /sources/{id}/sync`, and reports
runs from `GET /sources/sync-history`. Two rules the code keeps:

- **A sync is queued, not finished.** The endpoint enqueues the run and it stays
  `queued` until the scheduler or worker drains it (`last_sync_at` is stamped on
  completion, not on queueing), so the toast says *queued* and the copy says rows
  appear once the run completes. Reporting "imported" here is the exact claim the
  old prototype made for a request it never sent. When the run is drained,
  `_job_connector_sync` performs a **real pull** — it fetches the provider endpoint
  in the connector's credentials and commits the returned rows through
  `commit_demand`, tagged with the channel's `source_id` (`services/ingestion/connector_pull.py`).
  A channel with no endpoint is reported as such and does **not** get a freshness
  stamp, because no pull happened.
- **Only `pos` and `momo` channels belong here.** An OCR or file source is real
  and appears on Sources; this tab does not count it or answer for it.

Creating a channel needs `connectors:manage`, which this seat does not hold
(`POST/GET /connectors` answer 403 without it — measured), so the offer to add
one is made only to a seat that can, and everyone else gets a `PermissionNote`
rather than a button that would bounce them off an unopenable route.

The export upload hands off to the **spreadsheet channel** via
`onRouteToSpreadsheet`. A POS export *is* a CSV/XLSX file and that channel
already does preview → column mapping → commit, so the export takes the real
path instead of a second, thinner upload that only showed a toast.

**Sync is offered only for an `active` channel.** `_job_connector_sync` selects
connectors with `status == "active"` and steps over everything else — and
`available`, which is what *creating* a connector defaults to, is everything
else. Enabling the button for a channel the job skips would queue a run that
settles as `success` having pulled nothing, so the tab ties the button to the set
the job actually processes and explains the rest. The API enforces the same rule:
`POST /sources/{id}/sync` answers **409 `not_active`** for any other status, so a
direct caller gets the refusal too and `Sources.tsx` disables its Sync button on
the same `isSyncable` check. Creating or activating a channel needs
`connectors:manage` (`GET/POST /connectors` answer 403 without it — measured), so
the offer to add one is made only to a seat that can.

Each channel also shows its **own last pull** (`last_pull` on `GET /sources`) —
rows pulled, committed and skipped — because the workspace-wide run table records
no connector id, so a per-channel row there is impossible. Before any pull the
line says *No pull yet*, and a channel with no endpoint shows the backend's own
reason instead of a fabricated count.

Channel naming and status colour come from `lib/sourceChannels.ts`, shared with
`Sources.tsx` so `momo` cannot read as "Mobile Money" on one screen and `momo`
on the other.

## Import trace (`ExcelCsvTab.tsx`)

After a commit the tab shows an **Import trace**: the demand rows that file wrote,
asked for as `GET /ingest/demand?upload_id={id}`. Matching on the row's own
foreign key is the only honest trace — comparing dates, or hunting the filename
inside free-text notes, would also "find" a manual entry that merely mentioned
the file.

The panel lives *outside* the upload card because that card resets the moment a
commit lands, and "which rows did this import produce?" is asked after it, when
the upload id is gone from state. A commit that reconciled every day wrote no new
rows, which is reported as exactly that rather than as an empty table; and a
failed read is reported as a failure, not as "nothing was written".

## OCR upload and engine quality

The upload is checked by content, not by its filename or the browser's MIME
type: `POST /ingest/image` returns **400 `invalid_image`** for a non-image or an
image above the pixel budget. The local engine applies EXIF orientation, a
conservative upscale, illumination flattening and a deskew — in that order, for
the reasons in *Photos of a page* — then tries Tesseract's uniform-block, column
and sparse page-segmentation modes. The mode is chosen by distinct dates, then
fewest repeated dates, then plausible counts, then word confidence — not by
which one returned the most rows, because a mode that splits lines in two can
always return more. `GET /ingest/image/{job_id}` reports the chosen mode, the
measured mean word confidence and `row_source` under `quality`, separately from
each row's line-legibility score. `row_source` is `lines` when the engine's own
lines carried both fields, or `cells` when the page came back column-by-column
and the words had to be re-grouped into rows by their position on the page.

The count on a line is the number nearest the date that is neither part of the
date nor the value of a label (`Table 6`, `tips 3`). Taking the last integer on
the line, as the parser used to, imported the table number as the visitor count.
A count of `0` is kept: a restaurant that served nobody has not stopped
reporting, and the platform stores zero.

A date written without a year (`12 Oct`, `3 Jan`) is read in the **reporting
year** the operator picks above the upload button, sent as `reporting_year`.
Left to the server it would default to the current year, which silently files an
archive page as this year's demand — and the commit cannot tell the difference
afterwards. The year travels in the preview, so a retry re-reads the photo under
the same assumption.

Extraction accuracy is measured rather than assumed: `backend/scripts/ocr_accuracy.py`
scores the real pipeline over verified photos and a reference CSV, and should be
run before and after any change to the parser, the preprocessing or the engine.

## OCR row scores (`../../PreviewPanel.tsx`)

The photo review lists each row's `confidence`, worded for the engine that
produced it:

- **`remote`** — a vision service, so the value is a real model score and is
  shown as a percentage.
- **`tesseract`** — the local parser's number is a **readability signal**, not a
  probability. `0.9` means one number was left on the line after the date (the
  demand count was unambiguous); `0.75` means several were and the nearest to the
  date was taken for the operator. Those two are shown as *One number on the
  line* / *Several numbers on the line — check*, never as "75% confident".
- A row with no score says **No score reported**, and a row the operator typed
  says **Added by hand** — neither invents a figure the engine never produced.

A summary counts the rows worth a second look, and is absent when every row was
unambiguous (a caveat that is always on is a caveat nobody reads). `engine`
travels with the rows on `GET /ingest/image/{job_id}` for exactly this reason.

Two more aids sit on the same screen, both for the same reason — a wrong or
duplicated demand record is worse than a missing one, because nothing downstream
can tell it from a real trading day:

- **Highlights on the photo.** Each row's `boxes` give where the engine read its
  date and its count, as fractions of the upload (`null` when the engine reported
  no geometry). `PreviewPanel` fetches the stored photo from
  `GET /ingest/uploads/{id}/source` and draws them over it, emphasising the row
  the operator is on. The OCR copy is upscaled, straightened and padded, so the
  backend maps every box back through those steps first; the caption says the
  highlights are approximate once the page has been straightened.
- **Repeated dates.** The commit upserts on `(location_id, date)`, so two rows
  for one day become one and the later silently wins. The preview names the date
  and both counts before the import, and marks both rows. The backend reports the
  same list under `warnings` (`duplicate_date` / `conflicting_date`).

## Messy spreadsheets are the normal case

A logbook keyed into Excel is not a template: a title line, a date range, a
blank spacer, `Visitors (adults)`, `1,112`, `Dec 3, 2025`, a `TOTAL` footer, an
`n/a` week. Three rules keep such a file importable, and each one exists because
its absence imported **nothing**:

- **The header is found, not assumed.** `parse_file_table` walks the first 25
  rows for the first one that names two or more canonical fields and carries no
  dates. Row 1 being a title used to turn a four-column file into one column
  named after the restaurant.
- **The mapping arrives filled in.** `POST /ingest/file` returns `suggested_map`
  (alias matching over a vocabulary of the words logs use), and the dropdowns
  start from it. The old guess was `header.includes('date')` on raw names.
- **A footer cannot veto the file.** Rows that are not data — totals, blanks,
  `n/a` — are counted as `skipped_count` and named; they are not errors. Rows
  that are data but unreadable are errors, listed, and **only block when nothing
  is importable**: the readable days still commit, and the toast reports what was
  left out. Requiring `error_count === 0` meant one `TOTAL` line made a whole
  month uncommittable.

`preamble_rows > 0` is surfaced in the upload card, so an operator can see the
file was read past its title rather than mangled.

Location names are matched **conservatively fuzzily**
(`backend/app/services/ingestion/location_match.py`): case, accents, punctuation,
doubled spaces and a one-word typo are normalised away, a shorter name contained
in a longer one needs two shared words, and two equally plausible sites are
refused rather than guessed. `Remera Grills` resolves to `Remer Grills`; an
unknown site is still an error the operator sees.

## Photos of a page

The image pipeline flattens the lighting (subtracting a heavily blurred copy of
the page) before it stretches contrast, then deskews by up to 6 degrees measured
on a 240px probe. Measured on a six-image set: a shadow across the page went
from **0 rows to 3 correct**, a 4.5-degree tilt from **2 rows (one wrong) to 3
correct**, and clean/low-resolution pages were unchanged. A median filter was
tried and removed — it cleaned speckle the flattening already handled and turned
hand-written `98` into `9` and `134` into `434`.

Handwriting remains the weak case: jittered glyphs still misread digits (a
`2025` came back `2925` in one sample), which is why every row carries its own
readability score and the review screen asks for confirmation. `OCR_SERVICE_URL`
remains the route to a real handwriting model.

## The original photo is kept (`../../pages/ImportDemandData.tsx`)

Reviewing OCR rows means comparing them with the page they came from, so the
upload now stores the photo itself (`ingest_uploads.source_image`, deferred so
listing uploads never loads it) and serves it at
`GET /ingest/uploads/{id}/source`, workspace-scoped like the rows inside it.
`GET /ingest/uploads` reports only `has_source`, and Recent uploads shows a
photo control for those rows that expands the image beneath the table — no new
tab, and the object URL is revoked on close. Uploads from before the photo was
kept have nothing stored and answer `404 no_source`, rather than rendering an
empty frame that looks like a failed photo.

## Key Props / Contracts / Data Sources
Contracts: `POST /ingest/file {id, preview}`, `POST /ingest/file/{id}/mapping`, `POST /ingest/file/{id}/commit`, `POST /ingest/manual`, `POST /ingest/image` + `GET /ingest/image/{job_id}` (OCR), `GET /ingest/uploads` (+ `GET /ingest/uploads/{id}/source` for the stored photo). Canonical row `UNIQUE(location_id,date)` per `database/schema.sql:132`.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [ML Contracts](../../../../model/README.md)
- [Feature Spec](../../../../model/FEATURE_SPEC.md)
- [Proposal](../../../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
