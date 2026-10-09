# frontend/src/components/ui

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Design-system primitives used across all pages and admin screens. Supports Ch 3.22 failure behaviour (EmptyState/ErrorPage for model fallback, connector down, malformed upload) and governance tables (change_requests, audit_log).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `Modal.tsx` / `ConfirmDialog.tsx` | Dialogs for commit/approve/reject |
| `Panel.tsx` | Card/panel container |
| `TabBar.tsx` | The one segmented control (`role="tab"`, thumb-sized tabs on a phone, scrolls rather than overflowing, focus ring) |
| `StatCard.tsx` | KPI tile — label / big number / one line of context |
| `LoadingState.tsx` | The one spinner — `LoadingState` centres a whole content region, `Spinner` is the same ring for a wait inside a button, row or panel |
| `Skeleton.tsx` | `SkeletonLine` / `SkeletonBlock` / `PanelSkeleton` / `PageSkeleton` — a first load shows the page's own shape filling in rather than a lone spinner. One `role="status"` carries the same accessible name the spinner used |
| `Table.tsx` | `TableShell` / `TRow` / `TD` — one header style, scroll container, empty-state slot. Also `ResponsiveTable` — a column-driven table that restyles **into cards below `md`** (one markup tree; the switch is pure CSS via `data-label`, see `index.css`). A row made selectable with `onRowClick` is also focusable, activates on Enter/Space, and takes `rowAriaLabel` for its accessible name. The `primary` column renders as a `<th scope="row">`, so the first column names its row on desktop and heads the card on a phone. A `TableShell` row names itself with `<TD scope="row">` on its first cell (the same role as `ResponsiveTable`'s `primary` column), and both components take `maxHeight` for a bounded list with a pinned header. Both take a `caption` (the table's accessible name — a panel heading names the panel, not the table) and a `captionVisible` flag; the caption is `sr-only` unless asked for, because a table under a visible heading does not need the words twice |
| `Toggle.tsx` | The one switch (`role="switch"`). Carries a 44px touch target on a phone while keeping its designed on-screen size |
| `useDialogBehaviour.ts` | The dialog contract — focus in on open, Escape, the Tab trap, page scroll lock, focus back to the trigger — for the two surfaces that own their own layout (the legal reader, the welcome sheet) |
| `PermissionNote.tsx` | "Why you cannot change this" note for controls the seat's capabilities do not cover |
| `Field.tsx` / `SelectMenu.tsx` | Form controls |
| `FormModal.tsx` | The edit dialog every admin record uses: a real `<form>` (Enter submits), Cancel/Save footer, busy state on save |
| `FilterBar.tsx` | The toolbar a record list shares — search box, one dropdown per named filter, active-count, and the result line |
| `ViewSwitcher.tsx` | Table / cards / list toggle (`aria-pressed`); the choice persists through `useListState` |
| `Pagination.tsx` | "Showing 1–12 of 40 models" plus prev/next and an optional page-size picker; hidden for a single page |
| `RecordList.tsx` | Column-driven list composing `FilterBar` + `ViewSwitcher` + `ResponsiveTable`/cards + `Pagination` over `useListState` |
| `StatusPill.tsx` | Status badges (parsing/ready/committed, training/production) |
| `CountChart.tsx` | A count of things grouped by something — which check raised the issues, how long since each device last checked in — as columns or horizontal bars. The y-axis counts objects, so its ticks come from `formatCountTick`, and its ceiling comes from the data, never a pinned `domain`. A horizontal chart is one row per label at 34px (capped at 14 rows) and takes `labelWidth` for long labels. Both axes are titled through `axisLabel.ts`: `categoryLabel` names the rows/columns (`Role`, `Check`), and the value axis defaults to `Number of ${noun}` |
| `MixDonut.tsx` | How a set divides into a few named parts, with the counts and shares in the legend. Colours arrive from the caller, because a severity palette and a device-status palette mean different things |
| `EmptyState.tsx` | Empty + error slot (illustration + CTA) |
| `DateRangeModal.tsx` / `LocationPickerModal.tsx` | Shared pickers for reports/forecasts |
| `AnimatedTitle.tsx` | Inter display title with a short entrance (`rise` / `words`); renders static text under `prefers-reduced-motion` |
| `CalendarClock.tsx` | Kigali-consistent clock plus the derived calendar/ML date features for a target date. With a `targetDate` the live time is labelled `computed now`, so a future day's date is not read as that day's clock time |

## Key Props / Contracts / Data Sources
Props: `Modal { open, onClose, title }`; `StatusPill { variant: 'success'|'warning'|'critical' }`; `EmptyState { illustration, title, action }`; `ResponsiveTable { columns, rows, rowKey, onRowClick?, rowAriaLabel? }`. No direct API calls.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Deliberate non-primitive surfaces

