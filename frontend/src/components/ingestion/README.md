# frontend/src/components/ingestion

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Ingestion tabs implementing Ch 3.6 Data Ingestion & Normalisation and `backend/ARCHITECTURE.md` §1 (six channels → canonical `demand_observations(location_id,date,demand_qty)`). Enforces commit rules: `UNIQUE(location_id,date)`, no future dates, staged `ingest_uploads` preview before commit.

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
image above the pixel budget. The local engine applies EXIF orientation,
contrast normalisation and a conservative upscale, then tries Tesseract's
uniform-block, column and sparse page-segmentation modes. `GET
/ingest/image/{job_id}` reports the chosen mode and the measured mean word
confidence under `quality`, separately from each row's line-legibility score.

## OCR row scores (`../../PreviewPanel.tsx`)

The photo review lists each row's `confidence`, worded for the engine that
produced it:

- **`remote`** — a vision service, so the value is a real model score and is
  shown as a percentage.
- **`tesseract`** — the local parser's number is a **readability signal**, not a
  probability. `0.9` means one number was left on the line after the date (the
  demand count was unambiguous); `0.75` means several were and the last was taken
  for the operator. Those two are shown as *One number on the line* / *Several
  numbers on the line — check*, never as "75% confident".
- A row with no score says **No score reported**, and a row the operator typed
  says **Added by hand** — neither invents a figure the engine never produced.

A summary counts the rows worth a second look, and is absent when every row was
unambiguous (a caveat that is always on is a caveat nobody reads). `engine`
travels with the rows on `GET /ingest/image/{job_id}` for exactly this reason.

## Key Props / Contracts / Data Sources
Contracts: `POST /ingest/file {id, preview}`, `POST /ingest/file/{id}/mapping`, `POST /ingest/file/{id}/commit`, `POST /ingest/manual`, `POST /ingest/image` + `GET /ingest/image/{job_id}` (OCR), `GET /ingest/uploads`. Canonical row `UNIQUE(location_id,date)` per `database/schema.sql:132`.

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
