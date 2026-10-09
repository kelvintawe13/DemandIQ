# DemandIQ — UI Design

Design process, wireframes and style guide for the React front end.
Referenced from the [root README](../README.md).

Every token, breakpoint and component path below is real — taken from
`frontend/tailwind.config.js` and the components themselves, not aspirational.

---

## 1. The problem the interface solves

The app is read by an operator making a preparation decision, often on a phone,
often under time pressure. That drove every choice below.

| Constraint | Design response |
| --- | --- |
| Decision, not exploration | One forecast surface per location. Every card answers "do I need to prepare more or less?" |
| Numbers need trust | Every point forecast ships with its interval and its drivers, on the same screen |
| Operators may not trust ML | "Why this number" is first-class, not a hidden debug view |
| Mixed literacy | Plain language beside the statistic — "likely 18–32 customers", not `σ=4.1` |
| Often on a phone | Bottom navigation and a bottom-sheet nav on small screens |
| Model can be wrong | Drift and data-quality state are visible, including when they are bad |

---

## 2. Information architecture

```
Sign in ──► Overview ──┬──► Forecast ──┬──► Demand distribution
                       │               ├──► Weekday profile
                       ├──► Analytics ─┴──► Forecast by day
                       ├──► Ingestion ─────► Upload ──► Quality issues
                       ├──► Locations ─────► Menu groups
                       ├──► Devices
                       ├──► Reports
                       ├──► Alerts
                       └──► Settings ─────┬── Profile
                                        ├── Security (MFA, sessions)
                                        ├── Notifications
                                        └── Account deletion

Assistant rail (right) — available from every page
```

Navigation groups by **task**, not by table. An operator thinks "check tomorrow",
not "query `forecasts`".

---

## 3. Wireframes

### 3.1 Desktop — 1280px and up

```
┌───────────────────────────────────────────────────────────────────────────┐
│ DemandIQ          Forecast · Acacia Bistro                     [Run] [⚙]  │  PageHeader
├────────────┬──────────────────────────────────────────┬───────────────────┤
│            │  ┌────────────────────────────────────┐  │  ASSISTANT        │
│  SIDEBAR   │  │ Acacia Bistro · Tue 14 Mar          │  │                   │
│            │  │                                    │  │  "Why is Tuesday   │
│ ▸ Overview │  │        ╭──────────────╮            │  │   high here?"      │
│ ▸ Forecast │  │        │     24       │            │  │                   │
│ ▸ Analytics│  │        │  likely 18–32 │            │  │  ▸ dow_mean_8w     │
│ ▸ Ingestion│  │        ╰──────────────╯            │  │  ▸ roll_mean_7     │
│ ▸ Locations│  │        visitors, best estimate      │  │  ▸ rsv_visitors    │
│ ▸ Devices  │  └────────────────────────────────────┘  │                   │
│ ▸ Reports  │  ┌──────────────┐ ┌──────────────┐      │  [Ask about this   │
│ ▸ Alerts   │  │ Prepare 26   │ │ Surplus 2    │      │   forecast]        │
│ ▸ Settings │  │ Unmet  0.4%  │ │ Unmet days 8 │      │                   │
│            │  └──────────────┘ └──────────────┘      │                   │
│ 236px      │  ┌────────────────────────────────────┐  │  320px             │
│ (72px      │  │ Demand by day  ▁▂▃▅▇▆▄▂   Recharts │  │  (overlay <xl)     │
│  collapsed)│  │        ░░░ 90% interval band       │  │                   │
│            │  └────────────────────────────────────┘  │                   │
├────────────┴──────────────────────────────────────────┴───────────────────┤
│ feature set fs-2026.09.2 · seed 42                    v1.0.0 · proprietary  │  PageFooter
└───────────────────────────────────────────────────────────────────────────┘
```

Layout is CSS Grid by way of flex: sidebar (`shrink-0`), content (`flex-1
min-w-0`), assistant rail. `min-w-0` on the middle column is what stops a wide
Recharts canvas from forcing horizontal scroll on the whole app.

### 3.2 Mobile — below 768px

```
┌──────────────────────┐
│ ☰  Forecast      [⚙] │  header
├──────────────────────┤
│ Acacia Bistro        │
│                      │
│        ╭──────────╮  │
│        │    24    │  │  point estimate
│        │ 18–32    │  │  + interval, always together
│        ╰──────────╯  │
│                      │
│ ┌──────────────────┐ │
│ │ Prepare  26      │ │  the decision, above the chart
│ │ Unmet   0.4%     │ │
│ └──────────────────┘ │
│                      │
│ ┌──────────────────┐ │
│ │ Demand by day    │ │
│ │  ▁▂▃▅▇▆▄▂        │ │  horizontal scroll inside the card
│ └──────────────────┘ │
│                      │
├──────────────────────┤
│  ⌂    ▤    ⚑    ⚙   │  MobileNav — fixed, 60px, safe-area padded
│ Home  Data Alerts Set│
└──────────────────────┘

Nav opened → bottom sheet:
┌──────────────────────┐
│           ───        │  drag handle
│  Overview            │
│  Forecast        (2) │  unread badge
│  Ingestion           │
│  …                   │
└──────────────────────┘
  max-h 70vh, scrolls
```

**Design decision:** the sidebar is not collapsed-and-hidden on mobile. It becomes
a bottom sheet, because an operator opening the app one-handed cannot reach a
side drawer.

### 3.3 Modal contract

```
┌──────────────────────────────────────────┐
│  Title                          [ × ]    │
│  ────────────────────────────────────────│
│                                          │
│  Body — focus is trapped inside this      │
│  panel; Tab cycles, Shift+Tab reverses,  │
│  Escape closes. Background does not      │
│  scroll.                                 │
│                                          │
│  ────────────────────────────────────────│
│                    [Cancel]  [Confirm]    │
└──────────────────────────────────────────┘
```