Audited against § 1 of the UI skill ("there is one spinner, one switch, one
segmented control"). These keep their own markup on purpose:

- `legal/LegalModal` and `WelcomeModal` declare `aria-modal` but need a layout
  `Modal` cannot express — a fixed-height document reader with its own section
  navigation, and a bottom sheet that becomes a centred card. Both now take
  their dialog behaviour from `useDialogBehaviour` rather than re-implementing
  part of it.
- `legal/ConsentBanner` (`role="dialog"` without `aria-modal`) and the sheet in
  `MobileNav` stay outside `Modal`: a cookie banner must not trap focus, and the
  sheet is navigation rather than a task dialog.
- The theme control in `chrome/HeaderActions` and `auth/AuthControls` carries
  `role="switch"` but is a two-way sun/moon picker, not the `Toggle`
  track-and-knob — `Toggle` is for on/off settings.
- `agent/AgentPanel` renders its own equal-width in-panel view tabs and an
  inline suggestion `listbox`; `TabBar` and `SelectMenu` express neither shape.
- `agent/AgentPanel` docks as a third column from **1180px** (`min-[1180px]:`,
  mirroring `RAIL_MIN_WIDTH` in `contexts/AgentContext`) and is open on load
  there — iPad landscape and laptops get the rail without the page being
  covered. Below 1180px it is a drawer that starts closed. It spends an
  assistant session only on a gesture, so a rail that is open by default costs
  nothing on page load. The sidebar keeps its full width in either state;
  collapsing it stays the user's call.
- Dashed-border blocks (`Sources`, `Reports`, `Settings`, `UploadImagePanel`,
  `ExcelCsvTab`, …) are drop zones, promos and previews, not empty states. Real
  empty / error / offline regions all render `EmptyState`.

## Mobile contracts

- **Breakpoint.** Below `md` (768px) the layout must fit 320px with no horizontal document scroll. A wide element is only acceptable inside an explicit `overflow-x-auto` / `touch-scroll` container.
- **A long list scrolls inside its panel, not down the page.** For a list whose length is the data's rather than the page's — an upload history, a daily breakdown, the issue list — pass `maxHeight` to `ResponsiveTable` (`maxHeight="max-h-[420px]"`, or `max-h-[380px]` for two-line rows). It caps the scroll container, pins an opaque sticky header, and contains the scroll so reaching the end does not drag the page. Do not hand-roll the classes: the prop exists because the sticky-header markup otherwise has to be repeated in every column's `headerClassName`, and one missed column leaves a list whose heading scrolls away. Capped today: the quality issues list, the analytics breakdown, the upload history, forecast accuracy by location, seat allowances, the access console's role matrix, and the Site Admin account/workspace directories.
- **A row's action cell must not be `flex-wrap` — unless the row's height is set by another cell.** With a wrapping flex row the column's widest content is *one* button, so the table sizes it to ~95px and stacks three buttons over three lines — 156px per row instead of 57px on the quality issues list, where `whitespace-nowrap` is the fix. Two caveats worth measuring before copying it: the Location Registry keeps the wrap on purpose, because its rows are 106px tall at every width regardless (the *Location* cell's own wrap sets that height), and unwrapping the actions measured 2px shorter while pushing the buttons out of a 768px panel. Measure the row, not the cell. The actions column is deliberately **not** pinned: the spelled-out labels (`Apply fix` / `Dismiss` / `Details`) are ~254px of the ~404px the quality panel gets with the assistant rail docked, so pinning them would cover the issue they act on. The row scrolls sideways at that width and fits with no scroll once the content column is wider (rail closed).
- **Tables.** A data table that shelves into horizontal scroll is a cropped table on a phone. Use `ResponsiveTable` with a `primary` column; it becomes a card per row. Migrated: members, roles-by-location, locations, models, seat allowances, sync history, scheduled deliveries, invoices, uploads, webhook deliveries, the forecast day-by-day table (`DemandForecast`) and the policy comparison (`PolicyLab`). Deliberately **not** migrated, because a card is worse there: the role-permission matrix (`AccessControl` Roles), the breakdown numeric tables, and the editable grids in `ingestion/*`.

## Accessibility contracts

