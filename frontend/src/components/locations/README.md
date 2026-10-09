# frontend/src/components/locations

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
The site record (Ch 3.2 — location registry and onboarding). A location's name,
district, capacity and trading pattern are the keys every demand series is
grouped by, and the seven-day history bar they start against is what the
forecast pipeline reads. Adding a site and editing one ask for the same eleven
fields, so both render them from here.

## File Map
| File | Role |
| --- | --- |
| `LocationFields.tsx` | The four field groups — Identity, Trading, Contact, Notes — plus `FormSection`, the eyebrow + rule wrapper they sit in. Rendered by the Add location route and by the registry's edit dialog |

## Key Contracts

- **One field list, two surfaces.** `pages/admin/AddLocation.tsx` (the route) and
  `pages/admin/LocationRegistry.tsx` (the edit dialog) both render
  `LocationFields` with the same `LocationForm` draft. Adding cannot ask for a
  field that editing does not, or vice versa — the only difference between the
  surfaces is what submitting does.
- **The rules that read the payload live in `lib/locations.ts`**, not in either
  screen: `NEEDED_HISTORY_DAYS` (7 — `lag_7` is a model feature, so it is a
  modelling fact rather than a preference), `forecastState` (a tri-state, because
  `forecastable: null` is not `true`), `openDayNames`/`maskRecorded` (a missing
  `open_days_mask` is not "open every day"), `locationPayload` and `formFromRow`.
  A screen that re-derives them is how one page says "forecasting" while another
  says "not scored".
- **`LocationForm` holds strings.** `seating_capacity` is a `string` in the form
  and is parsed once, in `locationPayload`, so an empty input means "not set"
  rather than `NaN`.
- **The add flow is a route, not a dialog.** `/admin-locations-new` is declared in
  `data/navItems.ts` (`SUB_ROUTE_PARENT`) and `data/pages.ts`; the registry hands
  off with `onNavigate('admin-locations-new')`. A sub-route needs both entries —
  the shell resolves its header from `pageMeta[activeNavId]`, so a missing one
  titles the page "Overview".
- **Fields are grouped past ~6.** The eyebrow (`text-overline`) names the group
  and a hairline rule separates it from the one above; `__tests__/addLocationRoute.test.tsx`
  asserts the four groups on the route and `__tests__/adminDataFields.test.tsx`
  asserts them in the dialog, so the two cannot drift.
- **Open days are a bitmask with an accessible name.** The weekday pills carry
  `aria-pressed` and `${Monday}: open` — open/closed was previously conveyed by
  fill colour alone. The mask itself is shown as `<code>` underneath, since it is
  what the API stores.

## Related Docs
- [UI primitives](../ui/README.md) — `Field`, `FormModal`, `InlineEdit`
- [Admin features](../../pages/admin/ADMIN_FEATURES.md)
- [API Contract](../../../../../backend/API_ENDPOINTS.md)
