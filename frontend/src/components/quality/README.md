# frontend/src/components/quality

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Data-quality visualisation for the validation rules in Ch 3.6 (ingestion &
normalisation) and the freshness/completeness signals that gate a forecast. Every
panel reads the issue list and rule list the Quality Monitor already loads, so a
chart can never disagree with the table underneath it.

## File Map
| File | Role |
| --- | --- |
| `SeverityDonut.tsx` | How open issues divide across critical / warning / info, with the counts and shares in a legend so the numbers are readable without hovering. A thin wrapper over `ui/MixDonut` — the severity palette is the only thing that belongs to this folder |

The count chart this folder used to own is now `components/ui/CountChart.tsx`: the fleet console draws device counts with the same primitive, and a chart that two pages render belongs with the primitives, not inside one page's folder.

## Key Contracts

- **Counts, not demand.** These axes measure a number of issues, so they format
  ticks with `formatCountTick` (`lib/chartScale`). `formatDemandTick` would label
  a count of five as `5K`; `__tests__/chartScale.test.ts` enforces the
  distinction.
- **Names, never ids.** `GET /api/v1/quality/issues` returns `rule_id` and
  `location_id` and nothing else — there is no `rule_name` on the wire. The page
  resolves both through the rule and location lists it already loads
  (`checkNameOf` in `pages/QualityMonitor.tsx`). Falling back to the id itself
  puts a UUID on an axis, which is the same defect as printing a machine token
  anywhere else on screen.
- **A missing name is admitted, not guessed.** When a location cannot be
  resolved (it belongs to another workspace, so `/locations` rightly omits it)
  the chart labels it `location <short id>`. That is deliberate: an honest
  placeholder beats a wrong name.

## The issues list

Capped with `maxHeight="max-h-[380px]"` on `ResponsiveTable` — an internal scroll
with a pinned header — for one measured reason: eight rows of two lines ran to
~1284px, so the panel was mostly one list. Each row was **156px** because the
action cell was `flex-wrap`: the column's widest content became one button (95px)
and the three buttons stacked over three lines. `whitespace-nowrap` brings a row
to **57px**.

The row actions keep their words (`Apply fix` / `Dismiss` / `Details`). They were
icon-only for a while to fit the ~404px this panel gets with the assistant rail
docked; the labels cost ~254px, so the row scrolls sideways at that width rather
than the column being pinned — a pinned column that wide would cover the issue it
acts on. Close the rail (or collapse the sidebar) and the whole row fits.

## Validation rules

Each rule is a card, not a table row: what it is, the dimension it feeds, what it
looks for, when it runs, its state, and how many open issues it is currently
raising. The grid is the point — the four-column table it replaced repeated the
same coverage in every row and left the one cell that varied (the switch) looking
like a settings screen.

- **The cadence comes from `RULE_META`, keyed by `rule.name`.** The API sends
  `name`; the old table resolved the rule by `rule_key`, which is never on the
  wire, so every row claimed `On import` — including the connector check that runs
  hourly and the upload check that runs on upload. The card states the real one.
- **Findings are attributed, not guessed.** `findingsByRule` groups the open
  issues by the rule name each one resolves to, so a check that is on but silent
  (`Nothing open`) reads differently from one that is on and catching things.
- **A paused rule looks paused**: dashed border, neutral icon tile, `Paused`
  instead of `On`.
- **The switch keeps the machine token** (`gap_detection`) as its accessible name
  while every visible string is humanised — the contract `RULE_META` and
  `__tests__/dataClerkScreens.test.tsx` key off.
- **The switch writes forward, never backward.** `Toggle` reports the value to
  move *to*, and `setRuleEnabled(id, next)` writes exactly that. It used to be
  handed `rule.enabled` — the state the switch was already showing — so every
  click PATCHed the value it already held and the rule never changed at all.
  `__tests__/qualityRuleToggle.test.tsx` asserts the request body and the
  revert-on-failure path, so the wiring cannot silently regress again.
- **The dimension reads `Completeness check`, never bare `Completeness`**: that
  word already belongs to the dimension analysis panel above, and two identical
  strings on one screen make both harder to point at.

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [Proposal](../../../../project proposal.md)