- **Focus.** `Modal` traps Tab inside the dialog, closes on Escape, and returns focus to the control that opened it. A clickable (`onRowClick`) row is focusable and activates on Enter or Space — a pointer-only row is not acceptable.
- **Contrast.** Light-mode copy never uses `text-slate-400` (`#94a3b8`, ~2.6:1 on white, below WCAG AA); use `text-slate-500` (`#64748b`, ~4.6:1). `dark:text-slate-400` is fine (~6:1 on slate-900) and is left alone. `__tests__/contrast.test.ts` scans `src/` and fails on a regression.
- **Motion.** `prefers-reduced-motion: reduce` neutralises the CSS animations and transitions (`animate-spin`, `animate-pulse`, the modal `fadeIn`) in one place in `index.css`; framer-motion surfaces branch on `useReducedMotion`. A loader must stay legible without motion — the skeleton keeps its shape and its `role="status"` name.
- **Names for icon-only controls.** An icon alone is not a name: `lucide-react` marks its SVGs `aria-hidden`, so the button needs its own `aria-label` (`View ${name}`, `Archive ${name}`) — three bare icons in a row otherwise announce as "button, button, button" with no idea what they act on.
- **Dark theme.** Not a `dark:` variant per element: `index.css` carries one `.dark` remap layer for the neutral scale, brand scale, semantic accent tints/inks, hover counterparts and the recharts surfaces. A new colour class is only themed once the layer mentions it — `__tests__/darkTheme.test.ts` enforces that.
- **Chart axes.** Not a primitive, but a contract: y-axis ceilings come from `lib/chartScale.ts` (`demandAxisScale`) and tick labels from `formatDemandTick`, so ticks speak in `K` only at ≥ 1000. A pinned `domain={[0, 20000]}` or a hand-rolled `/ 1000` fails `__tests__/chartScale.test.ts`. See DESIGN.md § 16.
- **Charts are shared, and a count is not a demand.** Reach for `CountChart` (grouped counts) and `MixDonut` (a few named parts) before writing a chart in a page: they are scanned by `__tests__/chartScale.test.ts`, which requires every axis in `components/` to use `formatCountTick`/`formatDemandTick` and forbids a hardcoded `domain={[0, …]}`. Keep axis labels short — recharts wraps a long category label onto a second line rather than dropping it, so `Not bound to a restaurant` renders as two lines inside a 110px axis and is still readable, but five long labels on an upright x-axis are not: at 320px the access console's `Administrator` and `Analyst` were measured overlapping, and so were the fleet's `1–24 hours` and `1–7 days` — which is why both are drawn with `layout="bars"`. Three measured rules follow from that: a horizontal chart is sized **34px per row** (a wrapped label is 25px tall, so seven rows in a fixed 210px box left a 30px band and consecutive labels met at a **0px gap**); `labelWidth` widens the category band past its 110px default, because a label wider than the band is drawn from the band's right edge leftwards and pokes out of the chart's `<svg>`, which clips; and **never give a chart a negative left margin** — the `left: -20` idiom shifts the axis band outside that same clipped svg, and `CountChart`'s y-axis digits were measured starting at `x = -5` in user units. Every cartesian chart also **titles both axes** via `components/ui/axisLabel.ts` (`leftAxisLabel` / `bottomAxisLabel`): tick numbers alone (`0 35 70 105`) never said whether the axis counted visitors, days or issues. A rotated title sits at the axis band's own left edge, so a chart using `leftAxisLabel` needs a few px of left margin or the title is clipped by the same `overflow: hidden`; `__tests__/chartScale.test.ts` fails a chart that draws an axis without one of the two helpers.
- **Action and segment padding.** Panel-header primary actions and segmented controls carry `py-2.5 … sm:py-1.5`, so a phone gets a ~38px target while desktop density is unchanged. Keep that pairing when adding a new action button.
- **Tap targets.** Interactive controls need ~44px of touch height on a phone. `Toggle` does this with padding + a matching negative margin (no layout shift); small text links and icon-only buttons use the `.tap-expand` utility, which grows the hit area with a transparent pseudo-element.
- **`Toggle` hands over the value to move *to*.** `onChange(next)` is called with `!checked`, so no call site inverts it. A handler that *writes* what it is given must therefore never be handed the value the switch already shows: the Quality Monitor did exactly that (`onToggle={() => toggleRule(rule.id, rule.enabled)}`), so every click re-sent the state it was in and the switch flicked back. `__tests__/qualityRuleToggle.test.tsx` pins the request body now. Two handlers with opposite contracts must not share a name — `Alerts`' `toggleRule(id, current)` flips internally, so the Quality Monitor's is `setRuleEnabled(id, next)`.
- **Truncation.** Anything that can be long (a name, a generated id) gets `truncate` plus a `title`, never an unlabelled clip.
- **Forms are dialogs, and long forms scan by group.** Use `FormModal`, never `Modal` plus a hand-rolled footer: it is what gives the dialog a real `<form>` (so Enter submits from any field), the shared Cancel/Save order, and the `Saving…` state. Past ~6 fields, group them under `text-overline` eyebrows with hairline dividers — the Location Registry's four sections (`Identity / Trading / Contact / Notes`) are the reference. A footer hint between Cancel and Save (`mr-auto`) is the place for what the pending change will do.
- **A long form that is the *first* thing a workspace does gets a route, not a dialog.** Adding a location is `/admin-locations-new` (declared in `data/navItems.ts` as a sub-route of `admin-locations`): the address survives a reload, Back leaves the form, and the sidebar keeps its place instead of the app being covered by an overlay. Editing an existing record stays a dialog — that is a correction, not a destination. Both surfaces render the same field component (`components/locations/LocationFields.tsx`), so the route cannot ask for a different set of fields than the dialog.
- **`InlineEdit` is for a correction, not for the record.** It turns a value into its own control — click the value, type, Enter or the tick saves, Escape or clicking away cancels. Use it where the field changes for reasons unrelated to the rest of the record (a manager's name, a phone number, a label): sending the operator into an eleven-field dialog for a two-second edit loses their place in the list. Anything that changes what the record *is* stays in the dialog.
  - **Blur cancels; it does not save.** Saving on blur *and* offering a tick is a double-write waiting to happen (the tick's own blur fires first). The control checks `relatedTarget` so moving focus onto its own buttons is not "leaving", and unchanged text sends no request at all.
  - **A failed write reverts the value and rethrows.** The parent owns the message (a toast); the control only guarantees that what it shows is what the server holds.
  - **The parent should render the value from the server's response, not from what was typed.** `PATCH /locations` ignores a `null` for any field, so a "cleared" write may not have been stored; `LocationRegistry.patchField` merges the read model the API returns and only falls back to the typed value when the response carries none. `__tests__/adminDataFields.test.tsx` pins that (`shows what the server stored, not what was typed`).
  - **The pencil is not hover-only.** `opacity-60`, not `opacity-0 group-hover:opacity-100` — there is no hover on a phone, where a hidden pencil makes a correctable field look like fixed text.
  - **The accessible name carries the value it replaces** (`Edit phone for Kimironko Kitchen — currently +250 788 111 222`), and the buttons are named `Save …` / `Cancel editing …`, so the icon-only controls are not three anonymous buttons in a row.
- **A missing value is not a default.** `forecastable: null` is not `true`, `open_days_mask: null` is not "every day", and an unknown history draws **no** progress bar — an empty bar reads as zero, which is a different and worse claim than "unknown". Where the API did not answer, the screen says so; `__tests__/adminDataFields.test.tsx` covers this on the location registry.
- **A day is the workspace's day, not UTC's.** "Today" comes from `lib/workspaceTime.ts` (`todayISO`/`addDaysISO`/`isoDaysAgo`), which reads the calendar day in `Africa/Kigali` — `workspaces.timezone`'s default. `new Date().toISOString().slice(0, 10)` answers with the **UTC** day, and at 23:30 UTC that is already the next Kigali day: the forecast target day and the demand-entry `max` were each a day out for two hours every night, and for a viewer west of UTC the app's "today" was their tomorrow. `__tests__/workspaceTime.test.ts` pins the boundary. Screens that *decide* a date use the helper; formatting a date-only `YYYY-MM-DD` for display stays at UTC or the value's own zone.
- **Viewport.** The shell uses `.app-viewport` (`100dvh`), not `h-screen`: `100vh` includes mobile browser chrome and hides the bottom nav behind it.
- **A scroll container is also a containing block.** `ResponsiveTable`'s scroll box carries `relative` because its `sr-only` `<caption>` is `position: absolute`. With a `static` box the caption resolved against the initial containing block, which is *outside* the shell's `#root { overflow: hidden }` clip, so it escaped and stretched the document — on `/forecast` the whole UI scrolled 2342px into blank space (`html` scrollHeight 3242px against a 900px viewport), and `/analytics` by 45px. `overflow: hidden` only clips a descendant whose containing block is inside the clipper, so any new `sr-only` / visually-hidden markup needs a positioned ancestor between it and `#root`.

Utilities live in `src/index.css`: `.app-viewport`, `.safe-bottom`, `.safe-bottom-offset`, `.touch-scroll`, `.scroll-fade-x`, `.tap-expand`, and the `.responsive-cards` table mode.

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [ML Contracts](../../../../model/README.md)
- [Feature Spec](../../../../model/FEATURE_SPEC.md)
- [Proposal](../../../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