Every modal — including the legal reader and the welcome sheet, which own their
own layout — inherits this behaviour from `useDialogBehaviour`
(`frontend/src/components/ui/useDialogBehaviour.ts`). Two surfaces previously
re-implemented it partially: the legal reader had Escape and the scroll lock but
no focus trap, and the welcome sheet declared `aria-modal` with none of it.

---

## 4. Style guide

### 4.1 Colour

Two palettes in `tailwind/tailwind.config.js`, plus a blue scale reserved for
data visualisation.

| Token | Role | Default |
| --- | --- | --- |
| `brand-500` | Primary action | slate `#64748b` |
| `brand-700` | Primary hover / dark surface | `#334155` |
| `green-500` | Positive delta, "prepared enough" | `#17a294` |
| `blue-500` | Forecast series | `#3b82f6` |
| `red-*` (`#C44E52`) | Waste, deficit, risk | — |

The default theme is **deliberately neutral grey**. DemandIQ is a decision tool,
not a marketing page: a brand colour that reads as "good" would pre-judge the
forecast before the operator has seen the interval. Green and red are reserved
for the *delta*, never for chrome.

Dark mode is class-based (`darkMode: 'class'`), defaulting per migration `018`.

### 4.2 Type scale

Nine steps, defined once so no screen invents a size:

| Token | Size / line-height | Used for |
| --- | --- | --- |
| `nano` | 9.5 / 1.4 | chart axis ticks |
| `fine` | 11 / 1.45 | badges, table meta |
| `overline` | 11 / 1.4, `0.16em` tracking | section labels |
| `micro` | 11.5 / 1.45 | button labels |
| `caption` | 12 / 1.45 | secondary text |
| `meta` | 12.5 / 1.45 | card metadata |
| `body-xs` | 13 / 1.5 | dense table cells |
| `body-sm` | 13.5 / 1.55 | default body |
| `control` | 14 / 1.5 | inputs |
| `body-sm-lg` | 14.5 / 1.5 | lead paragraphs |

### 4.3 Space, radius, elevation

- Spacing: Tailwind default 4px scale. Card padding is `p-4` desktop, `p-3` mobile.
- Radius: `rounded-lg` (8px) for controls, `rounded-2xl` (16px) for the mobile
  sheet and modal panels, `rounded-full` for badges only.
- Elevation: two tokens, both deliberately soft —
  `shadow-lift` (`0 1px 0 …, 0 12px 32px -12px …`) for cards,
  `shadow-lift-lg` for modals and the assistant drawer. No third level.

### 4.4 Motion

| Rule | Implementation |
| --- | --- |
| Only the content region animates on navigation | `motion.div` keyed on `activeNavId`, 0.2s, `[0.23, 1, 0.32, 1]` |
| Respect reduced-motion | `useReducedMotion()` → `initial={false}` |
| No layout animation on the shell | Sidebar/header stay mounted so navigation never reflows |

Motion is used to explain *where* content went, never to decorate.

### 4.5 Accessibility

- Focus ring on every interactive element: `focus-visible:ring-2 ring-brand-500`
  (+ `focus-visible:ring-offset-2` on filled buttons).
- Dialog contract: focus trap, focus restore to trigger, Escape, scroll lock.
- Status is never colour-only — deltas carry an icon or a sign as well.
- Charts ship a table equivalent or an accessible label.

---

## 5. Breakpoints and responsive strategy

| Breakpoint | Navigation | Assistant | Grid |
| --- | --- | --- | --- |
| `< 768px` | bottom bar + bottom sheet | full-screen overlay | 1 column, cards stack |
| `768–1279px` | fixed sidebar, 236px (72px collapsed) | overlay drawer | 2 columns |
| `≥ 1280px` (`xl`) | fixed sidebar | docked third column, 320px | 3 columns |

Mobile detection is CSS (`md:hidden` / `hidden md:flex`), not a JS media-query
hook, so there is no hydration mismatch and no layout flash.

---

## 6. Screenshots

| | |
| --- | --- |
| ![Sign in](screenshots/signin.png) | ![Sign up](screenshots/signup.png) |
| ![Overview](screenshots/overview.png) | ![Forecast](screenshots/forecast.png) |
| ![Analytics](screenshots/analytics.png) | ![Ingestion](screenshots/ingestion.png) |
| ![Reports](screenshots/reports.png) | ![Alerts](screenshots/alerts.png) |
| ![Settings](screenshots/settings.png) | |

Source: `docs/screenshots/`.

---

## 7. Where the design went, and what came next

Decisions worth defending in a review:

1. **Interval beside the point estimate, never below the fold.** The single most
   common way a forecasting tool misleads is showing a number and burying its
   uncertainty. Both are on the same card by construction.
2. **"Why this number" on the same screen as the number.** Explanations are a
   panel, not a separate explainability product, because the operator's question
   arrives while they are looking at the forecast.
3. **Drift and data-quality states are designed, not afterthoughts.** A model
   that has stopped being trustworthy has to say so in the layout; there is a
   state for it.
4. **Neutral default palette.** See §4.1.
5. **Bottom sheet rather than hidden sidebar on mobile** — see §3.2.

Known gaps, honestly stated:

- No Figma source file is committed. The wireframes in §3 are ASCII and were
  produced alongside the components; they are not the original design artefacts.
- Recharts is the only charting dependency, so there is no shared chart theme
  object — series colours are currently passed per chart.
- The design system is expressed entirely as Tailwind tokens in one config file.
  It is not extracted into a distributable package.